"""End-to-end smoke test inside real (offscreen) QtWebEngine/Chromium.

This is the test that proves the whole product works: a real document renders
through ``mdview://`` with local KaTeX, local Mermaid (classic script hydration
through our scheme), Pygments-highlighted code, a table, and a relative
image served from the document's own directory -- and it proves the offline
guarantee live: zero ``http(s)`` resource requests in the browser's own
resource timing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage

from mdview.core.palette import Palette
from mdview.viewer.application import AppContext, bootstrap
from mdview.viewer.capture import capture
from mdview.viewer.content_view import ContentView
from mdview.viewer.main_window import MainWindow

pytestmark = [pytest.mark.gui, pytest.mark.webengine]

SMOKE_DOC = """\
# Smoke

Inline math $a^2 + b^2 = c^2$.

```mermaid
graph TD; A[Start] --> B[Done];
```

| Col | Val |
| --- | --- |
| a   | 1   |

```python
def f(x):
    return x + 1  # add
```

![logo](logo.png)
"""


@pytest.fixture
def smoke_ctx(seeded_vendor: Path, tmp_path: Path) -> Any:
    doc = tmp_path / "smoke.md"
    doc.write_text(SMOKE_DOC, encoding="utf-8")
    logo = tmp_path / "logo.png"
    image = QImage(64, 64, QImage.Format.Format_RGBA8888)
    image.fill(QColor("crimson"))
    assert image.save(str(logo), "PNG")
    context = bootstrap(["mdview-test"], ensure_bundled_assets=False)
    context.window.open_paths([doc])
    yield context
    context.window.close()


def _decode(raw: Any) -> Any:
    """Turn a runJavaScript result into a Python value.

    This QtWebEngine build hands back ``''`` for non-primitive JS values, so
    every probe below is wrapped in ``JSON.stringify`` in-page; ``''`` also
    covers thrown scripts and ``undefined`` and decodes to ``None``.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _probe_expr(script: str) -> str:
    """Wrap a JS *expression* so its value reaches Python as a JSON string."""
    return f"JSON.stringify((() => ({script}))())"


def _js(qtbot: Any, view: ContentView, script: str, timeout_ms: int = 30_000) -> Any:
    """Evaluate ``script`` (a JS expression) in the page and return the result."""
    box: list[Any] = []
    view.run_script(_probe_expr(script), lambda raw: box.append(_decode(raw)))
    qtbot.waitUntil(lambda: bool(box), timeout=timeout_ms)
    return box[0]


def _wait_js(
    qtbot: Any,
    view: ContentView,
    script: str,
    predicate: Callable[[Any], bool],
    timeout_ms: int,
) -> Any:
    """Re-evaluate ``script`` (each poll tick) until ``predicate(value)`` holds."""
    holder: dict[str, Any] = {}

    def probe() -> bool:
        view.run_script(_probe_expr(script), lambda raw: holder.__setitem__("value", _decode(raw)))
        return bool(predicate(holder.get("value")))

    qtbot.waitUntil(probe, timeout=timeout_ms)
    return holder.get("value")


