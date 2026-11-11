"""Light/dark appearance model (pure).

The app follows the system appearance by default, but the user may pin light or
dark in settings.  The GUI maps ``QStyleHints.colorScheme()`` onto
:class:`Appearance` and stores :class:`AppearanceMode`; everything downstream
works on the resolved :class:`Appearance`.
"""

from __future__ import annotations

from typing import Literal

__all__ = [
    "Appearance",
    "AppearanceMode",
    "appearance_mode_values",
    "flip",
    "normalize_appearance_mode",
    "resolve_appearance",
]

Appearance = Literal["light", "dark"]
"""A concrete appearance that a palette can be authored for."""

AppearanceMode = Literal["system", "light", "dark"]
"""What the user asked for; ``"system"`` means *follow macOS*."""


def appearance_mode_values() -> tuple[AppearanceMode, ...]:
    """Allowed settings values, in menu order."""
    return ("system", "light", "dark")


def normalize_appearance_mode(value: object) -> AppearanceMode:
    """Coerce untrusted input to an :data:`AppearanceMode` (default ``system``)."""
    if isinstance(value, str) and value in appearance_mode_values():
        return value  # type: ignore[return-value]
    return "system"


def resolve_appearance(mode: AppearanceMode, system: Appearance) -> Appearance:
    """Turn a user preference plus the system appearance into a concrete one."""
    return system if mode == "system" else mode


def flip(appearance: Appearance) -> Appearance:
    """The other appearance (used when a palette only exists for one mode)."""
    return "dark" if appearance == "light" else "light"
