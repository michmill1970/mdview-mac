"""Appearance + palette selection (pure).

Ties :class:`~mdview.core.settings.Settings` and a
:class:`~mdview.core.palette.PaletteRegistry` together: given what the user
picked and what macOS is currently doing, which palette do we render with?

The rule set is deliberately forgiving: a palette that does not exist (deleted
by hand, or authored for the other appearance) must never stop the app from
drawing, so every miss falls back to a built-in default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from mdview.core.appearance import Appearance, AppearanceMode, resolve_appearance
from mdview.core.palette import Palette, PaletteRegistry
from mdview.core.settings import Settings

__all__ = ["DEFAULT_PALETTES", "PaletteSelection", "select_palette", "selection_for"]

DEFAULT_PALETTES: Final[dict[Appearance, str]] = {
    "light": "github-light",
    "dark": "github-dark",
}


@dataclass(frozen=True, slots=True)
class PaletteSelection:
    """The resolved rendering choice for one paint."""

    appearance: Appearance
    palette: Palette
    #: Set when the configured palette id was missing and we fell back.
    fell_back: bool = False

    @property
    def is_dark(self) -> bool:
        return self.appearance == "dark"


def select_palette(
    registry: PaletteRegistry,
    appearance: Appearance,
    *,
    palette_light: str,
    palette_dark: str,
) -> PaletteSelection:
    """Resolve one palette, preferring the configured id for the appearance."""
    wanted = palette_dark if appearance == "dark" else palette_light
    palette = registry.find(wanted)
    if palette is not None:
        return PaletteSelection(appearance=appearance, palette=palette)
    # Configured palette vanished or belongs to the other appearance: use the
    # first palette authored for this appearance, else any palette at all.
    for pid in registry.ids(appearance=appearance):
        return PaletteSelection(appearance=appearance, palette=registry.get(pid), fell_back=True)
    for pid in registry.ids():
        return PaletteSelection(appearance=appearance, palette=registry.get(pid), fell_back=True)
    raise LookupError("no palettes available -- built-in presets are missing from the install")


def selection_for(
    registry: PaletteRegistry,
    settings: Settings,
    system_appearance: Appearance,
    *,
    override: str | None = None,
) -> PaletteSelection:
    """High-level entry point used by the GUI on every re-render.

    ``override`` lets a one-off render (e.g. the palette editor's live preview)
    pick a specific palette id without touching settings.
    """
    appearance = resolve_appearance(
        "system" if override is not None else settings.appearance_mode, system_appearance
    )
    if override is not None:
        palette = registry.find(override)
        if palette is not None:
            appearance = palette.appearance
            return PaletteSelection(appearance=appearance, palette=palette)
    return select_palette(
        registry,
        appearance,
        palette_light=settings.palette_light,
        palette_dark=settings.palette_dark,
    )


def default_palette_ids(registry: PaletteRegistry) -> dict[Appearance, str]:
    """Settings defaults derived from the shipped presets (with a hard fallback)."""
    found = registry.defaults_for(("light", "dark"))
    return {
        "light": found.get("light", DEFAULT_PALETTES["light"]),
        "dark": found.get("dark", DEFAULT_PALETTES["dark"]),
    }


def mode_is_pinned(mode: AppearanceMode) -> bool:
    """Whether the user pinned an appearance (menu checkbox state helper)."""
    return mode != "system"
