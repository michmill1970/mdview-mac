"""Assemble the final HTML document (pure).

Data flow::

    markdown text ──render_body()──▶ body HTML ─┐
    palette       ──page_css()────▶ <style>     ├─▶ _TEMPLATE ─▶ full HTML
    assets.py     ────────────────▶ asset URLs  ┘

The result is a self-contained string that references only ``mdview://`` (or
``file://`` for browser QA) URLs, which is what lets
``tests/test_offline_contract.py`` prove that no page can reach the network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote

from mdview import __version__
from mdview.core.appearance import Appearance
from mdview.core.palette import Palette
from mdview.render.assets import (
    SCHEME_HOST_DOC,
    SCHEME_HOST_PAGE,
    asset_url,
    require_vendored,
    static_url,
)
from mdview.render.markdown import RenderedBody, render_body
from mdview.render.mermaid_theme import mermaid_theme_vars
from mdview.render.theme_css import page_css

__all__ = ["CSP", "PageOptions", "RenderedPage", "base_url_for", "render_page"]

#: Hard offline + XSS boundary.  Notes:
#: * no ``inline`` in ``script-src`` -- behaviour is app.js, configuration is a
#:   JSON block, so a Markdown document can never inject executable code;
#: * ``unsafe-eval`` is deliberately *absent*.  If a future Mermaid release
#:   needs it, adding it must be a visible, reviewed change (DESIGN.md §Security);
#: * sources are scheme-scoped (``mdview:``), not ``'self'``: the page lives on
#:   ``mdview://page`` while assets/static/doc live on other hosts of the same
#:   scheme, which are *origins* of their own.
CSP: str = (
    "default-src 'none'; "
    "script-src mdview:; "
    "style-src mdview: 'unsafe-inline'; "
    "img-src mdview: data: blob:; "
    "font-src mdview: data:; "
    "connect-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-src 'none'; "
    "object-src 'none'"
)

_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-appearance="{appearance}">
<head>
<meta charset="utf-8">
{csp_meta}
<meta name="generator" content="mdview {version}">
<title>{title}</title>
<link rel="stylesheet" href="{base_css_url}">
<link rel="stylesheet" href="{katex_css_url}">
<style id="mdview-palette-style">
{page_css}
</style>
</head>
<body class="md-body" data-math="{used_math}" data-mermaid="{used_mermaid}">
<article class="md-content" id="mdview-content">
{body}
</article>
<script id="mdview-config" type="application/json">{config}</script>
{scripts}
</body>
</html>
"""


@dataclass(frozen=True, slots=True)
class PageOptions:
    """Everything the assembler needs besides the Markdown text."""

    palette: Palette
    font_size_px: int = 16
    appearance: Appearance = "light"
    title: str = "MDView"
    #: ``mdview`` (in-app) or ``file`` (open the same page in a browser during dev).
    asset_scheme: str = "mdview"
    smooth_scrolling: bool = True
    #: Directory of the document; relative images resolve against it.
    document_dir: Path | None = None
    #: Stable id so the view can re-navigate without re-encoding the page.
    page_id: str = "preview"
    #: Skip the "vendor files exist" assertion (unit tests of the template).
    skip_asset_check: bool = False


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """A finished page plus what the view needs to know about it."""

    html: str
    base_url: str
    used_math: bool
    used_mermaid: bool
    languages: tuple[str, ...]

    def byte_size(self) -> int:
        """UTF-8 size -- useful when comparing against ``setHtml`` size limits."""
        return len(self.html.encode("utf-8"))


