"""Headless GUI behaviour (offscreen platform): tabs, theme, fonts, settings.

These construct the real ``MainWindow`` through the production bootstrap
(``ensure_bundled_assets=False`` + the seeded vendor cache, so no network),
but never load a page into the view -- they assert on model state, the page
store, and the settings file, which is exactly where the app's logic lives.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QTabBar, QToolBar

from mdview.core.palette import PaletteRegistry
from mdview.core.paths import AppDir, app_dir
from mdview.viewer.application import AppContext, bootstrap
from mdview.viewer.main_window import MainWindow, page_id_for
from mdview.viewer.palette_editor import PaletteEditor
from mdview.viewer.scheme import PageStore

pytestmark = [pytest.mark.gui, pytest.mark.webengine]


@pytest.fixture
def ctx(seeded_vendor: Path) -> object:
    context = bootstrap(["mdview-test"], ensure_bundled_assets=False)
    yield context
    context.window.close()


def _settings_file(context: AppContext) -> dict[str, object]:
    return json.loads(context.settings_path.read_text(encoding="utf-8"))


class TestTabs:
    def test_open_close_and_empty_state(
        self, ctx: AppContext, demo_file: Path, tmp_path: Path
    ) -> None:
        window: MainWindow = ctx.window
        assert window._stack.currentIndex() == 0  # empty state
        second = tmp_path / "second.md"
        second.write_text("# Second\n", encoding="utf-8")
        window.open_paths([demo_file, second])
        assert window.tab_count() == 2
        assert window._stack.currentIndex() == 1
        assert window.current_view() is not None
        window._actions["closeTab"].trigger()
        assert window.tab_count() == 1
        window._actions["closeTab"].trigger()
        assert window.tab_count() == 0
        assert window._stack.currentIndex() == 0

    def test_opened_tab_renders_into_page_store(self, ctx: AppContext, demo_file: Path) -> None:
        window = ctx.window
        window.open_paths([demo_file])
        tab = window.current_tab()
        assert tab is not None
        html = PageStore.get(page_id_for(tab.tab_id))
        assert html is not None
        assert "Demo Document" in html

    def test_unsupported_file_only_warns(self, ctx: AppContext, tmp_path: Path) -> None:
        bogus = tmp_path / "data.txt"
        bogus.write_text("x", encoding="utf-8")
        ctx.window.open_paths([bogus])
        assert ctx.window.tab_count() == 0
        assert "not a Markdown file" in ctx.window.statusBar().currentMessage()

    def test_external_edit_rerenders(self, ctx: AppContext, demo_file: Path) -> None:
        window = ctx.window
        window.open_paths([demo_file])
        tab = window.current_tab()
        assert tab is not None
        demo_file.write_text(
            demo_file.read_text(encoding="utf-8") + "\nMARKER-XYZ\n", encoding="utf-8"
        )
        window._file_changed(str(demo_file))
        html = PageStore.get(page_id_for(tab.tab_id))
        assert html is not None
        assert "MARKER-XYZ" in html


class TestAppearanceAndPalettes:
    def test_palettes_menu_title(self, ctx: AppContext) -> None:
        """The Palettes menu must *display* "Palettes" (ampersand is the mnemonic)."""
        menus = {
            menu.title(): menu
            for action in ctx.window.menuBar().actions()
            if (menu := action.menu()) is not None
        }
        palettes = [title for title in menus if title.replace("&", "").startswith("Palett")]
        assert palettes == ["Pa&lettes"]
        assert palettes[0].replace("&", "") == "Palettes"

    def test_appearance_mode_roundtrip(self, ctx: AppContext) -> None:
        window = ctx.window
        window.set_appearance_mode("dark")
        assert window.selection().appearance == "dark"
        assert _settings_file(ctx)["appearance_mode"] == "dark"
        window.set_appearance_mode("light")
        assert window.selection().appearance == "light"
        window.set_appearance_mode("system")
        assert _settings_file(ctx)["appearance_mode"] == "system"

    def test_palette_choice_per_appearance(self, ctx: AppContext) -> None:
        window = ctx.window
        window.set_appearance_mode("dark")
        window._set_palette("dark", "dracula-dark")
        assert window.selection().palette.id == "dracula-dark"
        assert _settings_file(ctx)["palette_dark"] == "dracula-dark"

    def test_deleted_palette_falls_back(self, ctx: AppContext) -> None:
        from mdview.core.settings import Settings
        from mdview.viewer.main_window import resolve_initial_settings

        fixed = resolve_initial_settings(Settings(palette_light="ghost-theme"), ctx.registry)
        assert fixed.palette_light == "github-light"


class TestFontControls:
    def test_spin_shortcuts_and_reset(self, ctx: AppContext) -> None:
        window = ctx.window
        window._font_spin.setValue(20)
        assert window.current_font_size() == 20
        window._actions["zoomIn"].trigger()
        assert window.current_font_size() == 21
        window._actions["zoomReset"].trigger()
        assert window.current_font_size() == 16
        assert window._font_spin.value() == 16
        assert _settings_file(ctx)["font_size_px"] == 16

    def test_limits_disable_actions(self, ctx: AppContext) -> None:
        window = ctx.window
        window._font_spin.setValue(window._font_spin.maximum())
        assert window._actions["zoomIn"].isEnabled() is False
        assert window._actions["zoomOut"].isEnabled() is True


class TestSettingsPersistence:
    def test_close_remembers_open_files(self, ctx: AppContext, demo_file: Path) -> None:
        window = ctx.window
        window.open_paths([demo_file])
        window.close()
        data = _settings_file(ctx)
        assert any(demo_file.name in str(entry) for entry in data["open_files"])


class TestPaletteEditor:
    def test_save_writes_user_palette(self, ctx: AppContext, qtbot: object) -> None:
        window = ctx.window
        editor = PaletteEditor(ctx.registry, ctx.registry.get("github-light"), window)
        with qtbot.waitSignal(editor.palette_saved, timeout=5000) as blocker:  # type: ignore[attr-defined]
            editor._name_edit.setText("Nord Night")
            editor._rows["background"][0].setText("#111111")
            editor._on_edited()
            editor._save()
        assert blocker.args == ["nord-night"]
        saved = app_dir(AppDir.PALETTES) / "nord-night.json"
        assert saved.is_file()
        from mdview.core.paths import builtin_palettes_dir

        reloaded = PaletteRegistry.from_dirs(builtin_palettes_dir(), app_dir(AppDir.PALETTES))
        palette = reloaded.get("nord-night")
        assert palette.colors["background"] == "#111111"
        assert palette.builtin is False

    def test_invalid_color_blocks_save(self, ctx: AppContext) -> None:
        window = ctx.window
        editor = PaletteEditor(ctx.registry, ctx.registry.get("github-dark"), window)
        editor._rows["text"][0].setText("nonsense")
        editor._on_edited()
        save = editor._button_box.button(QDialogButtonBox.StandardButton.Save)
        assert not save.isEnabled()
        assert "Cannot save" in editor._errors.text()


class TestPaletteEditorSwatchesAndChrome:
    """Widget-level checks driven by the field + editingFinished, like a user."""

    def _editor(self, registry: object, qtbot: object, palette_id: str) -> PaletteEditor:
        editor = PaletteEditor(registry, registry.get(palette_id))  # type: ignore[attr-defined]
        qtbot.addWidget(editor)  # type: ignore[attr-defined]
        return editor

    def test_typing_hex_updates_swatch(self, registry: PaletteRegistry, qtbot: object) -> None:
        """The PO's bug: page preview went orange, the swatch stayed black."""
        editor = self._editor(registry, qtbot, "github-light")
        line, swatch = editor._rows["heading"]
        before = swatch.icon().pixmap(QSize(24, 18)).toImage().pixelColor(0, 0)
        line.setText("#d6320c")
        line.editingFinished.emit()  # exactly what losing focus in the field fires
        image = swatch.icon().pixmap(QSize(24, 18)).toImage()
        assert image.pixelColor(0, 0) == QColor("#d6320c")  # opaque -> exact, over checker
        assert before != QColor("#d6320c")  # the old icon really did change

    def test_alpha_swatch_renders_over_checkerboard(
        self, registry: PaletteRegistry, qtbot: object
    ) -> None:
        editor = self._editor(registry, qtbot, "github-light")
        swatch = editor._rows["selection"][1]
        swatch.set_hex("#0969da33")  # translucent: invisible without the checker
        image = swatch.icon().pixmap(QSize(24, 18)).toImage()
        over_white = image.pixelColor(0, 0)  # (0,0) sits on a white square...
        over_grey = image.pixelColor(5, 0)  # ...x=4..7 on the grey one
        assert over_white != over_grey, "alpha must blend differently with each checker colour"
        for pixel in (over_white, over_grey):
            assert pixel.blue() > pixel.green() > pixel.red()  # still reads as the blue hue

    def test_invalid_or_empty_hex_shows_bare_checker(
        self, registry: PaletteRegistry, qtbot: object
    ) -> None:
        editor = self._editor(registry, qtbot, "github-light")
        line, swatch = editor._rows["link"]
        line.setText("nonsense")
        line.editingFinished.emit()
        image = swatch.icon().pixmap(QSize(24, 18)).toImage()
        assert image.pixelColor(0, 0) != image.pixelColor(5, 0)  # no colour fill laid over it

    def test_title_and_builtin_hint_follow_selection(
        self, registry: PaletteRegistry, qtbot: object
    ) -> None:
        editor = self._editor(registry, qtbot, "github-light")
        hint = editor.findChild(QLabel, "paletteBuiltinHint")
        assert editor.windowTitle() == "Edit Palette — GitHub Light"
        assert hint.text() == "Built-in palette — saving creates a user copy"
        assert not hint.isHidden()
        save = editor._button_box.button(QDialogButtonBox.StandardButton.Save)
        assert save.isEnabled()  # documented design: Save shadows the built-in with a user copy

        editor._select("github-dark")
        assert editor.windowTitle() == "Edit Palette — GitHub Dark"
        assert not hint.isHidden()

        editor._name_edit.setText("My GitHub")  # saving turns the selection into a user palette
        editor._on_edited()
        editor._save()
        assert editor.last_saved_path is not None
        assert editor.windowTitle() == "Edit Palette — My GitHub"
        assert hint.isHidden()
        assert hint.text() == ""
        assert save.isEnabled()


