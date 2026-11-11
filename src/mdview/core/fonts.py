"""Font-size model (pure).

Zooming is modelled as a *base font size in CSS pixels*, not a Qt font point
size: the content pane is HTML, so one number drives ``--mdv-font-size`` and
everything else (headings, code, spacing) scales with ``rem`` units.

Shortcuts map 1:1 onto the browser conventions the product owner asked for:
``Cmd+=`` up, ``Cmd+-`` down, ``Cmd+0`` reset.
"""

from __future__ import annotations

from dataclasses import dataclass

from mdview.core.errors import MDViewError

__all__ = [
    "DEFAULT_FONT_SIZE_PX",
    "FONT_SIZE_MAX_PX",
    "FONT_SIZE_MIN_PX",
    "FONT_SIZE_STEP_PX",
    "FontSize",
    "clamp_font_size",
    "font_css_vars",
]

DEFAULT_FONT_SIZE_PX = 16
FONT_SIZE_MIN_PX = 10
FONT_SIZE_MAX_PX = 40
FONT_SIZE_STEP_PX = 1
#: Line height as a unitless multiple of the font size (CSS-friendly).
LINE_HEIGHT = 1.6


def clamp_font_size(value: object, *, default: int = DEFAULT_FONT_SIZE_PX) -> int:
    """Coerce arbitrary input into the legal font-size range."""
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(FONT_SIZE_MIN_PX, min(FONT_SIZE_MAX_PX, value))


@dataclass(slots=True)
class FontSize:
    """Mutable font-size controller shared by the window, the toolbar and the renderer."""

    size_px: int = DEFAULT_FONT_SIZE_PX

    def __post_init__(self) -> None:
        self.size_px = clamp_font_size(self.size_px)

    @property
    def is_default(self) -> bool:
        """Whether ``Cmd+0`` would be a no-op (drives the menu item's enabled state)."""
        return self.size_px == DEFAULT_FONT_SIZE_PX

    @property
    def can_increase(self) -> bool:
        return self.size_px < FONT_SIZE_MAX_PX

    @property
    def can_decrease(self) -> bool:
        return self.size_px > FONT_SIZE_MIN_PX

    def percent(self) -> int:
        """Zoom relative to the default size, for the UI control's label."""
        return round(self.size_px / DEFAULT_FONT_SIZE_PX * 100)

    def increase(self, step: int = FONT_SIZE_STEP_PX) -> bool:
        """Grow by ``step``; return whether anything changed."""
        return self._set(self.size_px + max(1, step))

    def decrease(self, step: int = FONT_SIZE_STEP_PX) -> bool:
        """Shrink by ``step``; return whether anything changed."""
        return self._set(self.size_px - max(1, step))

    def reset(self) -> bool:
        """Back to :data:`DEFAULT_FONT_SIZE_PX`; return whether anything changed."""
        return self._set(DEFAULT_FONT_SIZE_PX)

    def set(self, value: int) -> bool:
        """Set an explicit size (from the spin box); clamped, returns whether it changed."""
        return self._set(value)

    def _set(self, value: int) -> bool:
        try:
            target = clamp_font_size(value)
        except MDViewError:  # pragma: no cover - clamp never raises
            return False
        if target == self.size_px:
            return False
        self.size_px = target
        return True


def font_css_vars(size_px: int) -> str:
    """CSS custom properties for one font size (used by :mod:`mdview.render.page`)."""
    size = clamp_font_size(size_px)
    return (
        f"  --mdv-font-size: {size}px;\n"
        f"  --mdv-code-size: {max(FONT_SIZE_MIN_PX, size - 1)}px;\n"
        f"  --mdv-line-height: {LINE_HEIGHT};\n"
    )
