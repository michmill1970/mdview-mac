"""Headless UI screenshot pack for product-owner visual review.

Launches the real :class:`~mdview.viewer.main_window.MainWindow` with
offscreen QtWebEngine — the exact harness ``tests/test_webengine_smoke.py``
uses: Chromium needs the container flags that
:func:`mdview.viewer.application.prepare_webengine_environment` sets, and
every ``grab()`` follows the paint-settle window from
:mod:`mdview.viewer.capture` so we capture real content, never a blank frame.

The script drives the same actions the menus and shortcuts trigger (palette
menu handler, zoom actions, tab jump actions), writes PNGs to
``artifacts/shots/``, and verifies each one is non-trivial (size and pixel
variety) before exiting.

Run:  uv run python scripts/screenshot_pack.py
Exit: 0 when every shot is on disk and passes verification; 1 otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QDialogButtonBox, QLineEdit, QWidget

from mdview.core.paths import DATA_DIR_ENV_VAR
from mdview.viewer.application import AppContext, bootstrap
from mdview.viewer.capture import DEFAULT_SETTLE_MS, capture, settle
from mdview.viewer.content_view import ContentView
from mdview.viewer.main_window import MainWindow
from mdview.viewer.palette_editor import PaletteEditor

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"
SHOTS_DIR = REPO_ROOT / "artifacts" / "shots"

#: Scratch app data dir; seeded from the test suite's shared vendor cache so
#: the pack renders fully offline (same trick as ``conftest.seeded_vendor``).
DATA_DIR = Path("/tmp/opencode/mdview-screenshot-data")
VENDOR_CACHE = Path("/tmp/opencode/mdview-vendor-cache/vendor")

DEMO_DOC = EXAMPLES / "demo.md"
MEETING_DOC = EXAMPLES / "meeting-notes.md"
CHECKLIST_DOC = EXAMPLES / "release-checklist.md"

WINDOW_SIZE = (1280, 800)
#: Dialog placement for the palette-editor shot: parked right so the live
#: preview (re-coloured headings) stays visible in the page's left column.
EDITOR_POS = QPoint(540, 80)
EDITOR_SIZE = (700, 640)
#: The colour the palette-editor shot previews into the page (``heading``).
PREVIEW_HEADING_RGB = "rgb(214, 50, 12)"  # #d6320c

MIN_BYTES = 20_000
MIN_DISTINCT_COLORS = 25


# --------------------------------------------------------------- plumbing --
def configure_environment() -> None:
    """Offscreen Qt platform + scratch data dir with vendored assets seeded."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if not VENDOR_CACHE.is_dir():
        raise SystemExit(
            f"vendor cache missing at {VENDOR_CACHE}; run the webengine smoke tests "
            "first (they fetch KaTeX/Mermaid once) or `uv run mdview fetch-assets`"
        )
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    DATA_DIR.mkdir(parents=True)
    shutil.copytree(VENDOR_CACHE, DATA_DIR / "vendor")
    os.environ[DATA_DIR_ENV_VAR] = str(DATA_DIR)


def _decode(raw: object) -> object:
    """Turn a ``runJavaScript`` result into a Python value (see smoke tests)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _probe(expression: str) -> str:
    """Wrap a JS expression so its value reaches Python as a JSON string."""
    return f"JSON.stringify((() => ({expression}))())"


def run_js(view: ContentView, expression: str, timeout_ms: int = 20_000) -> object:
    """Evaluate one JS expression in the page and wait for the callback."""
    box: list[object] = []
    view.run_script(_probe(expression), lambda raw: box.append(_decode(raw)))
    deadline = time.monotonic() + timeout_ms / 1000
    while not box and time.monotonic() < deadline:
        settle(100)
    return box[0] if box else None


def wait_js(
    view: ContentView,
    expression: str,
    predicate: Callable[[object], bool],
    what: str,
    timeout_ms: int = 90_000,
) -> object:
    """Poll ``expression`` until ``predicate(value)`` holds or the timeout hits."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        value = run_js(view, expression)
        if predicate(value):
            return value
    raise TimeoutError(f"timed out waiting for {what}")


