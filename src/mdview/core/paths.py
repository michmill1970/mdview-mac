"""Application directories.

Every writable path in mdview goes through this module.  Unit tests redirect
the whole app by setting :data:`DATA_DIR_ENV_VAR` to a ``tmp_path``, which is
why nothing else in the codebase may call ``QStandardPaths`` / ``os.path.expanduser``.

Layout on macOS (``$HOME/Library/Application Support/MDView``)::

    settings.json
    palettes/<id>.json        # user palettes only; built-ins live in the wheel
    vendor/                   # downloaded KaTeX/Mermaid (see mdview.render.fetch)
"""

from __future__ import annotations

import os
import sys
from enum import StrEnum
from pathlib import Path

__all__ = ["AppDir", "builtin_palettes_dir", "data_dir", "package_dir", "static_dir"]

APP_NAME = "MDView"
"""Bundle/display name used for directory names and Info.plist keys."""

DATA_DIR_ENV_VAR = "MDVIEW_DATA_DIR"
"""Set this to relocate all writable state (used by tests and for portable use)."""


class AppDir(StrEnum):
    """Named writable locations under :func:`data_dir`."""

    PALETTES = "palettes"
    VENDOR = "vendor"
    CACHE = "cache"


def package_dir() -> Path:
    """Directory of the installed ``mdview`` package."""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Root for user state (settings, user palettes, vendored assets)."""
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":  # pragma: no cover - not a target platform
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "mdview"


def app_dir(kind: AppDir) -> Path:
    """Return (and create) one of the named writable sub-directories."""
    path = data_dir() / kind.value
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    """Path of ``settings.json`` (parent directory is created)."""
    app_dir(AppDir.CACHE)  # ensures data_dir() exists before the file is written
    return data_dir() / "settings.json"


def builtin_palettes_dir() -> Path:
    """Read-only directory with the shipped palette presets."""
    return package_dir() / "palettes"


def static_dir() -> Path:
    """Read-only directory with ``mdview.css`` / ``app.js``."""
    return package_dir() / "static"
