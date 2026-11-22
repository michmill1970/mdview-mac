"""Phase 14: WYSIWYG printing of the current tab (paper always light).

The user-facing surface is one menu entry (File > Print..., Cmd+P) and one
policy: no matter what palette is on screen, paper is rendered with the
user's *light* palette.  Everything after the dialog is the pipeline in
``mdview.viewer.printing`` (hidden print-variant view -> printToPdf ->
QPdfDocument raster -> QPainter on the panel's QPrinter).

The panel itself is the seam: tests monkeypatch ``printing.request_printer``
to return a ``QPrinter`` writing PDF to a file, so the whole chain runs
headless -- including the real ``printToPdf`` of a hydrated page -- and the
paper is asserted with ``QPdfDocument`` pixels (light background, dark text).
Nothing may outlive a job: no PageStore ``print-*`` key, no temp dir, whether
the job printed, failed, or was cancelled.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QMarginsF, QSize
from PySide6.QtGui import QKeySequence, QPageLayout
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QWidget

from mdview.core.palette import PaletteRegistry
from mdview.core.settings import Settings
from mdview.core.tabs import TabModel
from mdview.viewer import printing
from mdview.viewer.application import AppContext, bootstrap
from mdview.viewer.main_window import MainWindow
from mdview.viewer.scheme import PageStore

pytestmark = [pytest.mark.gui, pytest.mark.webengine]


@pytest.fixture
def ctx(seeded_vendor: Path) -> AppContext:
    context = bootstrap(["mdview-test"], ensure_bundled_assets=False)
    yield context
    context.window.close()


def _paper_printer(out_path: Path) -> QPrinter:
    """A QPrinter configured exactly as the pipeline leaves the macOS panel:
    paper settings live on it -- here, a PDF file standing in for the printer."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(out_path))
    return printer


def _render_page(pdf_path: Path, index: int = 0, *, size: tuple[int, int] = (330, 467)):
    document = QPdfDocument()
    assert document.load(str(pdf_path)).name == "None_", f"paper.pdf unreadable: {pdf_path}"
    image = document.render(index, QSize(*size))
    assert not image.isNull()
    return document, image


def _dark_pixel_fraction(image) -> float:
    sampled = dark = 0
    for x in range(0, image.width(), 4):
        for y in range(0, image.height(), 4):
            sampled += 1
            if image.pixelColor(x, y).lightness() < 100:
                dark += 1
    return dark / max(sampled, 1)


def _light_pixel_fraction(image) -> float:
    """Share of near-white samples: on light paper most of the sheet is white."""
    sampled = light = 0
    for x in range(0, image.width(), 4):
        for y in range(0, image.height(), 4):
            sampled += 1
            if image.pixelColor(x, y).lightness() > 240:
                light += 1
    return light / max(sampled, 1)


class TestPrintOptions:
    """The paper policy, as a pure unit: light palette whatever is on screen."""

    def test_print_variant_renders_the_light_palette_in_light_appearance(
        self, registry: PaletteRegistry, demo_file: Path
    ) -> None:
        model = TabModel(on_change=lambda: None)
        tab = model.open_path(demo_file)
        # Screen state deliberately dark: paper must still be the light palette.
        settings = Settings(
            appearance_mode="dark", palette_dark="dracula-dark", font_size_px=18
        ).normalized()
        options = printing.print_options(
            registry,
            settings,
            font_size_px=18,
            title=tab.title,
            document_dir=demo_file.parent,
            page_id="print-test",
        )
        assert options.palette.id == settings.palette_light
        assert options.palette.appearance == "light"
        assert options.appearance == "light"
        assert options.font_size_px == 18  # typography mirrors the screen
        assert options.title == tab.title
        assert options.document_dir == demo_file.parent
        assert options.page_id == "print-test"

    def test_missing_light_palette_falls_back_like_every_other_paint(
        self, registry: PaletteRegistry, demo_file: Path
    ) -> None:
        settings = Settings(palette_light="deleted-palette").normalized()
        options = printing.print_options(
            registry,
            settings,
            font_size_px=16,
            title="t",
            document_dir=demo_file.parent,
            page_id="print-test",
        )
        assert options.palette.appearance == "light"

    def test_print_page_ids_cannot_collide_with_tab_pages(self) -> None:
        assert not printing.print_page_id().startswith("tab-")
        assert len({printing.print_page_id() for _ in range(8)}) == 8

    def test_pdf_layout_keeps_panel_paper_margins_and_unit(self, tmp_path: Path) -> None:
        printer = _paper_printer(tmp_path / "probe.pdf")
        printer.setPageMargins(QMarginsF(20, 20, 20, 20), QPageLayout.Unit.Millimeter)
        layout = printing.pdf_layout_for(printer)
        margins = layout.margins(QPageLayout.Unit.Millimeter)
        assert layout.pageSize() == printer.pageLayout().pageSize()
        assert layout.orientation() == printer.pageLayout().orientation()
        # 20 mm in, not ~0.2 mm (the QPageLayout ctor's Point-unit trap).
        assert abs(margins.top() - 20) < 0.5
        assert abs(margins.left() - 20) < 0.5


