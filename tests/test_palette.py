"""Palette model: validation, normalisation, IO, registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdview.core.errors import PaletteError, PaletteValidationError
from mdview.core.palette import (
    ALL_COLOR_KEYS,
    PALETTE_FORMAT,
    REQUIRED_COLOR_KEYS,
    Palette,
    PaletteRegistry,
    load_palette_dir,
    load_palette_file,
    normalize_hex_color,
    save_palette,
    slugify,
)

def test_normalize_hex_accepts_shorthand_and_expands() -> None:
    assert normalize_hex_color("#ABC") == "#aabbcc"
    assert normalize_hex_color(" #1F2328 ") == "#1f2328"
    assert normalize_hex_color("#11223344") == "#11223344"


@pytest.mark.parametrize("bad", ["", "red", "#12", "#12345", "1f2328", None, 0x123456])
def test_normalize_hex_rejects_garbage(bad: object) -> None:
    with pytest.raises(ValueError, match="colour"):
        normalize_hex_color(bad)


def test_slugify() -> None:
    assert slugify("GitHub Dark") == "github-dark"
    assert slugify("  High-Contrast (Light!)  ") == "high-contrast-light"
    assert slugify("///") == "palette"


def test_all_ship_builtins_are_valid_and_typed() -> None:
    for path in sorted(Path("src/mdview/palettes").glob("*.json")):
        palette = load_palette_file(path, builtin=True)
        assert palette.colors.keys() >= set(REQUIRED_COLOR_KEYS)
        assert palette.appearance in ("light", "dark")
        assert palette.builtin is True


def test_optional_keys_get_fallbacks(dark_palette: Palette) -> None:
    raw = dark_palette.to_dict()
    for key in ("syntax_operator", "syntax_type", "syntax_invalid", "scrollbar", "code_border"):
        raw["colors"].pop(key, None)
    rebuilt = Palette.from_dict(raw)
    assert rebuilt.colors["syntax_operator"] == rebuilt.colors["text"]
    assert rebuilt.colors["code_border"] == rebuilt.colors["border"]


def test_from_dict_collects_every_problem_before_raising() -> None:
    with pytest.raises(PaletteValidationError) as exc_info:
        Palette.from_dict(
            {
                "format": "mdview-palette-99",
                "id": "!!!",
                "name": "  ",
                "appearance": "sepia",
                "colors": {"background": "green", "unknown-key": "#fff"},
            }
        )
    problems = " ".join(exc_info.value.problems)
    assert "unsupported format" in problems
    assert "id must match" in problems
    assert "name must be" in problems
    assert "appearance must be" in problems
    assert "unknown colour key" in problems
    assert "not a hex colour" in problems
    assert "missing required colour key 'text'" in problems


def test_font_size_out_of_range_is_rejected(dark_palette: Palette) -> None:
    raw = dark_palette.to_dict() | {"base_font_size_px": 900}
    with pytest.raises(PaletteValidationError, match="base_font_size_px"):
        Palette.from_dict(raw)


def test_roundtrip_write_and_load(tmp_path: Path, dark_palette: Palette) -> None:
    clone = Palette.from_dict(dark_palette.to_dict() | {"id": "my-dark", "name": "My Dark"})
    path = save_palette(clone, tmp_path)
    assert path.name == "my-dark.json"
    loaded = load_palette_file(path)
    assert loaded.colors == dark_palette.colors
    assert loaded.name == "My Dark"
    assert loaded.builtin is False


def test_builtin_refuses_to_write(tmp_path: Path, dark_palette: Palette) -> None:
    with pytest.raises(PaletteError, match="built-in"):
        dark_palette.write(tmp_path)


def test_load_dir_reports_bad_files(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PaletteError, match="invalid JSON"):
        load_palette_dir(tmp_path)


def test_registry_user_palette_overrides_builtin_but_keeps_original(
    tmp_path: Path, dark_palette: Palette
) -> None:
    user_dir = tmp_path / "user"
    override = Palette.from_dict(
        dark_palette.to_dict() | {"name": "GitHub Dark (mine)", "colors": dict(dark_palette.colors, background="#000000")}
    )
    override.write(user_dir)

    registry = PaletteRegistry.from_dirs(Path("src/mdview/palettes"), user_dir)
    assert registry.get("github-dark").name == "GitHub Dark (mine)"
    assert registry.get("github-dark").builtin is False
    assert registry.get("github-dark-builtin").builtin is True  # kept for "reset"


def test_registry_defaults_and_filters(registry: PaletteRegistry) -> None:
    assert set(registry.ids(appearance="dark")) == {"dracula-dark", "github-dark"}
    assert set(registry.defaults_for(("light", "dark"))) == {"light", "dark"}
    names = dict(registry.names())
    assert names["github-light"] == "GitHub Light"


def test_extra_metadata_survives_roundtrip(dark_palette: Palette, tmp_path: Path) -> None:
    raw = dark_palette.to_dict() | {"author": "me", "tags": ["x"]}
    palette = Palette.from_dict(raw)
    assert palette.extra["author"] == "me"
    palette.write(tmp_path)
    on_disk = json.loads((tmp_path / f"{palette.id}.json").read_text())
    assert on_disk["format"] == PALETTE_FORMAT
    assert on_disk["author"] == "me"
    assert set(palette.colors) == set(ALL_COLOR_KEYS)
