"""Palette + font size -> CSS custom properties (pure, heavily unit-tested).

This is the one place that knows how a palette becomes CSS.  ``static/mdview.css``
consumes only ``--mdv-*`` variables, so a palette change is a one-string CSS
swap -- which is exactly what makes *live* palette switching cheap enough to run
on every keystroke in the editor.
"""

from __future__ import annotations

from mdview.core.appearance import Appearance
from mdview.core.fonts import font_css_vars
from mdview.core.palette import ALL_COLOR_KEYS, Palette
from mdview.render.highlight import pygments_css

__all__ = ["css_var_name", "page_css", "palette_css_vars"]


def css_var_name(color_key: str) -> str:
    """``"code_bg"`` -> ``"--mdv-code-bg"`` (the only naming rule, tested once)."""
    return f"--mdv-{color_key.replace('_', '-')}"


def palette_css_vars(palette: Palette) -> str:
    """``:root`` body declaring every colour of a palette."""
    lines = [f"  {css_var_name(key)}: {palette.colors[key]};" for key in sorted(ALL_COLOR_KEYS)]
    return "\n".join(lines)


def page_css(
    palette: Palette,
    font_size_px: int,
    *,
    appearance: Appearance = "light",
    smooth_scrolling: bool = True,
) -> str:
    """The inline ``<style>`` block: variables + Pygments tokens + behaviour flags."""
    scroll = "smooth" if smooth_scrolling else "auto"
    return (
        ":root {\n"
        f"  color-scheme: {appearance};\n"
        f"{palette_css_vars(palette)}\n"
        f"{font_css_vars(font_size_px)}"
        f"  --mdv-scroll-behavior: {scroll};\n"
        "}\n"
        f"{pygments_css(palette.colors)}"
    )