def wait_rendered(
    view: ContentView, *, what: str = "the document", diagrams: int = 0, images: int = 0
) -> None:
    """Block until app.js, Mermaid diagrams, and local images are fully in."""
    wait_js(
        view,
        "document.documentElement?.dataset.mdviewReady === '1'",
        lambda v: v is True,
        f"app.js ready on {what}",
    )
    if diagrams:
        wait_js(
            view,
            "Number(document.documentElement?.dataset.mdviewDiagrams ?? 0)",
            lambda v: v == diagrams,
            f"{diagrams} Mermaid diagram(s) on {what}",
            timeout_ms=120_000,
        )
        wait_js(
            view,
            "document.querySelectorAll('.md-mermaid-svg svg').length",
            lambda v: v == diagrams,
            f"Mermaid SVG markup on {what}",
        )
    if images:
        wait_js(
            view,
            "[...document.querySelectorAll('img')].filter(i => i.complete && i.naturalWidth > 0)"
            ".length",
            lambda v: v == images,
            f"{images} loaded image(s) on {what}",
        )


def shoot(widget: QWidget, name: str) -> Path:
    """Grab ``widget`` (with the capture module's settle window) to the shots dir."""
    return capture(widget, SHOTS_DIR / f"{name}.png")


def shoot_dialog(window: MainWindow, dialog: QWidget, name: str) -> Path:
    """Composite the (top-level) dialog over a grab of the main window.

    Offscreen Qt has no window compositor, so ``window.grab()`` cannot contain
    a child dialog: we paint the dialog's own grab at the position it was
    moved to -- visually identical to what a user sees on screen.
    """
    settle(DEFAULT_SETTLE_MS)
    canvas = window.grab()
    overlay = dialog.grab()
    painter = QPainter(canvas)
    painter.drawPixmap(EDITOR_POS, overlay)
    painter.end()
    path = SHOTS_DIR / f"{name}.png"
    if not canvas.save(str(path), "PNG"):
        raise OSError(f"could not write {path}")
    return path


# ------------------------------------------------------------------ shots --
def set_palette(ctx: AppContext, palette_id: str) -> None:
    """Pin the palette's own appearance, then pick it via the menu's handler."""
    palette = ctx.registry.get(palette_id)
    ctx.window.set_appearance_mode(palette.appearance)
    ctx.window._set_palette(palette.appearance, palette_id)  # the Palettes-menu slot


def shot_palette_pack(ctx: AppContext) -> list[tuple[str, Path]]:
    """main-light / main-dark / main-dracula on demo.md."""
    window = ctx.window
    demo_view = window.current_view()
    assert demo_view is not None
    wait_rendered(demo_view, diagrams=1, images=1)
    # the moved-up Links & images section must be inside the first viewport
    top_view = run_js(
        demo_view,
        "(() => { const a = document.querySelector('.md-content a');"
        " const i = document.querySelector('img');"
        " return [a ? a.getBoundingClientRect().bottom < innerHeight : false,"
        " i ? i.getBoundingClientRect().bottom < innerHeight : false]; })()",
    )
    if top_view != [True, True]:
        raise RuntimeError(f"links/image are not visible in the first viewport: {top_view}")
    shots = [("main-light", shoot(window, "main-light"))]

    set_palette(ctx, "github-dark")
    shots.append(("main-dark", shoot(window, "main-dark")))

    set_palette(ctx, "dracula-dark")
    shots.append(("main-dracula", shoot(window, "main-dracula")))
    return shots


