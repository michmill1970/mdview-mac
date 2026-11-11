"""The main window: menus, shortcuts, tabs, theme wiring, watchers.

This is the only module that knows both the pure core and Qt widgets, and its
job is translation, not logic: which palette, what font size, which tab is
active, is this file stale -- all decided in ``mdview.core`` and merely applied
here.  That is what keeps the GUI thin enough to test headlessly.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QFileSystemWatcher, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mdview.core.appearance import (
    Appearance,
    AppearanceMode,
    appearance_mode_values,
)
from mdview.core.document import Document, error_page_markdown, inspect_openable
from mdview.core.errors import AssetError, DocumentError
from mdview.core.fonts import FONT_SIZE_MAX_PX, FONT_SIZE_MIN_PX, FontSize
from mdview.core.palette import Palette, PaletteRegistry
from mdview.core.paths import AppDir, app_dir, builtin_palettes_dir, settings_path
from mdview.core.settings import Settings, save_settings
from mdview.core.tabs import Tab, TabId, TabModel
from mdview.core.theme import PaletteSelection, default_palette_ids, selection_for
from mdview.render.page import PageOptions, RenderedPage, render_page
from mdview.viewer.content_view import ContentView
from mdview.viewer.palette_editor import PaletteEditor
from mdview.viewer.scheme import MdViewSchemeHandler, PageStore
from mdview.viewer.tab_view import TabView

_EMPTY_HINT = """<h2>MDView</h2>
<p>Open a Markdown file to get started.</p>
<p><kbd>&#8984;O</kbd> Open &nbsp; <kbd>&#8984;=</kbd> / <kbd>&#8984;-</kbd> text size
&nbsp; <kbd>&#8984;0</kbd> reset</p>"""

_MARKDOWN_FILTER = "Markdown (*.md *.markdown);;All files (*)"


