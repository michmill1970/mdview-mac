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

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

from mdview.core.appearance import Appearance
from mdview.core.palette import Palette
from mdview.render.mermaid_theme import mermaid_theme_vars
from mdview.render.page import RenderedPage
from mdview.render.theme_css import page_css
from mdview.viewer.scheme import PageStore

#: The JS surface of static/app.js -- keep the two files in sync (tested).
#: Live-control calls are *queued*, never called directly: app.js is a
#: ``type="module"`` script, so ``window.mdview`` exists only once it has
#: evaluated, while QtWebEngine runs injected JS as soon as the new document's
#: main-world context exists -- on a (re)load that window lands between
#: navigation and app.js, where a direct ``window.mdview.X(...)`` threw
#: ``Cannot read properties of undefined`` and the change was silently lost
#: (Phase 7 macOS race).  The envelope creates ``window.__mdviewPending``
#: itself, so the injection itself can never throw, and FIFO drain in app.js
#: preserves call order across the race (protocol: DESIGN.md §6 + app.js).
#: The Python side additionally retains the latest live state and replays it
#: on every load completion (:meth:`ContentView._replay_live_state`), covering
#: injections that land on the *outgoing* document and die with it.
_JS_ENQUEUE = (
    "(() => {{ const q = (window.__mdviewPending ??= []); q.push({call});"
    " window.__mdviewFlush?.(); }})()"
)
_JS_SCROLL_Y = "window.mdview?.scrollY() ?? 0"
_JS_READY = "document.documentElement.dataset.mdviewReady === '1'"
#: Cleared on every (re)navigation so a readiness poll can never observe a
#: stale ``'1'`` from the document that is about to be replaced.
_JS_RESET_READY = "document.documentElement.dataset.mdviewReady = '0'"

JsCallback = Callable[[object], None]


