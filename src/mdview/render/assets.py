"""Bundled web assets: manifest, on-disk layout, URL resolution (pure).

Everything the page loads comes from here -- **no CDN, ever**.  Two kinds:

* ``static/`` inside the package: ``mdview.css`` and ``app.js`` (ours, ~5 KB).
* ``vendor/`` in the user data dir: KaTeX and Mermaid, downloaded once by
  :mod:`mdview.render.fetch` from pinned npm versions and sha256-locked.
  Keeping them out of the wheel keeps the repo small while keeping the *app*
  fully offline: after the first fetch nothing is ever downloaded again.

The page addresses assets through the ``mdview://`` scheme that the GUI layer
serves (:mod:`mdview.viewer.scheme_handler`).  ``scheme="file"`` exists so the
pure renderer can be exercised against a real browser during development.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

from mdview.core.errors import AssetError
from mdview.core.paths import AppDir, app_dir, package_dir, static_dir

__all__ = [
    "ASSETS",
    "AssetSpec",
    "VendoredReport",
    "asset_url",
    "require_vendored",
    "vendor_dir",
    "vendored_report",
]

SCHEME_HOST_ASSETS = "assets"
SCHEME_HOST_PAGE = "page"
SCHEME_HOST_STATIC = "static"
SCHEME_HOST_DOC = "doc"
LOCKFILE = "lock.json"


@dataclass(frozen=True, slots=True)
class AssetSpec:
    """One npm package we vendor, and which files of it we actually need."""

    name: str
    npm_package: str
    version: str
    #: Glob patterns relative to the npm tarball root.
    include: tuple[str, ...]
    #: Stripped from the tarball path to build the vendor-relative path.
    strip_prefix: str

    @property
    def tarball_url(self) -> str:
        return (
            f"https://registry.npmjs.org/{self.npm_package}/-/{self.npm_package}-{self.version}.tgz"
        )

    def vendor_path(self, tarball_member: str) -> str:
        """``package/dist/fonts/KaTeX_Main.ttf`` -> ``katex/fonts/KaTeX_Main.ttf``."""
        without_root = tarball_member.removeprefix("package/")
        return f"{self.name}/{without_root.removeprefix(self.strip_prefix)}"

    def matches(self, tarball_member: str) -> bool:
        relative = tarball_member.removeprefix("package/")
        return any(fnmatch.fnmatch(relative, pattern) for pattern in self.include)


ASSETS: Final[tuple[AssetSpec, ...]] = (
    AssetSpec(
        name="katex",
        npm_package="katex",
        version="0.19.1",
        include=("dist/katex.min.css", "dist/katex.min.js", "dist/fonts/*"),
        strip_prefix="dist/",
    ),
    AssetSpec(
        name="mermaid",
        npm_package="mermaid",
        version="12.3.0",
        include=("dist/mermaid.min.js",),
        strip_prefix="dist/",
    ),
)


def vendor_dir() -> Path:
    """Where vendored JS/CSS lives (writable, so it can be re-fetched)."""
    return app_dir(AppDir.VENDOR)


def _installed_paths() -> dict[str, Path]:
    """Map ``<asset>/<relative path>`` -> on-disk path for everything in the manifest."""
    root = vendor_dir()
    out: dict[str, Path] = {}
    for spec in ASSETS:
        for pattern in spec.include:
            if "*" in pattern:
                base = root / spec.name / Path(pattern).parent
                if base.is_dir():
                    for path in sorted(base.glob(Path(pattern).name)):
                        out[f"{spec.name}/{path.relative_to(root / spec.name)}"] = path
            else:
                rel = spec.vendor_path(f"package/{pattern}")
                out[rel] = root / rel
    return out


class VendoredReport(TypedDict):
    """JSON-friendly shape of :func:`vendored_report`."""

    vendor_dir: str
    present: int
    missing: list[str]
    versions: dict[str, str]


def vendored_report() -> VendoredReport:
    """Diagnostics for ``mdview assets`` (counts + missing manifest entries)."""
    installed = _installed_paths()
    present = sorted(rel for rel, path in installed.items() if path.is_file())
    missing = sorted(rel for rel, path in installed.items() if not path.is_file())
    lock = vendor_dir() / LOCKFILE
    versions: dict[str, str] = {}
    if lock.is_file():
        try:
            versions = json.loads(lock.read_text(encoding="utf-8")).get("versions", {})
        except (json.JSONDecodeError, OSError):
            versions = {}
    return {
        "vendor_dir": str(vendor_dir()),
        "present": len(present),
        "missing": missing,
        "versions": versions or {spec.name: f"{spec.version} (not fetched)" for spec in ASSETS},
    }


def require_vendored() -> None:
    """Raise a helpful :class:`AssetError` if any manifest file is absent."""
    missing = [rel for rel, path in _installed_paths().items() if not path.is_file()]
    if missing:
        preview = ", ".join(missing[:3])
        more = f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
        raise AssetError(
            f"bundled web assets are incomplete ({len(missing)} missing: {preview}{more}). "
            "Fix: uv run mdview fetch-assets"
        )


def asset_url(name: str, relative: str, *, scheme: str = "mdview") -> str:
    """URL for one vendored file (``asset_url("katex", "katex.min.js")``)."""
    clean = relative.lstrip("/")
    if scheme == "file":
        return f"{(vendor_dir() / name / clean).as_uri()}"
    return f"{scheme}://{SCHEME_HOST_ASSETS}/{name}/{clean}"


def static_url(name: str, *, scheme: str = "mdview") -> str:
    """URL for one of our own static files (``mdview.css``, ``app.js``)."""
    if scheme == "file":
        return (static_dir() / name).as_uri()
    return f"{scheme}://{SCHEME_HOST_STATIC}/{name}"


def static_path(name: str) -> Path:
    """Filesystem path of a packaged static file (read once at import by tests)."""
    return package_dir() / "static" / name