class TestPrintAction:
    def test_action_text_shortcut_and_file_menu_position(self, ctx: AppContext) -> None:
        window: MainWindow = ctx.window
        action = window._actions["print"]
        assert action.text() == "&Print…"
        assert action.shortcut() == QKeySequence(QKeySequence.StandardKey.Print)
        file_menu = window.menuBar().actions()[0].menu()
        assert file_menu is not None
        texts = [action.text() for action in file_menu.actions() if action.text()]
        assert texts == ["&Open…", "&Print…", "&Close Tab"]

    def test_print_enabled_tracks_open_tabs(self, ctx: AppContext, demo_file: Path) -> None:
        window = ctx.window
        assert not window._actions["print"].isEnabled()  # no tab, nothing to print
        window.open_paths([demo_file])
        assert window._actions["print"].isEnabled()
        window._actions["closeTab"].trigger()
        assert not window._actions["print"].isEnabled()


class TestPrintFlow:
    """⌘P end to end against a QPrinter writing paper to a PDF file."""

    def test_print_current_tab_lands_light_on_paper(
        self,
        ctx: AppContext,
        demo_file: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        window = ctx.window
        window.set_appearance_mode("dark")  # on screen: dark palette
        paper = tmp_path / "paper.pdf"
        monkeypatch.setattr(printing, "request_printer", lambda parent: _paper_printer(paper))

        window.open_paths([demo_file])  # math + mermaid doc: hydration must complete
        started = time.monotonic()
        window._actions["print"].trigger()

        assert paper.is_file()
        assert paper.stat().st_size > 0
        assert time.monotonic() - started < printing._HYDRATE_TIMEOUT_MS / 1000 + 60
        assert "Printed" in window.statusBar().currentMessage()

        document, image = _render_page(paper)
        assert document.pageCount() >= 1
        # Dark palette was on screen; paper is light: mostly-white sheet...
        assert _light_pixel_fraction(image) > 0.6
        # ...with dark text actually on it (a dark-palette print would invert
        # both ratios: dark background, light glyphs).
        assert _dark_pixel_fraction(image) > 0.001

        # No job leftovers: no print page in the store, no temp dirs on disk.
        assert not [key for key in PageStore._pages if key.startswith("print-")]
        assert not list(Path(tempfile.gettempdir()).glob("mdview-print-*"))

    def test_print_error_tab_prints_what_the_tab_shows(
        self,
        ctx: AppContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window = ctx.window
        paper = tmp_path / "error-paper.pdf"
        monkeypatch.setattr(printing, "request_printer", lambda parent: _paper_printer(paper))

        window.open_paths([tmp_path / "vanished.md"])  # error tab becomes active
        error_tab = window.current_tab()
        assert error_tab is not None
        assert error_tab.error is not None
        window._actions["print"].trigger()

        assert paper.is_file()
        assert "Printed" in window.statusBar().currentMessage()

    def test_cancelled_panel_prints_nothing_and_leaves_no_trace(
        self,
        ctx: AppContext,
        demo_file: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        window = ctx.window
        paper = tmp_path / "never.pdf"
        monkeypatch.setattr(printing, "request_printer", lambda parent: None)  # Cancel

        window.open_paths([demo_file])
        window._actions["print"].trigger()

        assert not paper.exists()
        assert not [key for key in PageStore._pages if key.startswith("print-")]
        assert not list(Path(tempfile.gettempdir()).glob("mdview-print-*"))
        # The tab is untouched and printing remains available.
        assert window.tab_count() == 1
        assert window._actions["print"].isEnabled()

    def test_failed_print_reports_on_the_status_bar(
        self,
        ctx: AppContext,
        demo_file: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        window = ctx.window
        monkeypatch.setattr(
            printing,
            "request_printer",
            lambda parent: _paper_printer(tmp_path / "missing-dir" / "paper.pdf"),
        )

        window.open_paths([demo_file])
        window._actions["print"].trigger()

        message = window.statusBar().currentMessage()
        assert message.startswith("Print failed")
        assert window._actions["print"].isEnabled()  # usable again right away

    def test_print_job_hands_the_dialog_a_parent_widget(
        self,
        ctx: AppContext,
        demo_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The panel must sheet off the window, not float unowned."""
        window = ctx.window
        seen: list[object] = []

        def capture(parent):
            seen.append(parent)
            return None

        monkeypatch.setattr(printing, "request_printer", capture)
        window.open_paths([demo_file])
        window._actions["print"].trigger()
        assert seen == [window]
        assert isinstance(seen[0], QWidget)
