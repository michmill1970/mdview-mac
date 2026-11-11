"""Headless GUI behaviour (offscreen platform): tabs, theme, fonts, settings.

These construct the real ``MainWindow`` through the production bootstrap
(``ensure_bundled_assets=False`` + the seeded vendor cache, so no network),
but never load a page into the view -- they assert on model state, the page
store, and the settings file, which is exactly where the app's logic lives.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialogButtonBox

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
