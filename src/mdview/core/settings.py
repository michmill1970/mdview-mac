"""Typed settings model + JSON persistence (pure).

One file, ``settings.json``, human-readable, written atomically.  Unknown keys
are dropped rather than fatal so that older/newer builds can coexist, and
out-of-range values are clamped instead of raising -- a viewer must always open.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Final

from mdview.core.appearance import AppearanceMode, normalize_appearance_mode
from mdview.core.errors import SettingsError
from mdview.core.fonts import clamp_font_size

__all__ = ["Settings", "load_settings", "load_settings_or_default", "save_settings"]

SETTINGS_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the user can persist except palettes (which are their own files)."""

    version: int = SETTINGS_VERSION
    font_size_px: int = 16
    appearance_mode: AppearanceMode = "system"
    #: Palette used when the resolved appearance is light / dark.
    palette_light: str = "github-light"
    palette_dark: str = "github-dark"
    #: Re-open the files from the previous session on launch.
    restore_session: bool = True
    open_files: tuple[str, ...] = ()
    active_tab_path: str | None = None
    #: Follow system *reduced motion* for smooth scrolling.
    smooth_scrolling: bool = True

    def normalized(self) -> Settings:
        """Return a copy with every value clamped/coerced into its legal range."""
        return replace(
            self,
            version=SETTINGS_VERSION,
            font_size_px=clamp_font_size(self.font_size_px),
            appearance_mode=normalize_appearance_mode(self.appearance_mode),
            palette_light=str(self.palette_light),
            palette_dark=str(self.palette_dark),
            open_files=tuple(str(p) for p in self.open_files),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict (tuples become lists)."""
        return asdict(self.normalized())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Settings:
        """Leniently build settings, ignoring unknown keys and bad scalar types."""
        defaults = cls()
        if not isinstance(raw, Mapping):
            return defaults
        files = raw.get("open_files", defaults.open_files)
        if not isinstance(files, (list, tuple)):
            files = defaults.open_files
        active = raw.get("active_tab_path", defaults.active_tab_path)
        return cls(
            version=SETTINGS_VERSION,
            font_size_px=_as_int(raw.get("font_size_px"), defaults.font_size_px),
            appearance_mode=normalize_appearance_mode(raw.get("appearance_mode")),
            palette_light=_as_str(raw.get("palette_light"), defaults.palette_light),
            palette_dark=_as_str(raw.get("palette_dark"), defaults.palette_dark),
            restore_session=_as_bool(raw.get("restore_session"), defaults.restore_session),
            open_files=tuple(str(p) for p in files),
            active_tab_path=str(active) if isinstance(active, str) else None,
            smooth_scrolling=_as_bool(raw.get("smooth_scrolling"), defaults.smooth_scrolling),
        ).normalized()


def _as_int(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    return value if isinstance(value, int) else fallback


def _as_str(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _as_bool(value: object, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def load_settings(path: Path) -> Settings:
    """Read settings, raising :class:`SettingsError` on corrupt content."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path}: invalid JSON ({exc.msg} at line {exc.lineno})") from exc
    return Settings.from_dict(raw)


def load_settings_or_default(path: Path) -> tuple[Settings, str | None]:
    """Never-fail variant used at startup; second item is a warning to show the user."""
    try:
        return load_settings(path), None
    except (SettingsError, OSError) as exc:
        return Settings(), f"Could not read settings ({exc}); using defaults."


def save_settings(settings: Settings, path: Path) -> None:
    """Write atomically (temp file + ``os.replace``) with 0600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(settings.normalized().to_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    os.chmod(path, 0o600)
