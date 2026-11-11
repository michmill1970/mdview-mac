"""In-app palette editor (thin Qt form over the pure palette model).

The dialog is deliberately simple, because the *model* does the work:

* one labelled row per colour key, in a fixed readable order;
* every edit produces a candidate :class:`~mdview.core.palette.Palette` plus a
  validation pass -- Save stays disabled with a tooltip listing **all** problems
  at once, never one per save attempt;
* each valid edit emits ``palette_previewed`` so the window re-themes live
  (no reload, no settings write);
* Save writes ``<data>/palettes/<id>.json``; the window watches that directory,
  so editor saves and hand edits take effect through the same path.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mdview.core.appearance import Appearance, AppearanceMode
from mdview.core.errors import PaletteValidationError
from mdview.core.palette import (
    OPTIONAL_COLOR_KEYS,
    Palette,
    PaletteRegistry,
    normalize_hex_color,
    slugify,
)
from mdview.core.paths import AppDir, app_dir

#: Display order + human labels for the editor form (every key in one place).
_COLOR_LABELS: tuple[tuple[str, str], ...] = (
    ("background", "Background"),
    ("surface", "Surface (tables, quotes)"),
    ("text", "Text"),
    ("muted_text", "Muted text"),
    ("heading", "Headings"),
    ("link", "Links"),
    ("link_hover", "Links (hover)"),
    ("accent", "Accent"),
    ("border", "Borders"),
    ("selection", "Selection"),
    ("selection_text", "Selection text"),
    ("code_bg", "Code background"),
    ("code_text", "Code text"),
    ("code_border", "Code border"),
    ("syntax_keyword", "Syntax: keyword"),
    ("syntax_string", "Syntax: string"),
    ("syntax_comment", "Syntax: comment"),
    ("syntax_number", "Syntax: number"),
    ("syntax_function", "Syntax: function"),
    ("syntax_type", "Syntax: type"),
    ("syntax_operator", "Syntax: operator"),
    ("syntax_invalid", "Syntax: error"),
    ("scrollbar", "Scrollbar"),
)


#: Shown while a built-in palette is selected: Save stays enabled, but the
#: result is a *user* file that shadows the built-in (the documented design).
_BUILTIN_HINT = "Built-in palette — saving creates a user copy"


def css_hex_to_qcolor(value: str) -> QColor:
    """Parse an mdview hex colour (CSS semantics) into a QColor.

    ``QColor`` treats 8-digit hex as ``#AARRGGBB``, but palette values -- and
    the CSS they become -- are ``#RRGGBBAA``.  Without the reorder a 20%-alpha
    selection colour would render at 3% alpha and the wrong hue.
    """
    text = value.strip()
    if len(text) == 9 and text[0] == "#" and all(c in "0123456789abcdef" for c in text[1:].lower()):
        text = f"#{text[7:9]}{text[1:7]}"  # RRGGBBAA -> AARRGGBB (Qt's order)
    return QColor(text)


class _SwatchButton(QPushButton):
    """A 24x18px colour chip over a checkerboard; clicking opens the picker.

    The checkerboard sits *under* the colour fill: translucent colours such as
    ``#0969da33`` (selection) were near-invisible on the light dialog
    background when painted over a plain base.
    """

    color_picked = Signal(str)

    _CHECKER_STEP = 4  # px squares, deliberately tiny so the chip still reads as one colour
    _CHECKER_DARK = QColor("#c8c8c8")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(28, 22))
        self.setToolTip("Pick a colour")
        self._value = ""
        self.clicked.connect(self._pick)

    def set_hex(self, value: str) -> None:
        self._value = value
        pixmap = QPixmap(24, 18)
        pixmap.fill(QColor("#ffffff"))
        painter = QPainter(pixmap)
        for row, y in enumerate(range(0, pixmap.height(), self._CHECKER_STEP)):
            for column, x in enumerate(range(0, pixmap.width(), self._CHECKER_STEP)):
                if (row + column) % 2:
                    self._checker_square(painter, x, y)
        color = css_hex_to_qcolor(value)
        if color.isValid() and color.alpha() > 0:
            painter.fillRect(pixmap.rect(), color)  # source-over: alpha blends with the checker
        painter.end()
        self.setIcon(QIcon(pixmap))

    def _checker_square(self, painter: QPainter, x: int, y: int) -> None:
        painter.fillRect(x, y, self._CHECKER_STEP, self._CHECKER_STEP, self._CHECKER_DARK)

    def _pick(self) -> None:
        # Read the raw hex, not the composited pixels: the checkerboard under
        # translucent colours would poison a pixel-sampled initial value.
        color = QColorDialog.getColor(css_hex_to_qcolor(self._value), self, "Choose colour")
        if color.isValid():
            # HexArgb would emit Qt's "#aarrggbb"; palettes/CSS speak #rrggbb(aa).
            self.color_picked.emit(color.name(QColor.NameFormat.HexRgb))


class _AppearanceCombo(QComboBox):
    """light / dark selector (``system`` is a *setting*, not a palette property)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.addItem("Light", "light")
        self.addItem("Dark", "dark")

    def appearance(self) -> Appearance:
        value = self.currentData()
        return "dark" if value == "dark" else "light"

    def set_appearance(self, appearance: AppearanceMode) -> None:
        self.setCurrentIndex(1 if appearance == "dark" else 0)


