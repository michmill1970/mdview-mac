"""WYSIWYG printing of the current tab (Phase 14).

Paper policy (product decision, Phase 14): **always light on paper**.  The
print pipeline re-renders the document through the same ``render_page`` path
with the user's chosen *light* palette and ``appearance="light"``, no matter
what is on screen -- a dark palette never costs toner, while the light
palette's own colours (code tints, diagram fills) come through exactly as
the user authored them.

Two-stage pipeline, and why: PySide6's bindings do not expose Qt's
``QWebEnginePage::print(QPrinter*)`` (it exists in C++ and PyQt; verified
absent from PySide6 6.12), so we cannot hand the live page to the printer
directly.  Instead:

    hidden ContentView (print variant; KaTeX + Mermaid fully hydrated)
      └─ printToPdf(temp file)              vector PDF, backgrounds kept
           └─ QPdfDocument rasterises each page at :data:`_RASTER_DPI`
                └─ QPainter onto the QPrinter from the native panel   paper

The paper size, orientation and margins are the ones the native macOS panel
settled on.  The margins are baked into the *intermediate* PDF (Chromium
paginates with them), so the raster stage draws page-to-page at zero printer
margins and the paper comes out exactly as paginated -- no doubled insets.
Page-range and copies picks from the panel are honoured inside Qt's print
engine, below this layer.

All Qt loops here pump a nested event loop with a deadline (same style the
offscreen tests use), so the app stays responsive and nothing can hang.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final
from uuid import uuid4

from PySide6.QtCore import QEventLoop, QMarginsF, QSize, QTimer
from PySide6.QtGui import QPageLayout, QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWidgets import QDialog, QWidget

from mdview.core.palette import PaletteRegistry
from mdview.core.settings import Settings
from mdview.core.theme import select_palette
from mdview.render.page import PageOptions, render_page
from mdview.viewer.content_view import ContentView
from mdview.viewer.scheme import MdViewSchemeHandler, PageStore

__all__ = [
    "PrintError",
    "PrintJob",
    "pdf_layout_for",
    "print_options",
    "print_page_id",
    "request_printer",
]

#: Raster resolution for the PDF -> paper step: sharp text at a bounded per-
#: page memory cost (A4 at 300 dpi is ~35 MB of QImage, not 1200-dpi's 560 MB).
_RASTER_DPI: Final = 300

_LOAD_TIMEOUT_MS: Final = 20_000
_HYDRATE_TIMEOUT_MS: Final = 30_000
_JS_REPLY_TIMEOUT_MS: Final = 4_000
_PDF_TIMEOUT_MS: Final = 60_000

#: True once app.js has evaluated (KaTeX is synchronous) *and* every mermaid
#: block is resolved: hydrated blocks are replaced by ``.md-mermaid-svg``,
#: failed ones stay as ``.md-mermaid[data-mdview-state="error"]`` (their
#: printed source fallback is final, so that counts as ready too).
_HYDRATED_JS: Final = (
    "(() => {"
    " const d = document.documentElement.dataset;"
    " if (d.mdviewReady !== '1') return false;"
    " return document.querySelector("
    "'.md-mermaid:not([data-mdview-state=\"error\"])') === null;"
    " })()"
)


class PrintError(RuntimeError):
    """A print step failed; the window turns it into a status message."""


def print_page_id() -> str:
    """Fresh PageStore key for one print job (kept out of ``tab-*`` ids)."""
    return f"print-{uuid4().hex[:8]}"


def print_options(
    registry: PaletteRegistry,
    settings: Settings,
    *,
    font_size_px: int,
    title: str,
    document_dir: Path,
    page_id: str,
) -> PageOptions:
    """The print variant: the user's light palette on light paper.

    Typography mirrors the screen (same font size, same title, relative
    images still resolve); only the colours deliberately differ -- see the
    module docstring for the paper policy.
    """
    selection = select_palette(
        registry,
        "light",
        palette_light=settings.palette_light,
        palette_dark=settings.palette_dark,
    )
    return PageOptions(
        palette=selection.palette,
        font_size_px=font_size_px,
        appearance="light",
        title=title,
        document_dir=document_dir,
        page_id=page_id,
        smooth_scrolling=False,
    )


def request_printer(parent: QWidget) -> QPrinter | None:
    """Open the native print panel; ``None`` when the user cancels.

    The seam tests monkeypatch -- no headless test ever opens a dialog.
    """
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    dialog = QPrintDialog(printer, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return printer


def pdf_layout_for(printer: QPrinter) -> QPageLayout:
    """Layout for the intermediate PDF: the panel's paper *and* margins.

    ``QPageLayout``'s constructor defaults to point units, so the millimetre
    margins are handed back with the matching unit or they shrink by ~3x.
    """
    layout = printer.pageLayout()
    return QPageLayout(
        layout.pageSize(),
        layout.orientation(),
        layout.margins(QPageLayout.Unit.Millimeter),
        QPageLayout.Unit.Millimeter,
    )


def print_pdf_to_printer(pdf_path: Path, printer: QPrinter) -> int:
    """Draw every PDF page full-bleed onto the printer; returns the page count.

    The printer layout is re-set to zero margins first: the panel's margins
    are already part of the PDF pixels, and re-applying them here would
    shrink and double-inset the page.
    """
    document = QPdfDocument()
    if document.load(str(pdf_path)).name != "None_":
        raise PrintError(f"could not read the print preview ({document.error().name})")
    pages = document.pageCount()
    if pages < 1:
        raise PrintError("the print preview contains no pages")

    layout = printer.pageLayout()
    zero = QPageLayout(
        layout.pageSize(),
        layout.orientation(),
        QMarginsF(0, 0, 0, 0),
        QPageLayout.Unit.Millimeter,
    )
    printer.setPageLayout(zero)
    size_pt = zero.pageSize().size(QPageSize.Unit.Point)
    pixels = QSize(
        round(size_pt.width() / 72 * _RASTER_DPI),
        round(size_pt.height() / 72 * _RASTER_DPI),
    )

    painter = QPainter(printer)
    if not painter.isActive():
        raise PrintError("could not start the print job")
    try:
        for index in range(pages):
            image = document.render(index, pixels)
            if image.isNull():
                raise PrintError(f"could not rasterise page {index + 1} of the preview")
            if index:
                printer.newPage()
            painter.drawImage(printer.pageRect(QPrinter.Unit.DevicePixel), image)
    finally:
        painter.end()
    return pages


class PrintJob:
    """One ⌘P press, end to end: panel -> print page -> PDF -> paper.

    Owns its hidden view, its ``PageStore`` entry and its temp directory, and
    releases all three in ``finally`` -- a cancelled or failed print leaves
    nothing behind.
    """

    def __init__(
        self,
        *,
        parent: QWidget,
        profile: QWebEngineProfile,
        registry: PaletteRegistry,
        settings: Settings,
        font_size_px: int,
        scheme_handler: MdViewSchemeHandler,
        markdown_source: str,
        title: str,
        document_dir: Path,
    ) -> None:
        self._parent = parent
        self._profile = profile
        self._registry = registry
        self._settings = settings
        self._font_size_px = font_size_px
        self._scheme = scheme_handler
        self._source = markdown_source
        self._title = title
        self._document_dir = document_dir

    def run(self) -> int:
        """Print; ``0`` means the user cancelled the panel.

        Raises:
            PrintError: a print step failed (timeout, failed render, printer).
        """
        printer = request_printer(self._parent)
        if printer is None:
            return 0

        page_id = print_page_id()
        workdir = Path(tempfile.mkdtemp(prefix="mdview-print-"))
        view = ContentView(self._profile, self._parent)
        loaded: dict[str, bool] = {}
        view.loadFinished.connect(lambda ok: loaded.setdefault("ok", ok))
        try:
            page = render_page(
                self._source,
                print_options(
                    self._registry,
                    self._settings,
                    font_size_px=self._font_size_px,
                    title=self._title,
                    document_dir=self._document_dir,
                    page_id=page_id,
                ),
            )
            # Relative images resolve against the document directory (already
            # allowed for the live tab; re-assert for the hidden view).
            self._scheme.add_document_root(self._document_dir)
            view.show_page(page, page_id=page_id)
            if not _wait_until(lambda: "ok" in loaded, _LOAD_TIMEOUT_MS) or not loaded["ok"]:
                raise PrintError("the print page failed to load")
            if not _poll_js(view, _HYDRATED_JS, _HYDRATE_TIMEOUT_MS):
                raise PrintError("timed out rendering math and diagrams for print")

            preview = workdir / "preview.pdf"
            _print_to_pdf(view.page(), preview, pdf_layout_for(printer))
            return print_pdf_to_printer(preview, printer)
        finally:
            PageStore.drop(page_id)
            view.deleteLater()
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# nested-loop helpers (deadline'd, Qt-safe)
# ---------------------------------------------------------------------------


def _wait_until(predicate: Callable[[], bool], timeout_ms: int) -> bool:
    """Run a nested event loop until ``predicate()`` is true; ``False`` on timeout."""
    if predicate():
        return True
    loop = QEventLoop()
    deadline = QTimer(loop)
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll = QTimer(loop)
    poll.setInterval(20)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.start(timeout_ms)
    poll.start()
    loop.exec()
    poll.stop()
    deadline.stop()
    return bool(predicate())


def _js_is_true(view: ContentView, script: str) -> bool:
    """Evaluate ``script`` in the page and wait (briefly) for its value."""
    reply: dict[str, object] = {}

    def remember(value: object) -> None:
        reply.setdefault("value", value)

    view.run_script(script, remember)
    if not _wait_until(lambda: "value" in reply, _JS_REPLY_TIMEOUT_MS):
        return False
    return reply["value"] is True


def _poll_js(view: ContentView, script: str, timeout_ms: int) -> bool:
    """Re-evaluate a JS condition until it is ``true`` or the deadline passes."""
    end = time.monotonic() + timeout_ms / 1000
    while True:
        if _js_is_true(view, script):
            return True
        if time.monotonic() >= end:
            return False


def _print_to_pdf(page: QWebEnginePage, pdf_path: Path, layout: QPageLayout) -> None:
    """Vector-ify the hydrated print page via Chromium's own print-to-PDF."""
    reply: dict[str, bool] = {}
    page.pdfPrintingFinished.connect(lambda path, ok: reply.setdefault("success", ok))
    try:
        page.printToPdf(str(pdf_path), layout)
        if not _wait_until(lambda: "success" in reply, _PDF_TIMEOUT_MS):
            raise PrintError("the print job timed out")
    finally:
        page.pdfPrintingFinished.disconnect()
    if not reply.get("success") or not pdf_path.is_file():
        raise PrintError("could not produce the print preview")
