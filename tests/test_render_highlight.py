"""Pygments bridge: palette-driven styles, per-fence output, token CSS."""

from __future__ import annotations

from mdview.core.palette import Palette
from mdview.render.highlight import highlight, pygments_css, resolve_lexer


def test_python_fence_is_highlighted(dark_palette: Palette) -> None:
    result = highlight("def f(x):\n    return x + 1  # hi", "python", dark_palette.colors)
    assert not result.is_plain
    assert result.language == "python"
    assert 'class="md-code"' in result.html
    assert 'data-language="python"' in result.html
    assert result.html.count("<span") > 5


def test_fence_info_after_language_is_ignored(dark_palette: Palette) -> None:
    result = highlight("x = 1", "python title=foo.py", dark_palette.colors)
    assert result.language == "python"


def test_unknown_and_plain_languages_degrade_to_text(dark_palette: Palette) -> None:
    for lang in ("no-such-language", "", "text", "plaintext"):
        result = highlight("just text", lang, dark_palette.colors)
        assert result.is_plain
        assert "<span" not in result.html


def test_html_in_code_is_escaped(dark_palette: Palette) -> None:
    result = highlight('<script>alert("x")</script>', "html", dark_palette.colors)
    assert "<script>" not in result.html  # never live markup
    assert "&lt;" in result.html  # but visible as text
    assert "&gt;" in result.html


def test_styles_follow_the_palette(dark_palette: Palette, light_palette: Palette) -> None:
    dark_css = pygments_css(dark_palette.colors)
    light_css = pygments_css(light_palette.colors)
    assert dark_css != light_css
    assert dark_palette.colors["code_bg"] in dark_css
    assert light_palette.colors["code_bg"] in light_css
    # Scoped to our container so pages never leak styles into each other.
    assert dark_css.startswith(".md-code {")


def test_same_colors_reuse_the_cached_style_class(dark_palette: Palette) -> None:
    from mdview.render.highlight import _style_class  # cache identity, not internals

    key_a = tuple(sorted(dark_palette.colors.items()))
    key_b = tuple(sorted(dict(reversed(list(dark_palette.colors.items()))).items()))
    assert key_a == key_b  # sanity: same content, different construction
    assert _style_class(key_a) is _style_class(key_b)


def test_resolve_lexer_known_aliases() -> None:
    assert resolve_lexer("py") is not None
    assert resolve_lexer("shellsession") is not None
    assert resolve_lexer("text") is None
    assert resolve_lexer(None) is None
