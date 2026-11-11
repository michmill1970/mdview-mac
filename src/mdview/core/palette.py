"""Palette model: schema, validation, storage, registry (pure).

A palette is a **named set of colours** with a fixed vocabulary of keys so that
the CSS generator and the in-app editor can work without knowing about
individual palettes.  The on-disk format is JSON (``format: "mdview-palette-1"``),
one file per palette in the user palettes directory, so users can hand-edit or
share them without any export step.

Built-in palettes ship inside the package and are immutable; user palettes are
writable.  ``builtin=True`` is derived from *where a palette was loaded from*,
never from the file contents, so a user file can never masquerade as a built-in.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from mdview.core.appearance import Appearance
from mdview.core.errors import PaletteError, PaletteValidationError

__all__ = [
    "ALL_COLOR_KEYS",
    "PALETTE_FORMAT",
    "REQUIRED_COLOR_KEYS",
    "Palette",
    "PaletteRegistry",
    "load_palette_dir",
    "load_palette_file",
    "normalize_hex_color",
    "save_palette",
    "slugify",
]

PALETTE_FORMAT: Final = "mdview-palette-1"

ColorKey = str

#: Keys every palette must define.  Names are semantic, not CSS-ish, so that the
#: editor can group them ("Background", "Text", "Code", ...) later on.
REQUIRED_COLOR_KEYS: Final[frozenset[ColorKey]] = frozenset(
    {
        "background",
        "surface",
        "text",
        "muted_text",
        "heading",
        "link",
        "accent",
        "border",
        "selection",
        "selection_text",
        "code_bg",
        "code_text",
        "syntax_keyword",
        "syntax_string",
        "syntax_comment",
        "syntax_number",
        "syntax_function",
    }
)

#: Optional keys; absent ones fall back to a related required key.
OPTIONAL_COLOR_KEYS: Final[frozenset[ColorKey]] = frozenset(
    {
        "link_hover",
        "code_border",
        "syntax_operator",
        "syntax_type",
        "syntax_invalid",
        "scrollbar",
    }
)

ALL_COLOR_KEYS: Final[frozenset[ColorKey]] = REQUIRED_COLOR_KEYS | OPTIONAL_COLOR_KEYS

#: Fallbacks applied when an optional key is missing.
_FALLBACKS: Final[Mapping[ColorKey, ColorKey]] = {
    "link_hover": "link",
    "code_border": "border",
    "syntax_operator": "text",
    "syntax_type": "syntax_function",
    "syntax_invalid": "syntax_keyword",
    "scrollbar": "muted_text",
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FONT_SIZE_RANGE: Final = (8, 72)


def normalize_hex_color(value: object) -> str:
    """Return ``#rrggbb``/``#rrggbbaa`` for a valid CSS hex colour.

    Accepts the 3/4-digit shorthand and expands it, because hand-written
    palettes should not be rejected for using ``#abc``.
    """
    if not isinstance(value, str):
        raise ValueError(f"colour must be a string, got {type(value).__name__}")
    text = value.strip()
    if not _HEX_RE.match(text):
        raise ValueError(f"not a hex colour: {value!r} (expected e.g. '#1f2328')")
    digits = text[1:]
    if len(digits) in (3, 4):
        digits = "".join(ch * 2 for ch in digits)
    return f"#{digits.lower()}"


def slugify(name: str) -> str:
    """Turn a palette name into a filesystem-safe id (``GitHub Dark`` -> ``github-dark``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:64] or "palette"


