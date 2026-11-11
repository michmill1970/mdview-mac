"""Mermaid theming: palette -> themeVariables mapping (pure, no browser).

Phase 5 vision review: Mermaid's built-in themes rendered light-lavender
nodes and near-invisible edges on dark palettes ("a foreign object pasted
onto the dark page").  The contract is now: ``theme: "base"`` plus one
palette-derived colour per role, and every derived colour must *be* a palette
token -- no off-palette blends.
"""

from __future__ import annotations

import pytest

from mdview.core.palette import PaletteRegistry
from mdview.render.mermaid_theme import mermaid_theme_vars


def _channel(h: str) -> float:
    c = int(h, 16) / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    digits = color[1:]
    if len(digits) == 8:  # palette colours may carry alpha; theme uses them opaque
        digits = digits[:6]
    r, g, b = (digits[i : i + 2] for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture
def dark_tv(registry: PaletteRegistry) -> dict[str, str]:
    return mermaid_theme_vars(registry.get("github-dark"))


def test_every_value_is_a_palette_token(registry: PaletteRegistry) -> None:
    for palette in (p for p in registry.palettes.values() if p.builtin):
        variables = mermaid_theme_vars(palette)
        assert variables, f"{palette.id}: empty themeVariables"
        off_palette = {k: v for k, v in variables.items() if v not in set(palette.colors.values())}
        assert not off_palette, f"{palette.id}: colours not from the palette: {off_palette}"


def test_dark_mapping_spot_values(dark_tv: dict[str, str]) -> None:
    # github-dark tokens: surface #151b23, accent #2f81f7, text #e6edf3,
    # muted #8d96a0, border #3d444d, background #0d1117, code_bg #151b23.
    assert dark_tv["background"] == "#0d1117"
    assert dark_tv["primaryColor"] == "#151b23"  # node fill = surface
    assert dark_tv["primaryBorderColor"] == "#2f81f7"  # accent-tinted border
    assert dark_tv["primaryTextColor"] == "#e6edf3"
    assert dark_tv["lineColor"] == "#8d96a0"  # edges/arrows = muted, never off-palette
    assert dark_tv["edgeLabelBackground"] == "#0d1117"
    assert dark_tv["clusterBkg"] == "#151b23"
    assert dark_tv["clusterBorder"] == "#3d444d"
    assert dark_tv["noteBkgColor"] == "#151b23"
    assert dark_tv["noteTextColor"] == "#e6edf3"
    assert dark_tv["actorBorder"] == "#2f81f7"
    assert dark_tv["actorLineColor"] == "#8d96a0"


def test_all_builtin_palettes_share_one_schema(registry: PaletteRegistry) -> None:
    schemas = {
        frozenset(mermaid_theme_vars(palette))
        for palette in registry.palettes.values()
        if palette.builtin
    }
    assert len(schemas) == 1, "themeVariables keys must be palette-independent"


@pytest.mark.parametrize(
    "palette_id",
    ["github-light", "github-dark", "dracula-dark", "high-contrast-light"],
)
def test_edges_and_labels_stay_readable(registry: PaletteRegistry, palette_id: str) -> None:
    """The defect being fixed: near-invisible edges on dark palettes."""
    variables = mermaid_theme_vars(registry.get(palette_id))
    assert _contrast(variables["lineColor"], variables["background"]) >= 4.5
    assert _contrast(variables["primaryTextColor"], variables["primaryColor"]) >= 4.5
    assert _contrast(variables["edgeLabelColor"], variables["edgeLabelBackground"]) >= 4.5
    assert _contrast(variables["primaryBorderColor"], variables["background"]) >= 3.0
