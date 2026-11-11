"""The ``mdview://`` URL scheme: asset delivery + the app's network boundary.

Why not ``file://``?

1. Mermaid ships as an ES module, and Chromium refuses ``file://`` module
   imports (opaque origin) -- a custom scheme registered as standard+fetchable
   fixes that without any dangerous ``--allow-file-access-from-files`` flag.
2. It gives us one chokepoint: the only things this app can ever load are
   (a) pages we generated, (b) our own static files, (c) the vendored
   KaTeX/Mermaid, (d) files inside the directory of the document being viewed
   (so relative images work). Everything else is refused by the interceptor.

URL map::

    mdview://page/<id>                     generated HTML (in-memory PageStore)
    mdview://static/mdview.css|app.js      package static files
    mdview://assets/katex/katex.min.js     vendored assets (user data dir)
    mdview://doc/<pct-encoded-dir>/<rel>   files next to the open document
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import ClassVar
from urllib.parse import unquote

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QUrl
from PySide6.QtWebEngineCore import (
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
    QWebEngineUrlRequestJob,
)

from mdview.core.paths import AppDir, app_dir, static_dir
from mdview.render.assets import SCHEME_HOST_ASSETS, SCHEME_HOST_DOC, SCHEME_HOST_PAGE, SCHEME_HOST_STATIC

SCHEME_NAME = b"mdview"

_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
}


def register_mdview_scheme() -> None:
    """Register the scheme. Must run **before** the first QWebEngineProfile exists."""
    scheme = QWebEngineUrlScheme(SCHEME_NAME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.CorsEnabled
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)


class PageStore:
    """Generated pages keyed by id (the ``page`` host of our scheme).

    Serving the HTML through the scheme instead of ``setHtml`` avoids
    ``setHtml``'s size limits and keeps the document's base URL stable, so a
    palette or font change can re-navigate without a data: URL rewrite.
    """

    _pages: ClassVar[dict[str, str]] = {}

    @classmethod
    def put(cls, page_id: str, html: str) -> QUrl:
        cls._pages[page_id] = html
        return QUrl(f"mdview://{SCHEME_HOST_PAGE}/{page_id}")

    @classmethod
    def get(cls, page_id: str) -> str | None:
        return cls._pages.get(page_id)

    @classmethod
    def drop(cls, page_id: str) -> None:
        cls._pages.pop(page_id, None)

    @classmethod
    def clear(cls) -> None:
        cls._pages.clear()


def _content_type(path: Path) -> bytes:
    guessed, _ = mimetypes.guess_type(path.name)
    return (_CONTENT_TYPES.get(path.suffix.lower()) or guessed or "application/octet-stream").encode()


def _reply(job: QWebEngineUrlRequestJob, data: bytes, content_type: bytes) -> None:
    # The buffer is parented to the job so it outlives this call and dies with
    # the request (reply() does not take ownership of the QIODevice).
    buffer = QBuffer(job)
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    # Mermaid is imported as a module from a different *host* of the same
    # scheme, so the response has to be CORS-readable.
    job.setAdditionalResponseHeaders({"Access-Control-Allow-Origin": "*"})
    job.reply(content_type, buffer)


class MdViewSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serves pages, static files, vendored assets, and document-local files."""

    def __init__(self, document_roots: list[Path] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._document_roots: list[Path] = document_roots or []
        self._vendor_root = app_dir(AppDir.VENDOR)
        self._static_root = static_dir()

    def add_document_root(self, directory: Path) -> None:
        """Allow ``mdview://doc/...`` access to a directory (the open file's dir)."""
        resolved = directory.resolve()
        if resolved not in self._document_roots:
            self._document_roots.append(resolved)

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802
        url = job.requestUrl()
        host = url.host()
        path = url.path().lstrip("/")
        try:
            if host == SCHEME_HOST_PAGE:
                html = PageStore.get(path)
                if html is None:
                    job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                    return
                _reply(job, html.encode("utf-8"), b"text/html; charset=utf-8")
            elif host == SCHEME_HOST_STATIC:
                self._reply_file(job, self._static_root / path)
            elif host == SCHEME_HOST_ASSETS:
                self._reply_file(job, self._vendor_root / path)
            elif host == SCHEME_HOST_DOC:
                self._reply_document_file(job, path)
            else:
                job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
        except OSError:
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)

    def _reply_file(self, job: QWebEngineUrlRequestJob, candidate: Path) -> None:
        if not candidate.is_file():
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        _reply(job, candidate.read_bytes(), _content_type(candidate))

    def _reply_document_file(self, job: QWebEngineUrlRequestJob, encoded_path: str) -> None:
        """``doc/<pct-encoded absolute dir>/<relative>`` -> file inside that dir only."""
        directory, _, relative = unquote(encoded_path).partition("/")
        root = Path(directory)
        if not any(root == allowed or root.is_relative_to(allowed) for allowed in self._document_roots):
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        _reply(job, target.read_bytes(), _content_type(target))


class OfflineInterceptor(QWebEngineUrlRequestInterceptor):
    """Defence in depth: drop every request that is not ours.

    The page's CSP already sets ``connect-src 'none'`` and loads nothing remote,
    but a hostile document could still try to make an <img> or CSS request to a
    remote host; this makes offline operation a guarantee rather than a hope.
    """

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802
        scheme = bytes(info.requestUrl().scheme().encode()).lower()
        if scheme != SCHEME_NAME:
            info.block(True)