class TestMacTabChrome:
    """Phase 4 macOS field report: tab-bar affordances (DESIGN.md chrome notes).

    Offscreen CI is Fusion/Linux, so these assert the *platform-independent*
    contract (closable tabs, the standard Close key, and that the macOS
    document-mode switch is exactly platform-gated -- Linux keeps the tuned
    Fusion look; macOS uses classic tabs for always-visible close buttons and
    a distinct active tab, per the QMacStyle behaviour documented in DESIGN.md).
    """

    def test_document_mode_is_platform_gated(self, ctx: AppContext) -> None:
        assert ctx.window._tab_view.documentMode() is (sys.platform != "darwin")

    def test_tabs_are_closable_and_movable(self, ctx: AppContext) -> None:
        tabs = ctx.window._tab_view
        assert tabs.tabsClosable()
        assert tabs.isMovable()
        assert tabs.usesScrollButtons()

    def test_close_tab_uses_the_standard_close_key(self, ctx: AppContext) -> None:
        """QKeySequence::Close is ⌘W on macOS; binding the standard key is the guarantee."""
        action = ctx.window._actions["closeTab"]
        assert action.shortcut() == QKeySequence(QKeySequence.StandardKey.Close)

    def test_platform_close_key_press_closes_the_tab(
        self, ctx: AppContext, demo_file: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        window = ctx.window
        window.show()
        qtbot.waitExposed(window)  # type: ignore[attr-defined]
        window.open_paths([demo_file])
        assert window.tab_count() == 1
        combo = window._actions["closeTab"].shortcut()[0]  # ⌘W on macOS, Ctrl+F4 here
        window.activateWindow()
        qtbot.waitUntil(  # type: ignore[attr-defined]
            lambda: QApplication.activeWindow() is window, timeout=2000
        )
        qtbot.keyClick(  # type: ignore[attr-defined]
            window, combo.key(), combo.keyboardModifiers()
        )
        qtbot.waitUntil(lambda: window.tab_count() == 0, timeout=2000)  # type: ignore[attr-defined]

    def test_clicking_the_tab_close_button_closes_the_tab(
        self, ctx: AppContext, demo_file: Path
    ) -> None:
        """The tabsClosable contract end-to-end: the bar's close button path."""
        window = ctx.window
        window.open_paths([demo_file])
        assert window.tab_count() == 1
        tabs = window._tab_view
        index = tabs.currentIndex()
        assert index >= 0
        bar = tabs.tabBar()
        right = bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
        left = bar.tabButton(index, QTabBar.ButtonPosition.LeftSide)
        button = right if right is not None else left
        assert button is not None  # the affordance QMacStyle must keep visible on macOS
        button.click()
        assert window.tab_count() == 0


class TestWindowLayout:
    """Phase 4 item 3: content must never run under the bottom zoom/status bar."""

    def test_bottom_zoom_bar_does_not_occlude_content(
        self, ctx: AppContext, demo_file: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        window = ctx.window
        window.show()
        qtbot.waitExposed(window)  # type: ignore[attr-defined]
        window.open_paths([demo_file])
        for view_id in window._tab_view.view_ids():
            view = window.view_for(view_id)
            assert view is not None

        toolbar = window.findChild(QToolBar, "zoomToolbar")
        assert toolbar is not None
        assert toolbar.height() > 0  # really shown, not collapsed
        toolbar_top = toolbar.mapTo(window, QPoint(0, 0)).y()

        # The tab strip *and every content view* (QWebEngineView panes) must
        # end strictly above the bar in window coordinates. QMainWindowLayout
        # carves the toolbar out of the window area before laying out the
        # central widget, so this is a regression guard, not a workaround.
        stack = window.centralWidget()
        assert stack is not None
        stack_bottom = stack.mapTo(window, QPoint(0, stack.height() - 1)).y()
        assert stack_bottom < toolbar_top
        for view_id in window._tab_view.view_ids():
            view = window.view_for(view_id)
            assert view is not None
            view_bottom = view.mapTo(window, QPoint(0, view.height() - 1)).y()
            assert view_bottom < toolbar_top
