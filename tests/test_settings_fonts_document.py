"""Settings, font-size model, and the document model."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdview.core.document import Document, DocumentError, is_supported, title_from_markdown
from mdview.core.fonts import (
    FONT_SIZE_MAX_PX,
    FONT_SIZE_MIN_PX,
    FontSize,
    clamp_font_size,
    font_css_vars,
)
from mdview.core.settings import Settings, load_settings_or_default, save_settings

# ---------------------------------------------------------------- settings --


def test_settings_roundtrip(tmp_path: Path) -> None:
    settings = Settings(font_size_px=19, appearance_mode="dark", palette_dark="dracula-dark")
    path = tmp_path / "settings.json"
    save_settings(settings, path)
    loaded, warning = load_settings_or_default(path)
    assert warning is None
    assert loaded == settings


def test_settings_missing_file_yields_defaults(tmp_path: Path) -> None:
    loaded, warning = load_settings_or_default(tmp_path / "nope.json")
    assert warning is None
    assert loaded == Settings()


def test_corrupt_settings_survive_with_a_warning(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ broken", encoding="utf-8")
    loaded, warning = load_settings_or_default(path)
    assert loaded == Settings()
    assert warning and "settings" in warning.lower()


def test_settings_normalizes_clamps_and_validates() -> None:
    raw = Settings.from_dict(
        {"font_size_px": 999, "appearance_mode": "neon", "palette_light": 5, "open_files": ["a.md"]}
    )
    assert raw.normalized() == Settings(
        font_size_px=clamp_font_size(999),
        appearance_mode="system",
        palette_light="github-light",
        open_files=("a.md",),
    )


# ------------------------------------------------------------------- fonts --


def test_font_size_steps_and_clamps() -> None:
    size = FontSize(16)
    assert size.increase() and size.size_px == 17
    assert size.increase(step=3) and size.size_px == 20
    assert size.percent() == 125
    assert size.reset() and size.size_px == 16 and size.is_default
    big = FontSize(FONT_SIZE_MAX_PX)
    assert not big.increase()
    tiny = FontSize(FONT_SIZE_MIN_PX)
    assert not tiny.decrease()
    assert size.set(1000) and size.size_px == FONT_SIZE_MAX_PX  # clamped, not rejected
    assert size.set(23) and size.size_px == 23


def test_font_size_set_unchanged_is_not_a_change() -> None:
    assert not FontSize(16).set(16)
    assert not FontSize(16).reset()


def test_font_css_vars_scale_code_with_body() -> None:
    css = font_css_vars(20)
    assert "--mdv-font-size: 20px" in css
    assert "--mdv-code-size" in css


# ---------------------------------------------------------------- document --


def test_is_supported() -> None:
    assert is_supported("x.md") and is_supported(Path("x.MARKDOWN"))
    assert not is_supported("x.txt") and not is_supported("x")


def test_document_open_read_title(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text("# Real Title\nbody\n", encoding="utf-8")
    document = Document.open(path)
    assert document.title == "Real Title"
    assert document.exists
    assert document.read().startswith("# Real")


def test_document_title_falls_back_to_filename(tmp_path: Path) -> None:
    path = tmp_path / "plain.md"
    path.write_text("just text\n", encoding="utf-8")
    assert Document.open(path).title == "plain"


def test_document_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(DocumentError, match="[Nn]ot found"):
        Document.open(tmp_path / "ghost.md")


def test_stat_token_changes_on_edit(tmp_path: Path) -> None:
    path = tmp_path / "t.md"
    path.write_text("one", encoding="utf-8")
    document = Document.open(path)
    before = document.stat_token
    path.write_text("two -- longer content", encoding="utf-8")
    assert document.stat_token != before


def test_matches_is_path_equality() -> None:
    document = Document(Path("/tmp/a/b.md"))
    assert document.matches("/tmp/a/b.md")
    assert document.matches(Path("/tmp/a/../a/b.md"))
    assert not document.matches("/tmp/c.md")


def test_title_from_markdown_rules() -> None:
    assert title_from_markdown("  # Spaced Title #", fallback="f") == "Spaced Title"
    assert title_from_markdown("```\n# not a heading\n```", fallback="f") == "f"
    assert title_from_markdown("", fallback="fallback") == "fallback"