BELOW_FOLD_FEATURES_JS = """(() => {
  const full = (el) => { if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.top >= -4 && r.bottom <= innerHeight + 4; };
  return {
    mermaid: full(document.querySelector('.md-mermaid-svg svg')),
    code: full(document.querySelector('.md-code'))
          && !!document.querySelector('.md-code-inner span[class]'),
    quote: full(document.querySelector('blockquote')),
    task: full(document.querySelector('input[type="checkbox"]:checked')),
  };
})()"""


def shot_below_fold(ctx: AppContext) -> list[tuple[str, Path]]:
    """Scroll demo.md to its tail and re-shoot light / dark / dracula.

    Anchors the Mermaid diagram's bottom edge to the fold (scrollY 0 shows the
    moved-up Links & images section instead), so one viewport covers quote,
    task checkboxes, syntax-highlighted code, and the diagram.
    """
    window = ctx.window
    view = window.current_view()
    assert view is not None

    scroll_y = run_js(
        view,
        "(() => { const s = document.querySelector('.md-mermaid-svg svg');"
        " const q = document.querySelector('blockquote');"
        " if (!s || !q) return null;"
        " const bottom = s.getBoundingClientRect().bottom + scrollY;"
        " const top = q.getBoundingClientRect().top + scrollY;"
        " return [Math.max(0, Math.round(bottom) - innerHeight - 4),"
        " Math.round(top) + 4]; })()",
    )
    if not isinstance(scroll_y, list) or len(scroll_y) != 2:
        raise RuntimeError(f"could not measure the below-fold scroll position: {scroll_y!r}")
    lo, hi = (int(scroll_y[0]), int(scroll_y[1]))
    if lo > hi:
        raise RuntimeError(
            f"below-fold features do not fit one viewport: need scroll >= {lo}px "
            f"for the diagram and <= {hi}px for the quote (innerHeight too small?)"
        )
    # Prefer the classic anchor (diagram bottom at the fold = lo + 4) but
    # never scroll so far that the quote's top rule leaves the viewport.
    scroll_y = min(hi, lo + 4)
    view.run_script(f"window.mdview.setScrollY({scroll_y})")
    in_view = wait_js(
        view,
        BELOW_FOLD_FEATURES_JS,
        lambda v: isinstance(v, dict) and v.get("mermaid") is True and v.get("code") is True,
        "code block and Mermaid diagram to enter the viewport",
        timeout_ms=20_000,
    )
    assert isinstance(in_view, dict)
    missing = [key for key in ("quote", "task") if in_view.get(key) is not True]
    if missing:
        raise RuntimeError(f"below-fold viewport misses {missing} at scroll {scroll_y}px")
    print(f"below-fold shots at scrollY={scroll_y}px, features in view: {in_view}")

    shots: list[tuple[str, Path]] = []
    for palette_id, name in (
        ("github-light", "main-light-below"),
        ("github-dark", "main-dark-below"),
        ("dracula-dark", "dracula-below"),
    ):
        set_palette(ctx, palette_id)
        shots.append((name, shoot(window, name)))

    view.run_script("window.mdview.setScrollY(0)")
    wait_js(view, "scrollY", lambda v: v == 0, "scroll restored to top", 10_000)
    return shots


def shot_tabs(ctx: AppContext) -> tuple[str, Path]:
    """Three tabs open (extra docs + demo), middle tab active."""
    window = ctx.window
    set_palette(ctx, "github-light")
    # Tests drive the model the same way: the window deliberately exposes no
    # public tab enumeration, and these are the very paths the UI mutates it.
    window._model.close_all()
    window.open_paths([MEETING_DOC, DEMO_DOC, CHECKLIST_DOC])
    assert window.tab_count() == 3
    for tab in window._model.tabs:
        view = window.view_for(tab.tab_id)
        assert view is not None, f"no view for {tab.title}"
        heavy = tab.document.path.name == DEMO_DOC.name
        wait_rendered(
            view,
            what=tab.title,
            diagrams=1 if heavy else 0,
            images=1 if heavy else 0,
        )
    window._actions["tab2"].trigger()  # Cmd+2 jump, like a user would
    assert window.current_tab() is not None
    return ("tabs-multi", shoot(window, "tabs-multi"))


