"""Error hierarchy for mdview.

Every failure raised by the pure core is a subclass of :class:`MDViewError` so
that the GUI layer can catch exactly one type at its boundaries.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "AssetError",
    "DocumentError",
    "MDViewError",
    "PaletteError",
    "PaletteValidationError",
    "SettingsError",
]


class MDViewError(Exception):
    """Base class for all mdview errors."""


class PaletteError(MDViewError):
    """A palette could not be read or written."""


class PaletteValidationError(PaletteError):
    """A palette document is structurally invalid.

    All problems are collected before raising so that the palette editor can
    show the user every error at once instead of one per save attempt.
    """

    def __init__(self, palette_id: str, problems: Sequence[str]) -> None:
        self.palette_id = palette_id
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__(f"invalid palette {palette_id!r}: " + "; ".join(self.problems))


class SettingsError(MDViewError):
    """The settings file exists but cannot be parsed."""


class AssetError(MDViewError):
    """Bundled web assets (KaTeX/Mermaid) are missing or corrupt."""


class DocumentError(MDViewError):
    """A Markdown document could not be read."""