class TestWebEngineSmoke:
    def test_full_pipeline(
        self,
        qtbot: Any,
        smoke_ctx: AppContext,
        dark_palette: Palette,
        light_palette: Palette,
        tmp_path: Path,
    ) -> None:
        window: MainWindow = smoke_ctx.window
        view = window.current_view()
        assert view is not None

        # 1. the page loaded and app.js finished its synchronous work
        _wait_js(
            qtbot,
            view,
            "document.documentElement?.dataset.mdviewReady === '1'",
            lambda v: v is True,
            60_000,
        )

        # 2. local KaTeX, Pygments, tables all hydrated
        stats = _js(qtbot, view, "window.mdview.stats()")
        assert isinstance(stats, dict)
        assert stats["math"] >= 1  # katex span for the inline equation
        assert stats["code"] >= 1
        assert stats["tables"] >= 1
        assert stats["contentChars"] > len("# Smoke")

        # 3. Mermaid: classic <script> hydration through mdview://
        diagrams = _wait_js(
            qtbot,
            view,
            "Number(document.documentElement?.dataset.mdviewDiagrams ?? 0)",
            lambda v: v == 1,
            90_000,
        )
        assert diagrams == 1
        svg_ok = _js(qtbot, view, "document.querySelector('.md-mermaid-svg svg') !== null")
        assert svg_ok is True

        # 4. relative image served by mdview://doc
        image_width = _wait_js(
            qtbot,
            view,
            "document.querySelector('img')?.naturalWidth ?? 0",
            lambda v: isinstance(v, (int, float)) and v > 0,
            30_000,
        )
        assert image_width == 64

        # 5. live font size (no reload)
        view.set_font_size(23)
        _wait_js(qtbot, view, "window.mdview.stats().fontSize", lambda v: v == "23px", 10_000)

        # 6. live palette swap changes the computed background AND re-renders
        #    the Mermaid diagram with palette-derived themeVariables (a palette
        #    change never reloads the page, so the hydrated SVG must re-theme
        #    in place -- Phase 5).
        before = _js(qtbot, view, "window.mdview.stats().background")
        stats = _js(qtbot, view, "window.mdview.stats()")
        assert isinstance(stats, dict)
        assert stats["mermaidVars"] >= 30  # the init payload carried palette colours

        def svg_contains(color: Palette) -> str:
            hex_value = color.color("surface").lower()  # e.g. #151b23
            rgb = QColor(hex_value).getRgb()[:3]
            rgb_form = f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"  # serialiser-dependent forms
            return (
                "(() => { const s = document.querySelector('.md-mermaid-svg svg');"
                " if (!s) return false;"
                " const h = s.outerHTML.toLowerCase();"
                f" return h.includes('{hex_value}') || h.includes('{rgb_form}'); }})()"
            )

        # initial render already used the (light) palette's surface fill
        _wait_js(qtbot, view, svg_contains(light_palette), lambda v: v is True, 30_000)

        view.reapply_theme(dark_palette, 23, appearance="dark")
        after = _wait_js(
            qtbot,
            view,
            "window.mdview.stats().background",
            lambda v: v != before,
            10_000,
        )
        assert after != before
        # and the diagram followed the palette (node fill -> dark surface)
        _wait_js(qtbot, view, svg_contains(dark_palette), lambda v: v is True, 60_000)

        # 7. offline contract: Chromium attempted nothing off-scheme, the DOM
        # references nothing remote, and Qt's interceptor never had to block a
        # real request (QtWebEngine records no resource-timing entries for
        # custom-scheme handler responses, so we verify both sides directly).
        remote = _js(
            qtbot,
            view,
            "performance.getEntriesByType('resource').filter(e => /^https?:/i.test(e.name)).length",
        )
        assert remote == 0
        srcs = _js(
            qtbot,
            view,
            "[...document.querySelectorAll('link[href],script[src],img[src]')]"
            ".map(el => el.getAttribute('href') ?? el.getAttribute('src'))",
        )
        assert isinstance(srcs, list)
        assert len(srcs) >= 3  # css/js/katex really referenced
        assert all(str(s).startswith(("mdview:", "data:")) for s in srcs), srcs
        assert smoke_ctx.interceptor.blocked == []

        # 8. no uncaught JS errors and no CSP refusals in the page
        fatal = [
            entry for entry in view.load_errors if "Uncaught" in entry or "Refused to" in entry
        ]
        assert fatal == [], f"page reported errors: {fatal}"

        # 9. screenshot proves pixels, not just DOM
        shot = capture(view, tmp_path / "smoke.png")
        pixmap = QImage(str(shot))
        assert not pixmap.isNull()
        colors = {
            pixmap.pixelColor(x, y).name()
            for x in (4, pixmap.width() // 2, pixmap.width() - 4)
            for y in (4, pixmap.height() // 2, pixmap.height() - 4)
        }
        assert len(colors) > 1, f"screenshot is blank: {colors}"

    def test_live_injection_racing_a_reload_is_queued_not_lost(
        self, qtbot: Any, smoke_ctx: AppContext, dark_palette: Palette
    ) -> None:
        """Phase 7 race regression (macOS full-suite): live-control injections
        must never require ``window.mdview`` to already exist -- and must never
        die with the outgoing document either.

        QtWebEngine runs injected JS as soon as the reloaded document's main-
        world context exists -- before app.js, a ``type="module"`` script, has
        evaluated -- while its *load state* only flips asynchronously, so an
        injection can land on either side of the reload.  This test re-creates
        the exact failing user sequence: a (re)navigation with font/palette
        changes injected *immediately*, with no readiness wait in between.
        With the old direct-call code the page threw "Cannot read properties
        of undefined (reading 'setPaletteCss')" and the change was silently
        lost; with the queue + replay protocol the calls must land in order
        and the CSS must actually apply.
        """
        window = smoke_ctx.window
        view = window.current_view()
        assert view is not None
        ready = "document.documentElement?.dataset.mdviewReady === '1'"
        _wait_js(qtbot, view, ready, lambda v: v is True, 60_000)
        before = _js(qtbot, view, "window.mdview.stats().background")

        view.reload_page()
        # No readiness wait -- this is the race window itself.
        view.set_font_size(23)
        view.reapply_theme(dark_palette, 23, appearance="dark")

        # (b) the reload cleared the flag: a poll can never observe stale '1'
        # while the new document's app.js is still loading.
        assert _js(qtbot, view, ready) is not True

        # app.js finished, and the queued calls were applied in order.
        _wait_js(qtbot, view, ready, lambda v: v is True, 60_000)
        _wait_js(qtbot, view, "window.mdview.stats().fontSize", lambda v: v == "23px", 10_000)
        after = _wait_js(
            qtbot,
            view,
            "window.mdview.stats().background",
            lambda v: v is not None and v != before,
            10_000,
        )
        assert after != before

        fatal = [
            entry for entry in view.load_errors if "Uncaught" in entry or "Refused to" in entry
        ]
        assert fatal == [], f"page reported errors: {fatal}"

    def test_pending_call_queue_drains_in_order(self, qtbot: Any, smoke_ctx: AppContext) -> None:
        """The queue protocol deterministically: FIFO, last call wins, bad
        names skipped without throwing (so one queued failure can never
        strand the calls behind it or produce a page error)."""
        view = smoke_ctx.window.current_view()
        assert view is not None
        _wait_js(
            qtbot,
            view,
            "document.documentElement?.dataset.mdviewReady === '1'",
            lambda v: v is True,
            60_000,
        )
        result = _js(
            qtbot,
            view,
            "(() => {"
            " const q = (window.__mdviewPending ??= []);"
            " q.push(['setFontSize', [20]], ['setFontSize', [30]], ['noSuchMethod', []]);"
            " window.__mdviewFlush();"
            " return {fontSize: window.mdview.stats().fontSize,"
            " drained: window.__mdviewPending.length}; })()",
        )
        assert result == {"fontSize": "30px", "drained": 0}
        fatal = [entry for entry in view.load_errors if "Uncaught" in entry]
        assert fatal == []

    def test_reload_picks_up_external_edit(
        self, qtbot: Any, smoke_ctx: AppContext, tmp_path: Path
    ) -> None:
        window = smoke_ctx.window
        view = window.current_view()
        assert view is not None
        _wait_js(
            qtbot,
            view,
            "document.documentElement?.dataset.mdviewReady === '1'",
            lambda v: v is True,
            60_000,
        )
        doc = tmp_path / "smoke.md"
        doc.write_text(SMOKE_DOC + "\n## Later section ADDED-MARKER\n", encoding="utf-8")
        window._reload_current()  # watcher-free: drive the reload directly
        _wait_js(
            qtbot,
            view,
            "String(document.body?.innerText ?? '').includes('ADDED-MARKER')",
            lambda v: v is True,
            60_000,
        )


def test_capture_helper_grabs_plain_widget(qtbot: Any, tmp_path: Path) -> None:
    """``capture`` must also work for ordinary widgets (used for the window)."""
    from PySide6.QtWidgets import QLabel

    label = QLabel("hello capture")
    label.resize(200, 60)
    label.show()
    qtbot.waitExposed(label)
    shot = capture(label, tmp_path / "label.png", settle_ms=50)
    assert shot.is_file()
    assert shot.stat().st_size > 100
    label.close()