class MainWindow(QMainWindow):
    """One window, N tabs, one theme."""

    def __init__(
        self,
        settings: Settings,
        registry: PaletteRegistry,
        scheme_handler: MdViewSchemeHandler,
        profile: QWebEngineProfile,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("mainWindow")
        self.setWindowTitle("MDView")
        self.resize(1040, 780)
        if sys.platform == "darwin":
            # macOS chrome (DESIGN.md "macOS chrome notes"): the unified
            # title/toolbar style renders the window title left-aligned next to
            # the traffic lights.  Qt defaults this to off, but tabbed windows
            # have historically opted in; pin it explicitly and do not chase
            # further title-alignment cosmetics.
            self.setUnifiedTitleAndToolBarOnMac(False)

        self._registry = registry
        self._scheme_handler = scheme_handler
        self._profile = profile
        self._settings = resolve_initial_settings(settings.normalized(), registry)
        self._system_appearance: Appearance = system_appearance(self)
        self._fonts = FontSize(self._settings.font_size_px)
        self._actions: dict[str, QAction] = {}

        self._model = TabModel(on_change=self._sync_tabs)
        self._tab_view = TabView(self._model, self._make_view, self)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._make_empty_state())
        self._stack.addWidget(self._tab_view)
        self.setCentralWidget(self._stack)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._file_changed)
        self._palette_dir = app_dir(AppDir.PALETTES)
        self._palette_watcher = QFileSystemWatcher(self)
        self._palette_watcher.addPath(str(self._palette_dir))
        self._palette_watcher.directoryChanged.connect(self.reload_palettes)

        self._make_actions()
        self._make_menus()
        self._make_toolbar()
        self._sync_theme_menus()
        self._update_font_widgets()

        # QStyleHints is application-wide; colorSchemeChanged fires when macOS
        # flips light/dark (Qt >= 6.8).
        QApplication.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)

    # ------------------------------------------------------------- setup --
    def _make_view(self) -> ContentView:
        return ContentView(self._profile, self)

    def _make_actions(self) -> None:
        handlers: dict[str, Callable[[], object]] = {
            "open": self._prompt_open,
            "closeTab": self._close_current_tab,
            "reload": self._reload_current,
            "zoomIn": lambda: self._change_font(1),
            "zoomOut": lambda: self._change_font(-1),
            "zoomReset": lambda: self._set_font_size(16),
            "editPalettes": self._open_palette_editor,
            "nextTab": lambda: self._model.activate_next(),
            "previousTab": lambda: self._model.activate_previous(),
            "reveal": self._reveal_current_folder,
            "showHelp": self._show_help,
        }
        specs: tuple[tuple[str, str, QKeySequence | None], ...] = (
            ("open", "&Open…", QKeySequence(QKeySequence.StandardKey.Open)),
            ("closeTab", "&Close Tab", QKeySequence(QKeySequence.StandardKey.Close)),
            ("reload", "&Reload", QKeySequence("Ctrl+R")),
            ("zoomOut", "Zoom &Out", QKeySequence(QKeySequence.StandardKey.ZoomOut)),
            ("zoomIn", "Zoom &In", QKeySequence(QKeySequence.StandardKey.ZoomIn)),
            ("zoomReset", "Actual &Size", QKeySequence("Ctrl+0")),
            ("editPalettes", "&Edit Palettes…", QKeySequence("Ctrl+Shift+P")),
            ("previousTab", "Select &Previous Tab", QKeySequence("Ctrl+Shift+Tab")),
            ("nextTab", "Select &Next Tab", QKeySequence("Ctrl+Tab")),
            ("reveal", "Reveal in &Finder", QKeySequence("Ctrl+Shift+F")),
            # ⌘? on macOS, F1 elsewhere; the conventional macOS help-menu entry.
            ("showHelp", "MDView &Help", QKeySequence(QKeySequence.StandardKey.HelpContents)),
        )
        for name, text, shortcut in specs:
            action = QAction(text, self)
            if shortcut is not None and not shortcut.isEmpty():
                action.setShortcut(shortcut)
            action.triggered.connect(handlers[name])
            self._actions[name] = action
        for index in range(1, 10):  # Cmd+1..Cmd+9
            action = QAction(f"Tab &{index}", self)
            action.setShortcut(QKeySequence(f"Ctrl+{index}"))
            action.triggered.connect(lambda _=False, i=index - 1: self._model.activate_index(i))
            self._actions[f"tab{index}"] = action

    def _make_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        file_menu.addAction(self._actions["open"])
        file_menu.addSeparator()
        file_menu.addAction(self._actions["closeTab"])

        edit_menu = bar.addMenu("&Edit")
        # PySide6 6.12 does not bind Qt's Copy/SelectAll menu roles; the
        # heuristic role lets the macOS platform menu pick these items up by
        # their standard text (and they stay normal actions elsewhere).
        for text in ("&Copy", "Select &All"):
            action = QAction(text, self)
            action.setMenuRole(QAction.MenuRole.TextHeuristicRole)
            edit_menu.addAction(action)

        view_menu = bar.addMenu("&View")
        view_menu.addAction(self._actions["reload"])
        view_menu.addSeparator()
        view_menu.addAction(self._actions["zoomOut"])
        view_menu.addAction(self._actions["zoomIn"])
        view_menu.addAction(self._actions["zoomReset"])
        view_menu.addSeparator()
        view_menu.addAction(self._actions["previousTab"])
        view_menu.addAction(self._actions["nextTab"])
        jump_menu = view_menu.addMenu("&Jump to Tab")
        for index in range(1, 10):
            jump_menu.addAction(self._actions[f"tab{index}"])
        view_menu.addSeparator()
        view_menu.addAction(self._actions["reveal"])

        appearance_menu = bar.addMenu("&Appearance")
        self._appearance_group = QActionGroup(self)
        self._appearance_group.setExclusive(True)
        self._appearance_actions: dict[AppearanceMode, QAction] = {}
        mode_labels: dict[AppearanceMode, str] = {
            "system": "Follow &System",
            "light": "&Light",
            "dark": "&Dark",
        }
        for mode in appearance_mode_values():
            action = QAction(mode_labels[mode], self)
            action.setCheckable(True)
            action.triggered.connect(lambda _=False, m=mode: self._set_appearance_mode(m))
            self._appearance_group.addAction(action)
            self._appearance_actions[mode] = action
            appearance_menu.addAction(action)

        palettes_menu = bar.addMenu("Pa&lettes")
        self._light_palette_menu = palettes_menu.addMenu("&Light Palette")
        self._dark_palette_menu = palettes_menu.addMenu("&Dark Palette")
        palettes_menu.addSeparator()
        palettes_menu.addAction(self._actions["editPalettes"])
        self._build_palette_menus()

        # Native macOS convention (Phase 6): Qt6 exposes *no* public
        # WindowRole/HelpRole -- QCocoaMenuBar instead detects these menus by
        # their titles ("Window"/"Help", localized) and moves them to their
        # standard slots: the Window menu becomes the AppKit windows menu
        # (window list + "Enter Full Screen" appended by Cocoa) and the Help
        # menu gains the Spotlight search field.  So the titles matter; the
        # conventional items are ours on every platform.
        window_menu = bar.addMenu("&Window")
        window_menu.setObjectName("windowMenu")
        minimize = QAction("Mi&nimize", self)
        minimize.setShortcut(QKeySequence("Ctrl+M"))  # ⌘M on macOS (Qt Control==Command)
        minimize.triggered.connect(self.showMinimized)
        window_menu.addAction(minimize)
        zoom = QAction("&Zoom", self)
        zoom.triggered.connect(self._toggle_zoom)
        window_menu.addAction(zoom)
        window_menu.addSeparator()
        front = QAction("Bring &All to Front", self)
        front.triggered.connect(self._bring_all_to_front)
        window_menu.addAction(front)

        help_menu = bar.addMenu("&Help")
        help_menu.setObjectName("helpMenu")
        help_menu.addAction(self._actions["showHelp"])

    def _build_palette_menus(self) -> None:
        menus: tuple[tuple[QMenu, Appearance], ...] = (
            (self._light_palette_menu, "light"),
            (self._dark_palette_menu, "dark"),
        )
        for menu, appearance in menus:
            menu.clear()
            group = QActionGroup(menu)
            group.setExclusive(True)
            current = (
                self._settings.palette_light
                if appearance == "light"
                else self._settings.palette_dark
            )
            for palette_id, name in self._registry.names(appearance=appearance):
                action = QAction(name, menu)
                action.setCheckable(True)
                action.setChecked(palette_id == current)
                action.triggered.connect(
                    lambda _=False, pid=palette_id, a=appearance: self._set_palette(a, pid)
                )
                group.addAction(action)
                menu.addAction(action)

    def _make_toolbar(self) -> None:
        """The UI control for text size (requirement: shortcuts *and* a control).

        Phase 6 macOS field report: a bare ``QToolBar`` with text actions sits
        its first bezel flush against the window edge, and the bottom-bar
        chrome clipped the "Zoom Out" label ("oom Out").  So the bar hosts one
        widget whose layout owns the margins: nothing can clip or crowd the
        edge at any window width (geometry-tested at 640 px).
        """
        toolbar = QToolBar("Text Size", self)
        toolbar.setObjectName("zoomToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, toolbar)

        host = QWidget(toolbar)
        host.setObjectName("zoomBar")
        row = QHBoxLayout(host)
        # Vertical margins stay small on purpose: every bar pixel shrinks the
        # web view, and the demo below-fold shot needs quote→diagram to fit
        # one viewport (shot_below_fold measures the feasible scroll window).
        row.setContentsMargins(16, 2, 16, 2)
        row.setSpacing(8)

        self._zoom_out_button = QToolButton(host)
        self._zoom_out_button.setObjectName("zoomOutButton")
        self._zoom_out_button.setDefaultAction(self._actions["zoomOut"])
        self._zoom_out_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._zoom_out_button.setAutoRaise(True)  # flat pill, keeps the bar slim
        self._zoom_out_button.setToolTip("Decrease text size (⌘-)")

        self._font_spin = QSpinBox(host)
        self._font_spin.setObjectName("fontSizeSpin")
        self._font_spin.setRange(FONT_SIZE_MIN_PX, FONT_SIZE_MAX_PX)
        self._font_spin.setSuffix(" px")
        self._font_spin.setToolTip("Text size (⌘= bigger, ⌘- smaller, ⌘0 reset)")
        self._font_spin.setValue(self._fonts.size_px)
        self._font_spin.valueChanged.connect(self._spin_changed)

        self._zoom_in_button = QToolButton(host)
        self._zoom_in_button.setObjectName("zoomInButton")
        self._zoom_in_button.setDefaultAction(self._actions["zoomIn"])
        self._zoom_in_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._zoom_in_button.setAutoRaise(True)
        self._zoom_in_button.setToolTip("Increase text size (⌘=)")

        row.addWidget(self._zoom_out_button)
        row.addWidget(self._font_spin)
        row.addWidget(self._zoom_in_button)
        row.addStretch(1)
        toolbar.addWidget(host)

    def _make_empty_state(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        label = QLabel(_EMPTY_HINT, widget)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setTextFormat(Qt.TextFormat.RichText)
        button = QPushButton("Open Markdown…", widget)
        button.setObjectName("emptyStateOpenButton")
        button.clicked.connect(self._prompt_open)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)
        return widget

    # -------------------------------------------------------- public API --
    def open_paths(self, paths: Iterable[Path | str], *, quiet_missing: bool = False) -> list[str]:
        """Open files; every failure opens an error tab instead of failing silently.

        Phase 6: ``mdview open a.md missing.md`` used to swallow the missing
        file.  Now each path that cannot be opened (not found, a directory,
        unreadable, binary) gets a themed error tab naming the path and the
        reason, so the successful tabs and the failure are both visible.
        Readable text files open *as Markdown whatever their suffix* — a
        ``.txt`` is friendlier to view than to refuse (DESIGN.md "Open-file
        policy").  ``quiet_missing`` is for startup session restore only: a
        path that vanished since last run is skipped, not tabified.

        Returns one human-readable reason per failure (the CLI turns a
        non-empty list into a non-zero exit code).
        """
        problems: list[str] = []
        touched = False
        for raw in paths:
            path = Path(raw)
            reason = inspect_openable(path)
            if reason is not None:
                if quiet_missing and reason.startswith("file not found:"):
                    continue
                problems.append(reason)
                tab = self._model.open_error(path, reason)
            else:
                try:
                    tab = self._model.open_path(path)
                except DocumentError as exc:  # raced with the check; still visible
                    reason = str(exc)
                    problems.append(reason)
                    tab = self._model.open_error(path, reason)
            self._prepare_tab(tab)
            touched = True
        if problems:
            self.statusBar().showMessage("; ".join(problems[:3]), 8000)
        if touched:
            self._persist_settings()
        return problems

    def tab_count(self) -> int:
        return self._model.count

    def current_view(self) -> ContentView | None:
        return self._tab_view.current_view()

    def current_tab(self) -> Tab | None:
        return self._model.active

    def view_for(self, tab_id: TabId) -> ContentView | None:
        return self._tab_view.view_for(tab_id)

    def current_font_size(self) -> int:
        return self._fonts.size_px

    def selection(self) -> PaletteSelection:
        """The resolved palette + appearance (also used by the tests)."""
        return selection_for(self._registry, self._settings, self._system_appearance)

    def set_appearance_mode(self, mode: AppearanceMode) -> None:
        """Public entry point (menu, tests, future deep links): pin or unpin."""
        self._set_appearance_mode(mode)

    def _system_scheme_changed(self, _scheme: object) -> None:
        """macOS flipped light/dark while ``Follow System`` is on."""
        fresh = system_appearance(self)
        if fresh == self._system_appearance:
            return
        self._system_appearance = fresh
        if self._settings.appearance_mode == "system":
            self._apply_theme()

    def render_options(self, tab: Tab) -> PageOptions:
        """The exact PageOptions a tab renders with."""
        selection = self.selection()
        return PageOptions(
            palette=selection.palette,
            font_size_px=self._fonts.size_px,
            appearance=selection.appearance,
            title=tab.title,
            document_dir=tab.document.path.parent,
            page_id=page_id_for(tab.tab_id),
            smooth_scrolling=self._settings.smooth_scrolling,
        )

    def render_tab(self, tab: Tab) -> RenderedPage:
        """Render one tab's document -- or its error page -- and show it.

        Error tabs (``tab.error``) render through the *same* page pipeline as
        documents, so failed opens read like every other tab (same palette,
        fonts, theme switching) instead of being a second, uglier UI.
        """
        if tab.error is not None:
            source = error_page_markdown(tab.document.path, tab.error)
        else:
            source = tab.document.read()
        page = render_page(source, self.render_options(tab))
        if tab.error is None:
            self._scheme_handler.add_document_root(tab.document.path.parent)
        view = self._tab_view.view_for(tab.tab_id)
        if view is not None:
            view.show_page(page, page_id=page_id_for(tab.tab_id))
        return page

    # -------------------------------------------------------- font size --
    def _change_font(self, direction: int) -> None:
        changed = (
            self._fonts.increase(direction) if direction > 0 else self._fonts.decrease(-direction)
        )
        if changed:
            self._after_font_change()

    def _set_font_size(self, size_px: int) -> None:
        if self._fonts.set(size_px):
            self._after_font_change()

    def _spin_changed(self, value: int) -> None:
        if self._fonts.set(value):
            self._after_font_change()

    def _after_font_change(self) -> None:
        self._update_font_widgets()
        view = self._tab_view.current_view()
        if view is not None:
            view.set_font_size(self._fonts.size_px)
        self._settings = _with_fields(self._settings, font_size_px=self._fonts.size_px)
        self._persist_settings()

    def _update_font_widgets(self) -> None:
        self._font_spin.blockSignals(True)
        self._font_spin.setValue(self._fonts.size_px)
        self._font_spin.blockSignals(False)
        self._actions["zoomIn"].setEnabled(self._fonts.can_increase)
        self._actions["zoomOut"].setEnabled(self._fonts.can_decrease)
        self._actions["zoomReset"].setEnabled(not self._fonts.is_default)

    # ---------------------------------------------------------- palettes --
    def _set_appearance_mode(self, mode: AppearanceMode) -> None:
        self._settings = _with_fields(self._settings, appearance_mode=mode)
        self._apply_theme()
        self._persist_settings()

    def _set_palette(self, appearance: Appearance, palette_id: str) -> None:
        field = "palette_light" if appearance == "light" else "palette_dark"
        self._settings = _with_fields(self._settings, **{field: palette_id})
        self._apply_theme()
        self._persist_settings()

    def reload_palettes(self) -> None:
        """Live-reload user palettes from disk (editor save, Finder edit, sync tool)."""
        self._registry = PaletteRegistry.from_dirs(builtin_palettes_dir(), self._palette_dir)
        self._settings = resolve_initial_settings(self._settings, self._registry)
        self._build_palette_menus()
        self._apply_theme()
        self.statusBar().showMessage("Palettes reloaded", 2500)

    def _open_palette_editor(self) -> None:
        editor = PaletteEditor(self._registry, self.selection().palette, self)
        editor.palette_saved.connect(self.reload_palettes)
        editor.palette_previewed.connect(self._preview_palette)
        editor.exec()

    def _preview_palette(self, palette: Palette) -> None:
        """Live preview while editing: re-theme open tabs, touch no settings."""
        for tab in self._model.tabs:
            view = self._tab_view.view_for(tab.tab_id)
            if view is not None:
                view.reapply_theme(palette, self._fonts.size_px, appearance=palette.appearance)

    # -------------------------------------------------------------- files --
    def _prompt_open(self) -> None:
        active = self._model.active
        start = str(active.document.path.parent) if active else str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "Open Markdown", start, _MARKDOWN_FILTER)
        if paths:
            self.open_paths(paths)

    def _close_current_tab(self) -> None:
        active = self._model.active
        if active is not None:
            self._model.close(active.tab_id)
            self._persist_settings()

    def _reload_current(self) -> None:
        tab = self._model.active
        if tab is not None:
            self.render_tab(tab)

    def _reveal_current_folder(self) -> None:
        tab = self._model.active
        if tab is not None and sys.platform == "darwin":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(tab.document.path.parent)))

    def _show_help(self) -> None:
        """Help ▸ MDView Help: the shortcut sheet (macOS keeps Help for the app)."""
        QMessageBox.information(
            self,
            "MDView Help",
            "<p><b>MDView</b> renders Markdown offline: tabs, palettes, KaTeX, Mermaid.</p>"
            "<ul>"
            "<li><kbd>⌘O</kbd> Open &nbsp; <kbd>⌘W</kbd> Close tab &nbsp; "
            "<kbd>⌘R</kbd> Reload</li>"
            "<li><kbd>⌘=</kbd> / <kbd>⌘-</kbd> Text size &nbsp; <kbd>⌘0</kbd> Reset"
            " &nbsp; (zoom bar: bottom of the window)</li>"
            "<li><kbd>⌘1…⌘9</kbd> Jump to tab &nbsp; <kbd>⌃⇧Tab</kbd> / <kbd>⌃Tab</kbd> Cycle</li>"
            "<li>Appearance and palettes live in their own menus.</li>"
            "<li>Files changed on disk reload themselves automatically.</li>"
            "</ul>",
        )

    def _prepare_tab(self, tab: Tab) -> None:
        path = str(tab.document.path)
        if tab.error is None and path not in self._watcher.files():
            self._watcher.addPath(path)  # error paths typically do not exist to watch
        try:
            self.render_tab(tab)
        except AssetError as exc:
            QMessageBox.warning(self, "Bundled assets missing", str(exc))

    def _file_changed(self, path: str) -> None:
        """External edit (the user's editor saved) -> re-render affected tabs."""
        affected = [tab for tab in self._model.tabs if tab.document.matches(path)]
        for tab in affected:
            try:
                tab.document = Document.open(tab.document.path)
            except DocumentError:
                continue  # deleted mid-save; keep showing the last good render
            self.render_tab(tab)
        if affected:
            # Many editors write a temp file and rename, which drops the watch.
            if path not in self._watcher.files() and Path(path).is_file():
                self._watcher.addPath(path)
            self.statusBar().showMessage(f"Reloaded {Path(path).name}", 3000)

    # ------------------------------------------------------- window menu --
    def _toggle_zoom(self) -> None:
        """The non-macOS Window ▸ Zoom: AppKit does this natively on macOS."""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _bring_all_to_front(self) -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QWidget) and widget.isVisible():
                widget.raise_()
        self.raise_()
        self.activateWindow()

    # ---------------------------------------------------------------- Qt --
    def _sync_tabs(self) -> None:
        self._tab_view.sync()
        self._stack.setCurrentIndex(1 if self._model.count else 0)
        for tab_id in [t for t in self._tab_view.view_ids() if self._model.tab(t) is None]:
            PageStore.drop(page_id_for(tab_id))
        active = self._model.active
        self.setWindowTitle(f"{active.title} — MDView" if active else "MDView")

    def closeEvent(self, event: QCloseEvent) -> None:
        active = self._model.active
        self._settings = _with_fields(
            self._settings,
            font_size_px=self._fonts.size_px,
            open_files=self._model.open_paths(),
            active_tab_path=None if active is None else active.document.key,
        )
        self._persist_settings()
        super().closeEvent(event)

    # --------------------------------------------------------- internals --
    def _apply_theme(self) -> None:
        """Re-theme every open tab in place (no reload, no flicker)."""
        selection = self.selection()
        for tab in self._model.tabs:
            view = self._tab_view.view_for(tab.tab_id)
            if view is not None:
                view.reapply_theme(
                    selection.palette, self._fonts.size_px, appearance=selection.appearance
                )
        self._sync_theme_menus()

    def _sync_theme_menus(self) -> None:
        action = self._appearance_actions.get(self._settings.appearance_mode)
        if action is not None:
            action.setChecked(True)
        for menu, palette_id in (
            (self._light_palette_menu, self._settings.palette_light),
            (self._dark_palette_menu, self._settings.palette_dark),
        ):
            for action in menu.actions():
                if action.isCheckable() and action.text() in {
                    name for pid, name in self._registry.names() if pid == palette_id
                }:
                    action.setChecked(True)

    def _persist_settings(self) -> None:
        try:
            save_settings(self._settings, settings_path())
        except OSError as exc:
            self.statusBar().showMessage(f"Could not save settings: {exc}", 5000)


def page_id_for(tab_id: TabId) -> str:
    """The ``mdview://page/<id>`` key of a tab (one place, used by two modules)."""
    return f"tab-{tab_id}"


def _with_fields(settings: Settings, **changes: object) -> Settings:
    # Values are pre-normalised by callers; ``Any`` keeps dataclasses.replace
    # happy without leaking Any into the public signature.
    return replace(settings, **cast("dict[str, Any]", changes)).normalized()


def system_appearance(widget: QWidget) -> Appearance:
    """Map Qt's colour scheme onto our model (offscreen/Linux reports Unknown)."""
    scheme = QApplication.styleHints().colorScheme()
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def resolve_initial_settings(settings: Settings, registry: PaletteRegistry) -> Settings:
    """Point at palettes that actually exist (fresh install, deleted preset)."""
    defaults = default_palette_ids(registry)
    light = settings.palette_light if registry.find(settings.palette_light) else defaults["light"]
    dark = settings.palette_dark if registry.find(settings.palette_dark) else defaults["dark"]
    return _with_fields(settings, palette_light=light, palette_dark=dark)