@dataclass(frozen=True, slots=True)
class Palette:
    """An immutable, validated palette.

    ``colors`` is always a complete mapping over :data:`ALL_COLOR_KEYS` with
    normalised hex values, which is what makes the CSS generator trivial.
    """

    id: str
    name: str
    appearance: Appearance
    colors: Mapping[ColorKey, str] = field(default_factory=dict)
    base_font_size_px: int = 16
    builtin: bool = False
    source_path: Path | None = None
    #: Free-form metadata preserved round-trip (author, description, tags...).
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "colors", dict(sorted(self.colors.items())))

    def color(self, key: ColorKey) -> str:
        """Look up one colour; raises :class:`KeyError` for unknown keys."""
        try:
            return self.colors[key]
        except KeyError:  # pragma: no cover - defensive
            raise KeyError(f"unknown colour key {key!r}") from None

    def with_colors(self, **overrides: str) -> Palette:
        """Copy with a few colours changed (used by the editor on each keystroke)."""
        colors = dict(self.colors)
        colors.update(overrides)
        return replace(self, colors=colors)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the on-disk JSON document shape."""
        doc: dict[str, Any] = {
            "format": PALETTE_FORMAT,
            "id": self.id,
            "name": self.name,
            "appearance": self.appearance,
            "base_font_size_px": self.base_font_size_px,
            "colors": dict(self.colors),
        }
        doc.update(self.extra)
        return doc

    # ------------------------------------------------------------------ io --
    def write(self, directory: Path) -> Path:
        """Persist as ``<directory>/<id>.json``; refuses to shadow a built-in."""
        if self.builtin:
            raise PaletteError(f"refusing to write built-in palette {self.id!r}")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.id}.json"
        text = json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
        *,
        builtin: bool = False,
        source_path: Path | None = None,
    ) -> Palette:
        """Build and validate a palette, collecting *all* problems before raising."""
        problems: list[str] = []
        if not isinstance(raw, Mapping):
            raise PaletteValidationError("<unknown>", ["palette document must be an object"])

        fmt = raw.get("format")
        if fmt is not None and fmt != PALETTE_FORMAT:
            problems.append(f"unsupported format {fmt!r} (expected {PALETTE_FORMAT!r})")

        palette_id = raw.get("id")
        if not isinstance(palette_id, str) or not _ID_RE.match(palette_id):
            problems.append(f"id must match {_ID_RE.pattern}, got {palette_id!r}")
            palette_id = slugify(str(raw.get("name", "palette")))

        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append("name must be a non-empty string")
            name = palette_id

        appearance = raw.get("appearance")
        if appearance not in ("light", "dark"):
            problems.append(f"appearance must be 'light' or 'dark', got {appearance!r}")
            appearance = "light"

        font_size = raw.get("base_font_size_px", 16)
        if not isinstance(font_size, int) or isinstance(font_size, bool):
            problems.append("base_font_size_px must be an integer")
            font_size = 16
        elif not _FONT_SIZE_RANGE[0] <= font_size <= _FONT_SIZE_RANGE[1]:
            problems.append(f"base_font_size_px must be in {_FONT_SIZE_RANGE}, got {font_size}")
            font_size = 16

        colors, color_problems = _validate_colors(raw.get("colors"))
        problems.extend(color_problems)

        if problems:
            raise PaletteValidationError(str(palette_id), problems)

        known = {"format", "id", "name", "appearance", "base_font_size_px", "colors"}
        extra = {k: v for k, v in raw.items() if k not in known}
        return cls(
            id=palette_id,
            name=name.strip(),
            appearance=appearance,  # type: ignore[arg-type]
            colors=colors,
            base_font_size_px=font_size,
            builtin=builtin,
            source_path=source_path,
            extra=extra,
        )


def _validate_colors(raw: object) -> tuple[dict[ColorKey, str], list[str]]:
    problems: list[str] = []
    if not isinstance(raw, Mapping):
        return {}, [f"colors must be an object, got {type(raw).__name__}"]
    colors: dict[ColorKey, str] = {}
    for key, value in raw.items():
        if key not in ALL_COLOR_KEYS:
            problems.append(f"unknown colour key {key!r}")
            continue
        try:
            colors[key] = normalize_hex_color(value)
        except ValueError as exc:
            problems.append(str(exc))
    for key in sorted(REQUIRED_COLOR_KEYS - set(colors)):
        problems.append(f"missing required colour key {key!r}")
    for key, fallback in _FALLBACKS.items():
        if key not in colors and fallback in colors:
            colors[key] = colors[fallback]
    return colors, problems


def load_palette_file(path: Path, *, builtin: bool = False) -> Palette:
    """Read one palette JSON file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PaletteError(f"palette file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PaletteError(f"{path.name}: invalid JSON ({exc.msg} at line {exc.lineno})") from exc
    return Palette.from_dict(raw, builtin=builtin, source_path=path)


def load_palette_dir(directory: Path, *, builtin: bool = False) -> dict[str, Palette]:
    """Load every ``*.json`` palette in a directory, keyed by id (sorted, stable)."""
    if not directory.is_dir():
        return {}
    palettes: dict[str, Palette] = {}
    for path in sorted(directory.glob("*.json")):
        palette = load_palette_file(path, builtin=builtin)
        palettes[palette.id] = palette
    return palettes


def save_palette(palette: Palette, directory: Path) -> Path:
    """Functional wrapper around :meth:`Palette.write` (kept for symmetry)."""
    return palette.write(directory)


@dataclass(slots=True)
class PaletteRegistry:
    """Ordered id -> palette map of built-ins plus user palettes.

    User palettes may override a built-in id (a common way to tweak a preset);
    the built-in is kept as ``<id>-builtin`` so "Reset to default" can restore it.
    """

    palettes: dict[str, Palette] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    @classmethod
    def from_dirs(cls, builtin_dir: Path, user_dir: Path | None = None) -> PaletteRegistry:
        """Load shipped palettes, then user palettes on top."""
        merged: dict[str, Palette] = load_palette_dir(builtin_dir, builtin=True)
        if user_dir is not None:
            for pid, palette in load_palette_dir(user_dir, builtin=False).items():
                if pid in merged:
                    merged[f"{pid}-builtin"] = merged[pid]
                merged[pid] = palette
        return cls(palettes=merged, order=sorted(merged))

    def ids(self, *, appearance: Appearance | None = None) -> tuple[str, ...]:
        """Palette ids in display order, optionally filtered by appearance."""
        return tuple(
            pid
            for pid in self.order
            if appearance is None or self.palettes[pid].appearance == appearance
        )

    def get(self, palette_id: str) -> Palette:
        try:
            return self.palettes[palette_id]
        except KeyError as exc:
            raise PaletteError(f"unknown palette {palette_id!r}") from exc

    def find(self, palette_id: str) -> Palette | None:
        """Like :meth:`get` but returns ``None`` instead of raising."""
        return self.palettes.get(palette_id)

    def names(self, *, appearance: Appearance | None = None) -> list[tuple[str, str]]:
        """``(id, name)`` pairs for menus and the editor's list widget."""
        return [(pid, self.palettes[pid].name) for pid in self.ids(appearance=appearance)]

    def upsert(self, palette: Palette) -> None:
        """Insert or replace a user palette."""
        if palette.builtin:
            raise PaletteError(f"refusing to register built-in palette {palette.id!r} as user data")
        existing = self.palettes.get(palette.id)
        if existing is not None and existing.builtin:
            self.palettes[f"{palette.id}-builtin"] = existing
        self.palettes[palette.id] = palette
        if palette.id not in self.order:
            self.order.append(palette.id)
            self.order.sort()

    def iter_builtin(self) -> Iterable[Palette]:
        return (self.palettes[pid] for pid in self.order if self.palettes[pid].builtin)

    def defaults_for(self, appearances: Sequence[Appearance]) -> dict[Appearance, str]:
        """First built-in palette id per requested appearance (settings fallback)."""
        out: dict[Appearance, str] = {}
        for appearance in appearances:
            for pid in self.ids(appearance=appearance):
                if self.palettes[pid].builtin:
                    out[appearance] = pid
                    break
        return out