class ContentViewPage(QWebEnginePage):
    """Page policy: nothing leaves the app except genuine web links."""

    #: ``(level, message, line_number, source_id)`` for every console hit.
    console_message = Signal(object, str, int, str)

    def acceptNavigationRequest(
        self,
        url: QUrl | str,
        navigation_type: QWebEnginePage.NavigationType,
        is_user_initiated: bool,
    ) -> bool:
        target = url if isinstance(url, QUrl) else QUrl(url)
        scheme = target.scheme().lower()
        if scheme in {"mdview", "about", "data"}:
            return True
        if scheme in {"http", "https", "mailto"}:
            # Links open in the user's browser; the viewer never navigates away.
            QDesktopServices.openUrl(target)
        return False

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        # Qt exposes this as a virtual method, not a signal: bridge it onward.
        self.console_message.emit(level, message, line_number, source_id)


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

    def __init__(self, profile: QWebEngineProfile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content_page = ContentViewPage(profile, self)
        self.setPage(self._content_page)
        configure_settings(self._content_page.settings())
        self._page_id: str | None = None
        self._base_url: str | None = None
        self._load_errors: list[str] = []
        #: Last requested live state, ``(name, args)`` in send order, at most
        #: one entry per method (last wins).  Re-injected on every load
        #: completion -- see :meth:`_replay_live_state`.
        self._live_state: list[tuple[str, list[object]]] = []
        self._content_page.console_message.connect(self._on_console_message)
        self._content_page.loadFinished.connect(self._replay_live_state)

    # ------------------------------------------------------------ loading --
    def show_page(self, page: RenderedPage, *, page_id: str) -> None:
        """Publish the rendered HTML on our scheme and navigate to it."""
        self._page_id = page_id
        self._base_url = page.base_url
        self._reset_readiness()
        self.load(PageStore.put(page_id, page.html))

    def reload_page(self) -> None:
        """Re-run the page (picks up a re-rendered body after an external edit)."""
        self._reset_readiness()
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
        self._dispatch("setFontSize", [int(size_px)])

    def apply_palette_css(self, css: str) -> None:
        """Live palette swap: replace the generated ``:root`` + token CSS block."""
        self._dispatch("setPaletteCss", [css])

    def apply_mermaid_theme(self, palette: Palette) -> None:
        """Live Mermaid re-theme: palette-derived themeVariables, re-rendered.

        Palette changes never reload the page (scroll position!), so hydrated
        diagrams must be re-rendered by app.js with the new colours.  ``vars``
        is passed as a plain JSON object argument, not stringified twice, or
        mermaid.initialize chokes.
        """
        self._dispatch("setMermaidTheme", [mermaid_theme_vars(palette)])

    def reapply_theme(self, palette: Palette, font_size_px: int, *, appearance: Appearance) -> None:
        """Full theme refresh after a palette or appearance change."""
        self.apply_palette_css(page_css(palette, font_size_px, appearance=appearance))
        self.apply_mermaid_theme(palette)

    # ---------------------------------------------------------- scrolling --
    def scroll_position(self, callback: JsCallback) -> None:
        self._run(_JS_SCROLL_Y, callback)

    def restore_scroll(self, scroll_y: int) -> None:
        # PySide6 stubs expose loadProgress only as a signal; at runtime the
        # no-argument call returns the int percentage.  Not remembered live
        # state: a scroll restore belongs to exactly one navigation.
        if self.loadProgress() >= 100:  # type: ignore[operator]
            self._run(_JS_ENQUEUE.format(call=f'["setScrollY", [{int(scroll_y)}]]'))

    def is_ready(self, callback: JsCallback) -> None:
        """Whether app.js finished its synchronous work (used by the smoke tests)."""
        self._run(_JS_READY, callback)

    def run_script(self, script: str, callback: JsCallback | None = None) -> None:
        """Escape hatch used by the headless GUI tests."""
        self._run(script, callback)

    # ------------------------------------------------------------ private --
    def _dispatch(self, name: str, args: list[object]) -> None:
        """Send a live-control call and remember it as the latest state."""
        self._live_state = [s for s in self._live_state if s[0] != name] + [(name, args)]
        self._run(self._enqueue_js(name, args))

    def _replay_live_state(self, ok: bool) -> None:
        """Re-send the retained live state onto every freshly loaded page.

        The JS queue covers calls injected into the *new* document before
        app.js evaluated; this covers the other side of the race -- an
        injection that still landed on the outgoing document (Qt's load state
        flips asynchronously after ``reload()``) and was discarded with it.
        All live calls are idempotent whole-state assignments, so a replay is
        a no-op when the original already applied, and a later user change
        simply queues behind the replay on the page's single JS channel.
        """
        if not ok:
            return
        for name, args in self._live_state:
            self._run(self._enqueue_js(name, args))

    @staticmethod
    def _enqueue_js(name: str, args: list[object]) -> str:
        """Wrap ``name(*args)`` as a ``__mdviewPending`` queue push."""
        payload = json.dumps([name, args], ensure_ascii=False, sort_keys=False)
        payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        return _JS_ENQUEUE.format(call=payload)

    def _reset_readiness(self) -> None:
        """Flag the current document not-ready, synchronously, before navigating.

        The old document's ``mdviewReady`` stays ``'1'`` until the replacement
        navigation commits; without this reset a readiness poll issued right
        after ``reload_page()`` could observe that stale ``'1'`` and inject
        live-control calls into the new context before app.js has run.
        """
        self._run(_JS_RESET_READY)

    def _run(self, script: str, callback: JsCallback | None = None) -> None:
        if callback is None:
            self._content_page.runJavaScript(script)
        else:
            self._content_page.runJavaScript(script, callback)

    def _on_console_message(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        if level in (
            QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel,
            QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel,
        ):
            self._load_errors.append(f"{message} ({source_id}:{line_number})")


def document_dir_of(path: Path) -> Path:
    """Directory a document's relative resources resolve against."""
    return path.resolve().parent