def render_page(markdown_source: str, options: PageOptions) -> RenderedPage:
    """Render Markdown text into a complete, offline, palette-styled page."""
    if not options.skip_asset_check:
        require_vendored()
    body: RenderedBody = render_body(markdown_source, options.palette)
    scheme = options.asset_scheme
    body_html = _absolutize_images(body.html, options, scheme)

    scripts = []
    if body.used_math:
        # KaTeX is a classic (UMD) script: loaded first, `katex` on window.
        scripts.append(
            f'<script defer src="{asset_url("katex", "katex.min.js", scheme=scheme)}"></script>'
        )
    # app.js is a module (modules are deferred by default) and dynamically
    # imports Mermaid only when the document contains a ```mermaid block.
    scripts.append(f'<script type="module" src="{static_url("app.js", scheme=scheme)}"></script>')

    config = {
        "mermaidUrl": asset_url("mermaid", "mermaid.min.js", scheme=scheme),
        "appearance": options.appearance,
        # 'base' has no built-in identity of its own; every colour comes from
        # the active palette (see render/mermaid_theme.py), so diagrams are
        # palette-coherent on light and dark alike.
        "mermaidTheme": "base",
        "mermaidThemeVariables": mermaid_theme_vars(options.palette),
        "palette": options.palette.id,
        "smoothScrolling": options.smooth_scrolling,
    }
    csp_meta = (
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        if scheme == "mdview"
        else ""  # browser QA mode: a policy naming mdview: would block file: loads
    )
    html = _TEMPLATE.format(
        appearance=options.appearance,
        csp_meta=csp_meta,
        version=__version__,
        title=escape(options.title),
        base_css_url=static_url("mdview.css", scheme=scheme),
        katex_css_url=asset_url("katex", "katex.min.css", scheme=scheme),
        page_css=page_css(
            options.palette,
            options.font_size_px,
            appearance=options.appearance,
            smooth_scrolling=options.smooth_scrolling,
        ),
        used_math="1" if body.used_math else "0",
        used_mermaid="1" if body.used_mermaid else "0",
        body=body_html,
        # `<\/` keeps a literal </script> inside the JSON from closing the tag.
        config=json.dumps(config).replace("</", "<\\/"),
        scripts="\n".join(scripts),
    )
    return RenderedPage(
        html=html,
        base_url=base_url_for(options, scheme=scheme),
        used_math=body.used_math,
        used_mermaid=body.used_mermaid,
        languages=body.languages,
    )


_IMG_SRC = re.compile(r'(<img[^>]*?\bsrc=")([^"]*)(")')
_ABSOLUTEISH = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:|^[/#?]")


def _absolutize_images(body_html: str, options: PageOptions, scheme: str) -> str:
    """Rewrite document-relative ``<img src>`` to the document's directory.

    The viewer navigates to ``mdview://page/<id>``, so relative references in
    the body would otherwise resolve against the wrong directory.  Only
    ``<img>`` tags ever reach here (raw HTML in documents is escaped), and we
    touch neither anchors (``#fragments`` must stay same-document) nor
    absolute URLs, so this stays a two-line regex instead of a parser.
    """
    if options.document_dir is None:
        return body_html
    directory = options.document_dir.resolve()
    if scheme == "file":
        base = directory.as_uri() + "/"
    else:
        quoted = quote(str(directory).replace("\\", "/"), safe="")
        base = f"{scheme}://{SCHEME_HOST_DOC}/{quoted}/"

    def rewrite(match: re.Match[str]) -> str:
        src = match.group(2)
        if _ABSOLUTEISH.match(src):
            return match.group(0)
        return match.group(1) + base + quote(src, safe="/%") + match.group(3)

    return _IMG_SRC.sub(rewrite, body_html)


def base_url_for(options: PageOptions, *, scheme: str | None = None) -> str:
    """Base URL so relative references (images!) resolve to real local files.

    ``mdview://doc/<percent-encoded dir>/`` lets the scheme handler map
    ``img.png`` to a file *inside that directory only*.  Defaults to the
    scheme the page was rendered for.
    """
    if scheme is None:
        scheme = options.asset_scheme
    if scheme == "file" and options.document_dir is not None:
        return f"{options.document_dir.resolve().as_uri()}/"
    if options.document_dir is None:
        return f"{scheme}://{SCHEME_HOST_PAGE}/{options.page_id}/"
    quoted = quote(str(options.document_dir.resolve()).replace("\\", "/"), safe="")
    return f"{scheme}://{SCHEME_HOST_DOC}/{quoted}/"