class PaletteEditor(QDialog):
    """Edit a palette and save it into the user palettes directory."""

    palette_saved = Signal(str)  # palette id
    palette_previewed = Signal(object)  # Palette

    def __init__(
        self, registry: PaletteRegistry, current: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("paletteEditor")
        self.setWindowTitle("Edit Palettes")
        self.resize(900, 660)
        self._registry = registry
        self._base = current
        self._rows: dict[str, tuple[QLineEdit, _SwatchButton]] = {}
        self.last_saved_path: str | None = None

        self._build_ui()
        self._select(current.id)

    # ------------------------------------------------------------------ ui --
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        root.addWidget(splitter, 1)

        self._list = QListWidget(splitter)
        self._list.setObjectName("paletteList")
        self._list.currentItemChanged.connect(lambda *_: self._load_selected())
        splitter.addWidget(self._list)

        form_host = QWidget(splitter)
        form = QVBoxLayout(form_host)

        top = QHBoxLayout()
        top.addWidget(QLabel("Name", form_host))
        self._name_edit = QLineEdit(form_host)
        self._name_edit.setObjectName("paletteName")
        self._name_edit.textEdited.connect(lambda _=None: self._on_edited())
        top.addWidget(self._name_edit, 1)
        top.addWidget(QLabel("Mode", form_host))
        self._appearance_combo = _AppearanceCombo(form_host)
        self._appearance_combo.currentIndexChanged.connect(lambda _=None: self._on_edited())
        top.addWidget(self._appearance_combo)
        form.addLayout(top)

        self._builtin_hint = QLabel("", form_host)
        self._builtin_hint.setObjectName("paletteBuiltinHint")
        self._builtin_hint.setWordWrap(True)
        self._builtin_hint.setVisible(False)
        form.addWidget(self._builtin_hint)

        self._errors = QLabel("", form_host)
        self._errors.setObjectName("paletteErrors")
        self._errors.setWordWrap(True)
        form.addWidget(self._errors)

        scroll = QScrollArea(form_host)
        scroll.setWidgetResizable(True)
        rows_host = QWidget(scroll)
        layout = QFormLayout(rows_host)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for key, label in _COLOR_LABELS:
            line = QLineEdit(rows_host)
            line.setObjectName(f"color_{key}")
            line.setPlaceholderText("#rrggbb")
            line.setMaxLength(9)
            line.editingFinished.connect(self._on_edited)
            swatch = _SwatchButton(rows_host)
            swatch.color_picked.connect(lambda value, k=key: self._set_color(k, value))
            row = QWidget(rows_host)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(line, 1)
            row_layout.addWidget(swatch)
            layout.addRow(f"{label}:", row)
            self._rows[key] = (line, swatch)
        scroll.setWidget(rows_host)
        form.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self._duplicate_button = QPushButton("Duplicate…", form_host)
        self._duplicate_button.setObjectName("paletteDuplicate")
        self._duplicate_button.clicked.connect(self._duplicate)
        self._revert_button = QPushButton("Revert", form_host)
        self._revert_button.setObjectName("paletteRevert")
        self._revert_button.setEnabled(False)
        self._revert_button.clicked.connect(self._load_selected)
        buttons.addWidget(self._duplicate_button)
        buttons.addWidget(self._revert_button)
        buttons.addStretch(1)
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close,
            form_host,
        )
        self._button_box.accepted.connect(self._save)
        self._button_box.rejected.connect(self.reject)
        buttons.addWidget(self._button_box)
        form.addLayout(buttons)

        splitter.addWidget(form_host)
        splitter.setStretchFactor(1, 1)
        self._fill_list()

    def _fill_list(self) -> None:
        self._list.clear()
        for palette_id, name in self._registry.names():
            palette = self._registry.get(palette_id)
            suffix = "  (built-in)" if palette.builtin else ""
            item = QListWidgetItem(f"{name}{suffix}", self._list)
            item.setData(Qt.ItemDataRole.UserRole, palette_id)
            item.setToolTip(str(palette.source_path) if palette.source_path else "built-in")

    # -------------------------------------------------------------- state --
    def _select(self, palette_id: str) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == palette_id:
                self._list.setCurrentItem(item)
                return

    def _selected_id(self) -> str | None:
        item = self._list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _load_selected(self) -> None:
        palette_id = self._selected_id()
        if palette_id is None:
            return
        self._base = self._registry.get(palette_id)
        self._name_edit.blockSignals(True)
        self._name_edit.setText(self._base.name)
        self._name_edit.blockSignals(False)
        self._appearance_combo.blockSignals(True)
        self._appearance_combo.set_appearance(self._base.appearance)
        self._appearance_combo.blockSignals(False)
        for key, (line, swatch) in self._rows.items():
            value = self._base.colors.get(key, "")
            line.blockSignals(True)
            line.setText(value)
            line.blockSignals(False)
            swatch.set_hex(value)
        self._errors.setText("")
        self._sync_chrome()
        self._button_box.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)

    def _sync_chrome(self) -> None:
        """Window title + built-in hint follow the selected palette."""
        self.setWindowTitle(f"Edit Palette — {self._base.name}")
        self._builtin_hint.setText(_BUILTIN_HINT if self._base.builtin else "")
        self._builtin_hint.setVisible(self._base.builtin)

    def _on_edited(self) -> None:
        self._revert_button.setEnabled(True)
        self._sync_swatches()
        candidate, problems = self._candidate()
        self._validate(problems)
        if not problems:
            self.palette_previewed.emit(candidate)

    def _sync_swatches(self) -> None:
        """Repaint every swatch from its field, as typed (not just via the picker)."""
        for _key, (line, swatch) in self._rows.items():
            try:
                swatch.set_hex(normalize_hex_color(line.text()))
            except ValueError:
                swatch.set_hex("")  # empty or invalid -> bare checkerboard

    def _candidate(self) -> tuple[Palette, list[str]]:
        """Build the edited palette and report every problem with it."""
        colors: dict[str, str] = {}
        problems: list[str] = []
        for key, (line, _swatch) in self._rows.items():
            text = line.text().strip()
            if not text:
                if key not in OPTIONAL_COLOR_KEYS:
                    problems.append(f"{key}: required")
                continue
            try:
                colors[key] = normalize_hex_color(text)
            except ValueError:
                problems.append(f"{key}: {text!r} is not a hex colour")
        name = self._name_edit.text().strip()
        if not name:
            problems.append("name: required")
            name = self._base.name
        candidate = replace(
            self._base,
            id=slugify(name),
            name=name,
            appearance=self._appearance_combo.appearance(),
            source_path=None,
            builtin=False,
        ).with_colors(**colors)
        try:  # authoritative schema check (missing keys, bad id, …)
            candidate = Palette.from_dict(candidate.to_dict(), builtin=False)
        except PaletteValidationError as exc:
            problems.extend(exc.problems)
        return candidate, problems

    def _validate(self, problems: list[str]) -> None:
        save_button = self._button_box.button(QDialogButtonBox.StandardButton.Save)
        if problems:
            shown = "; ".join(problems[:3])
            more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
            self._errors.setText(f"Cannot save: {shown}{more}")
            save_button.setEnabled(False)
            save_button.setToolTip("\n".join(problems))
        else:
            self._errors.setText("")
            save_button.setEnabled(True)
            save_button.setToolTip("")

    # ------------------------------------------------------------ actions --
    def _set_color(self, key: str, value: str) -> None:
        line, _swatch = self._rows[key]
        line.setText(value)
        self._on_edited()  # keeps the swatch, validation, and preview in step

    def _duplicate(self) -> None:
        candidate, problems = self._candidate()
        if problems:
            QMessageBox.warning(self, "Fix the palette first", "\n".join(problems))
            return
        self._name_edit.setText(f"{candidate.name} Copy")
        self._on_edited()

    def _save(self) -> None:
        candidate, problems = self._candidate()
        if problems:
            QMessageBox.warning(self, "Cannot save palette", "\n".join(problems))
            return
        try:
            path = candidate.write(app_dir(AppDir.PALETTES))
        except Exception as exc:
            QMessageBox.critical(self, "Could not save palette", str(exc))
            return
        self._registry.upsert(candidate)
        self._base = candidate
        self.last_saved_path = str(path)
        self._fill_list()
        self._select(candidate.id)
        self.palette_saved.emit(candidate.id)