def shot_font_large(ctx: AppContext) -> tuple[str, Path]:
    """demo.md with the text size bumped four zoom steps (Cmd+= x4)."""
    window = ctx.window
    window._model.close_all()
    window.open_paths([DEMO_DOC])
    view = window.current_view()
    assert view is not None
    wait_rendered(view, what="demo.md (large font)", diagrams=1, images=1)
    for _ in range(4):
        window._actions["zoomIn"].trigger()  # Cmd+= x4
    wait_js(view, "window.mdview.stats().fontSize", lambda v: v == "20px", "font size 20px", 10_000)
    return ("font-large", shoot(window, "font-large"))


def shot_palette_editor(ctx: AppContext) -> tuple[str, Path]:
    """Palette editor open over demo.md with a live preview edit applied."""
    window = ctx.window
    window._actions["zoomReset"].trigger()  # back to 16px for a tidy shot
    view = window.current_view()
    assert view is not None
    wait_js(view, "window.mdview.stats().fontSize", lambda v: v == "16px", "font reset", 10_000)

    editor = PaletteEditor(ctx.registry, ctx.registry.get("github-light"), window)
    editor.palette_previewed.connect(window._preview_palette)  # the View menu's own slot
    editor.resize(*EDITOR_SIZE)
    editor.move(EDITOR_POS)
    editor.show()
    settle(800)

    heading_line = editor.findChild(QLineEdit, "color_heading")
    if heading_line is None:
        raise RuntimeError("palette editor lost its color_heading field")
    heading_line.setText("#d6320c")
    heading_line.editingFinished.emit()  # exactly what losing focus in the field fires
    wait_js(
        view,
        "getComputedStyle(document.querySelector('.md-content h1')).color",
        lambda v: v == PREVIEW_HEADING_RGB,
        "live palette preview to reach the page",
        timeout_ms=15_000,
    )
    path = shoot_dialog(window, editor, "palette-editor")
    editor.reject()
    settle(300)
    return ("palette-editor", path)


def shot_palette_editor_duplicate(ctx: AppContext) -> tuple[str, Path]:
    """Editor open on a DUPLICATED (user) palette: plain editable state, no built-in hint."""
    window = ctx.window
    view = window.current_view()
    assert view is not None

    editor = PaletteEditor(ctx.registry, ctx.registry.get("github-light"), window)
    editor.palette_previewed.connect(window._preview_palette)
    editor.resize(*EDITOR_SIZE)
    editor.move(EDITOR_POS)
    editor.show()
    settle(800)

    editor._duplicate_button.click()  # the real button: name -> "GitHub Light Copy"
    save = editor._button_box.button(QDialogButtonBox.StandardButton.Save)
    assert save.isEnabled(), "a duplicated palette must stay savable"
    save.click()  # writes the user copy, refreshes the list, selects the copy
    if not (editor.last_saved_path or "").endswith("github-light-copy.json"):
        raise RuntimeError(f"unexpected save path: {editor.last_saved_path!r}")
    if editor.windowTitle() != "Edit Palette — GitHub Light Copy":
        raise RuntimeError(f"unexpected dialog title: {editor.windowTitle()!r}")

    heading_line = editor.findChild(QLineEdit, "color_heading")
    if heading_line is None:
        raise RuntimeError("palette editor lost its color_heading field")
    heading_line.setText("#d6320c")
    heading_line.editingFinished.emit()  # same field flow as the main editor shot
    wait_js(
        view,
        "getComputedStyle(document.querySelector('.md-content h1')).color",
        lambda v: v == PREVIEW_HEADING_RGB,
        "live palette preview to reach the page",
        timeout_ms=15_000,
    )
    path = shoot_dialog(window, editor, "palette-editor-dup")
    editor.reject()
    settle(300)
    return ("palette-editor-dup", path)


