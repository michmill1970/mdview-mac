"""``mdview://`` scheme handler and interceptor as a pure contract test.

No live browser: the handler is driven with a duck-typed
``QWebEngineUrlRequestJob`` that records ``reply``/``fail``, so the whole URL
map -- including the directory-escape guards and the ``%2F`` doc-path format
-- is checked headlessly and fast.  (``test_webengine_smoke`` exercises the
same code inside real Chromium, including relative image loading.)
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob
from PySide6.QtWidgets import QApplication

from mdview.core.paths import AppDir, app_dir
from mdview.render.assets import (
    SCHEME_HOST_ASSETS,
    SCHEME_HOST_DOC,
    SCHEME_HOST_PAGE,
    SCHEME_HOST_STATIC,
)
from mdview.viewer.scheme import MdViewSchemeHandler, OfflineInterceptor, PageStore

pytestmark = pytest.mark.gui


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    assert app is not None, "session qt_application fixture did not run"
    return app


@pytest.fixture(autouse=True)
def _clean_pages() -> object:
    PageStore.clear()
    yield
    PageStore.clear()


class FakeJob(QObject):
    """Records what the handler decided; ``reply`` captures the body."""

    def __init__(self, url: QUrl) -> None:
        super().__init__()
        self._url = url
        self.replied: tuple[bytes, bytes] | None = None
        self.headers: object | None = None
        self.error: QWebEngineUrlRequestJob.Error | None = None

    def requestUrl(self) -> QUrl:
        return self._url

    def setAdditionalResponseHeaders(self, headers: object) -> None:
        self.headers = headers

    def reply(self, content_type: bytes, device: object) -> None:
        assert self.headers is not None, "replies must carry CORS headers"
        data = device.readAll()  # type: ignore[attr-defined]
        self.replied = (bytes(content_type), bytes(data.data()))

    def fail(self, error: QWebEngineUrlRequestJob.Error) -> None:
        self.error = error


class FakeInfo:
    def __init__(self, url: str) -> None:
        self._url = QUrl(url)
        self.blocked = False

    def requestUrl(self) -> QUrl:
        return self._url

    def block(self, blocked: bool) -> None:
        self.blocked = blocked


def run(handler: MdViewSchemeHandler, url: str) -> FakeJob:
    job = FakeJob(QUrl(url))
    handler.requestStarted(job)
    return job


class TestPageStore:
    def test_put_get_drop(self) -> None:
        url = PageStore.put("p1", "<h1>hi</h1>")
        assert url.toString() == f"mdview://{SCHEME_HOST_PAGE}/p1"
        assert PageStore.get("p1") == "<h1>hi</h1>"
        PageStore.drop("p1")
        assert PageStore.get("p1") is None

    def test_overwrite_and_clear(self) -> None:
        PageStore.put("p2", "one")
        PageStore.put("p2", "two")
        PageStore.clear()
        assert PageStore.get("p2") is None


class TestPageAndStaticHosts:
    def test_page_hit(self, qapp: QApplication) -> None:
        PageStore.put("tab-1", "<p>page</p>")
        job = run(MdViewSchemeHandler(), f"mdview://{SCHEME_HOST_PAGE}/tab-1")
        assert job.error is None
        assert job.replied is not None
        assert b"text/html" in job.replied[0]
        assert job.replied[1] == b"<p>page</p>"

    def test_page_miss(self, qapp: QApplication) -> None:
        job = run(MdViewSchemeHandler(), f"mdview://{SCHEME_HOST_PAGE}/nope")
        assert job.error == QWebEngineUrlRequestJob.Error.UrlNotFound

    def test_static_css_is_the_real_package_file(self, qapp: QApplication) -> None:
        job = run(MdViewSchemeHandler(), f"mdview://{SCHEME_HOST_STATIC}/mdview.css")
        assert job.replied is not None
        assert b"text/css" in job.replied[0]
        assert b"--mdv" in job.replied[1]

    def test_static_missing_file(self, qapp: QApplication) -> None:
        job = run(MdViewSchemeHandler(), f"mdview://{SCHEME_HOST_STATIC}/nope.css")
        assert job.error == QWebEngineUrlRequestJob.Error.UrlNotFound

    def test_qurl_keeps_dot_segments(self, qapp: QApplication) -> None:
        # Qt6 QUrl does NOT normalise ``..`` away, which is exactly why the
        # resolve()+is_relative_to() containment in _reply_file matters.
        url = QUrl(f"mdview://{SCHEME_HOST_STATIC}/../../pyproject.toml")
        assert ".." in url.path()

    def test_static_host_cannot_escape_root(self, qapp: QApplication, tmp_path: Path) -> None:
        (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
        handler = MdViewSchemeHandler()
        handler._static_root = tmp_path
        job = run(handler, f"mdview://{SCHEME_HOST_STATIC}/sub/../../secret.txt")
        assert job.error == QWebEngineUrlRequestJob.Error.UrlNotFound

    def test_vendor_assets_served(self, qapp: QApplication, seeded_vendor: Path) -> None:
        job = run(MdViewSchemeHandler(), f"mdview://{SCHEME_HOST_ASSETS}/katex/katex.min.js")
        assert job.replied is not None
        assert b"javascript" in job.replied[0]
        assert len(job.replied[1]) > 10_000  # the real KaTeX bundle

    def test_vendor_escape_refused(self, qapp: QApplication, seeded_vendor: Path) -> None:
        vendor = app_dir(AppDir.VENDOR)
        (vendor.parent / "sibling.txt").write_text("no", encoding="utf-8")
        handler = MdViewSchemeHandler()
        job = run(handler, f"mdview://{SCHEME_HOST_ASSETS}/../sibling.txt")
        assert job.error == QWebEngineUrlRequestJob.Error.UrlNotFound

    def test_unknown_host_denied(self, qapp: QApplication) -> None:
        job = run(MdViewSchemeHandler(), "mdview://other/thing")
        assert job.error == QWebEngineUrlRequestJob.Error.RequestDenied


class TestDocumentHost:
    @staticmethod
    def _doc_url(directory: Path, relative: str) -> str:
        return f"mdview://{SCHEME_HOST_DOC}/{quote(str(directory), safe='')}/{relative}"

    def test_file_inside_root_served(self, qapp: QApplication, tmp_path: Path) -> None:
        root = tmp_path / "docdir"
        root.mkdir()
        (root / "img.png").write_bytes(b"\x89PNG fake")
        job = run(MdViewSchemeHandler(document_roots=[root]), self._doc_url(root, "img.png"))
        assert job.replied is not None
        assert job.replied[1] == b"\x89PNG fake"
        assert b"png" in job.replied[0]

    def test_file_in_subdirectory(self, qapp: QApplication, tmp_path: Path) -> None:
        root = tmp_path / "docdir"
        (root / "assets").mkdir(parents=True)
        (root / "assets" / "a.txt").write_text("nested", encoding="utf-8")
        job = run(MdViewSchemeHandler(document_roots=[root]), self._doc_url(root, "assets/a.txt"))
        assert job.replied is not None
        assert job.replied[1] == b"nested"

    def test_unregistered_directory_denied(self, qapp: QApplication, tmp_path: Path) -> None:
        root = tmp_path / "elsewhere"
        root.mkdir()
        (root / "secret.png").write_bytes(b"secret")
        job = run(MdViewSchemeHandler(), self._doc_url(root, "secret.png"))
        assert job.error == QWebEngineUrlRequestJob.Error.RequestDenied

    def test_parent_escape_denied(self, qapp: QApplication, tmp_path: Path) -> None:
        root = tmp_path / "docdir2"
        root.mkdir()
        (tmp_path / "outside.png").write_bytes(b"outside")
        handler = MdViewSchemeHandler(document_roots=[root.resolve()])
        job = run(handler, self._doc_url(root, "../outside.png"))
        assert job.replied is None
        assert job.error in {
            QWebEngineUrlRequestJob.Error.UrlNotFound,
            QWebEngineUrlRequestJob.Error.RequestDenied,
        }

    def test_spaces_in_names_survive_encoding(self, qapp: QApplication, tmp_path: Path) -> None:
        root = tmp_path / "my docs"
        root.mkdir()
        (root / "pic one.png").write_bytes(b"p1c")
        job = run(
            MdViewSchemeHandler(document_roots=[root.resolve()]), self._doc_url(root, "pic one.png")
        )
        assert job.replied is not None
        assert job.replied[1] == b"p1c"

    def test_symlinked_document_root_serves_inside_and_refuses_escape(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        """macOS condition: the document directory may *be* a symlink.

        macOS resolves ``/tmp``/``/var`` to ``/private/...`` and users symlink
        Documents folders all the time.  Containment must be decided after
        ``resolve()`` on both sides: legitimate files still load, while parent
        traversal and symlinks that point out of the root stay refused.
        """
        real = tmp_path / "real-docs"
        real.mkdir()
        (real / "img.png").write_bytes(b"png-bytes")
        (tmp_path / "secret.png").write_bytes(b"secret")
        link = tmp_path / "docs-link"
        link.symlink_to(real, target_is_directory=True)

        handler = MdViewSchemeHandler(document_roots=[link])  # registered unresolved

        # legit file reached through the symlinked directory: served
        job = run(handler, self._doc_url(link, "img.png"))
        assert job.replied is not None
        assert job.replied[1] == b"png-bytes"

        # parent-directory traversal through the symlink: still refused
        job = run(handler, self._doc_url(link, "../secret.png"))
        assert job.replied is None
        assert job.error in {
            QWebEngineUrlRequestJob.Error.UrlNotFound,
            QWebEngineUrlRequestJob.Error.RequestDenied,
        }

        # a symlink *inside* the root that escapes it: refused (containment
        # applies to the resolved target, not the lexical path)
        (real / "trap.png").symlink_to(tmp_path / "secret.png")
        job = run(handler, self._doc_url(link, "trap.png"))
        assert job.replied is None
        assert job.error == QWebEngineUrlRequestJob.Error.UrlNotFound

    def test_deeper_symlinked_directory_still_denied(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        """Resolving the request root must never *widen* what is reachable.

        ``docs/sub`` is a symlink to a directory outside the registered root:
        after resolution it is not under the allow-list, so it stays denied.
        """
        root = tmp_path / "docs"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.png").write_bytes(b"secret")
        (root / "sub").symlink_to(outside, target_is_directory=True)

        handler = MdViewSchemeHandler(document_roots=[root])
        job = run(handler, self._doc_url(root / "sub", "secret.png"))
        assert job.replied is None
        assert job.error == QWebEngineUrlRequestJob.Error.RequestDenied


class TestInterceptor:
    @pytest.mark.parametrize(
        "url",
        ["https://evil.example/x.png", "http://x.example", "file:///etc/hosts", "data:text/html,x"],
    )
    def test_blocks_everything_foreign(self, url: str) -> None:
        info = FakeInfo(url)
        OfflineInterceptor().interceptRequest(info)  # type: ignore[arg-type]
        assert info.blocked

    def test_lets_mdview_through(self) -> None:
        info = FakeInfo("mdview://assets/mermaid/mermaid.min.js")
        OfflineInterceptor().interceptRequest(info)  # type: ignore[arg-type]
        assert not info.blocked
