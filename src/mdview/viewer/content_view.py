"""One content pane: a QWebEngineView showing one rendered page.

The view is intentionally dumb.  It receives finished HTML from the pure core
(:func:`mdview.render.page.render_page`), navigates to it through the
``mdview://`` scheme, and offers *live* adjustments (font size, palette CSS,
scroll offset) as small ``runJavaScript`` calls, so changing zoom never
re-renders or reloads the document.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from mdview.core.palette import Palette
from mdview.render.page import RenderedPage
from mdview.render.theme_css import page_css
from mdview.viewer.scheme import PageStore

#: The JS surface of static/app.js -- keep the two files in sync (tested).
_JS_SET_FONT_SIZE = "window.mdview.setFontSize({size})"
_JS_SET_PALETTE = "window.mdview.setPaletteCss({css})"
_JS_SCROLL_Y = "window.mdview.scrollY()"
_JS_SET_SCROLL_Y = "window.mdview.setScrollY({y})"
_JS_READY = "document.documentElement.dataset.mdviewReady === '1'"

JsCallback = Callable[[object], None]


class ContentViewPage(QWebEnginePage):
    """Page policy: nothing leaves the app except genuine web links."""

    def acceptNavigationRequest(  # noqa: N802
        self,
        url: QUrl,
        navigation_type: QWebEnginePage.NavigationType,
        is_user_initiated: bool,
    ) -> bool:
        scheme = url.scheme().lower()
        if scheme in {"mdview", "about", "data"}:
            return True
        if scheme in {"http", "https", "mailto"}:
            # Links open in the user's browser; the viewer never navigates away.
            QDesktopServices.openUrl(url)
        return False


def configure_settings(settings: QWebEngineSettings) -> None:
    """The entire web-view policy, in one auditable place (see DESIGN.md §Security)."""
    attr = QWebEngineSettings.WebAttribute
    policy: dict[QWebEngineSettings.WebAttribute, bool] = {
        attr.JavascriptEnabled: True,  # KaTeX and Mermaid need it
        attr.JavascriptCanOpenWindows: False,
        attr.JavascriptCanAccessClipboard: False,  # clipboard use is native (Edit > Copy)
        attr.LocalContentCanAccessFileUrls: False,  # our scheme replaces file:// access
        attr.LocalContentCanAccessRemoteUrls: False,
        attr.PluginsEnabled: False,
        attr.AutoLoadImages: True,  # relative images, served by mdview://doc
        attr.DnsPrefetchEnabled: False,
        attr.ErrorPageEnabled: False,
        attr.ScrollAnimatorEnabled: True,
        attr.SpatialNavigationEnabled: False,
        attr.FocusOnNavigationEnabled: False,
    }
    for attribute, enabled in policy.items():
        settings.setAttribute(attribute, enabled)


class ContentView(QWebEngineView):
    """Content pane for a single document."""

    def __init__(self, profile: QWebEngineProfile, parent=None) -> None:
        super().__init__(parent)
        self._content_page = ContentViewPage(profile, self)
        self.setPage(self._content_page)
        configure_settings(self._content_page.settings())
        self._page_id: str | None = None
        self._base_url: str | None = None
        self._load_errors: list[str] = []
        self._content_page.javaScriptConsoleMessage.connect(self._on_console_message)

    # ------------------------------------------------------------ loading --
    def show_page(self, page: RenderedPage, *, page_id: str) -> None:
        """Publish the rendered HTML on our scheme and navigate to it."""
        self._page_id = page_id
        self._base_url = page.base_url
        self.load(PageStore.put(page_id, page.html))

    def reload_page(self) -> None:
        """Re-run the page (picks up a re-rendered body after an external edit)."""
        self.reload()

    @property
    def page_id(self) -> str | None:
        return self._page_id

    @property
    def base_url(self) -> str | None:
        return self._base_url

    @property
    def load_errors(self) -> tuple[str, ...]:
        """Warnings/errors reported by the page -- shown by the smoke tests."""
        return tuple(self._load_errors)

    # ------------------------------------------------------ live controls --
    def set_font_size(self, size_px: int) -> None:
        """Zoom without reloading (preserves scroll position and selection)."""
        self._run(_JS_SET_FONT_SIZE.format(size=int(size_px)))

    def apply_palette_css(self, css: str) -> None:
        """Live palette swap: replace the generated ``:root`` + token CSS block."""
        self._run(_JS_SET_PALETTE.format(css=_js_string(css)))

    def reapply_theme(self, palette: Palette, font_size_px: int, *, appearance: str) -> None:
        """Full theme refresh after a palette or appearance change."""
        self.apply_palette_css(page_css(palette, font_size_px, appearance=appearance))

    # ---------------------------------------------------------- scrolling --
    def scroll_position(self, callback: JsCallback) -> None:
        self._run(_JS_SCROLL_Y, callback)

    def restore_scroll(self, scroll_y: int) -> None:
        if self.loadProgress() >= 100:
            self._run(_JS_SET_SCROLL_Y.format(y=int(scroll_y)))

    def is_ready(self, callback: JsCallback) -> None:
        """Whether app.js finished its synchronous work (used by the smoke tests)."""
        self._run(_JS_READY, callback)

    def run_script(self, script: str, callback: JsCallback | None = None) -> None:
        """Escape hatch used by the headless GUI tests."""
        self._run(script, callback)

    # ------------------------------------------------------------ private --
    def _run(self, script: str, callback: JsCallback | None = None) -> None:
        if callback is None:
            self._content_page.runJavaScript(script)
        else:
            self._content_page.runJavaScript(script, callback)

    def _on_console_message(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        source_id: str,
        line_number: int,
    ) -> None:
        if level in (
            QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel,
            QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel,
        ):
            self._load_errors.append(f"{message} ({source_id}:{line_number})")


def _js_string(text: str) -> str:
    """Encode a Python string as a JS string literal (JSON is a subset of JS)."""
    return json.dumps(text).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def document_dir_of(path: Path) -> Path:
    """Directory a document's relative resources resolve against."""
    return path.resolve().parent