def shot_high_contrast(ctx: AppContext) -> tuple[str, Path]:
    """Same document under the high-contrast-light preset."""
    set_palette(ctx, "high-contrast-light")
    return ("high-contrast", shoot(ctx.window, "high-contrast"))


def shot_missing_file(ctx: AppContext) -> tuple[str, Path]:
    """Error-tab state (Phase 6 defect 3): a good file + a missing one.

    ``mdview open demo.md does-not-exist.md`` used to open the good tab and
    swallow the failure; the missing path now gets a themed error tab naming
    the path and reason -- and this shot lets the vision review check it.
    """
    window = ctx.window
    set_palette(ctx, "github-light")
    window._model.close_all()
    window.open_paths([DEMO_DOC, EXAMPLES / "does-not-exist.md"])
    assert window.tab_count() == 2
    tab = window.current_tab()
    assert tab is not None
    assert tab.error is not None, "the missing file must be the active error tab"
    view = window.current_view()
    assert view is not None
    wait_rendered(view, what="error tab")
    heading = run_js(view, "document.querySelector('.md-content h1')?.textContent ?? ''")
    if not isinstance(heading, str) or "Can’t open" not in heading:  # noqa: RUF001
        raise RuntimeError(f"error tab did not render: {heading!r}")
    return ("missing-file", shoot(window, "missing-file"))


# ------------------------------------------------------------ validation --
def distinct_colors(path: Path, grid: int = 64) -> int:
    """Count different colours on a ``grid``-point lattice across the PNG."""
    image = QImage(str(path))
    if image.isNull():
        return 0
    step_x = max(1, image.width() // grid)
    step_y = max(1, image.height() // grid)
    seen = {
        image.pixelColor(x, y).name()
        for y in range(0, image.height(), step_y)
        for x in range(0, image.width(), step_x)
    }
    return len(seen)


def verify(shots: list[tuple[str, Path]]) -> list[str]:
    """Print the pack inventory and return human-readable failures."""
    problems: list[str] = []
    print(f"{'file':28} {'bytes':>9} {'colours':>8}")
    for name, path in shots:
        if not path.is_file():
            problems.append(f"{name}: {path} missing")
            print(f"{path.name:28} {'MISSING':>9}")
            continue
        size = path.stat().st_size
        colors = distinct_colors(path)
        print(f"{path.name:28} {size:>9,} {colors:>8,}")
        if size <= MIN_BYTES:
            problems.append(f"{name}: {size} bytes <= {MIN_BYTES}")
        if colors < MIN_DISTINCT_COLORS:
            problems.append(f"{name}: only {colors} distinct colours (blank?)")
    return problems


def main() -> int:
    configure_environment()
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    for stale in SHOTS_DIR.glob("*.png"):
        stale.unlink()

    ctx = bootstrap(["mdview-shots"], ensure_bundled_assets=False)
    for warning in ctx.startup_warnings:
        print(f"mdview: {warning}", file=sys.stderr)
    window = ctx.window
    window.resize(*WINDOW_SIZE)
    window.show()
    settle(500)

    window.open_paths([DEMO_DOC])
    shots = shot_palette_pack(ctx)
    shots += shot_below_fold(ctx)
    shots.append(shot_tabs(ctx))
    shots.append(shot_font_large(ctx))
    shots.append(shot_palette_editor(ctx))
    shots.append(shot_palette_editor_duplicate(ctx))
    shots.append(shot_high_contrast(ctx))
    shots.append(shot_missing_file(ctx))

    fatal = [
        (tab.title, entry)
        for tab in window._model.tabs
        if (view := window.view_for(tab.tab_id)) is not None
        for entry in view.load_errors
        if "Uncaught" in entry or "Refused to" in entry
    ]
    window.close()
    if fatal:
        print(f"page errors: {fatal}", file=sys.stderr)

    problems = verify(shots)
    print()
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    print(f"{len(shots) - len(problems)}/{len(shots)} shots verified in {SHOTS_DIR}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
