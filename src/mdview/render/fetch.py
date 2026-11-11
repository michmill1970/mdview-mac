"""Download and verify the vendored web assets (pure, network + disk only).

Why a fetch script instead of committing KaTeX/Mermaid: the versions are
pinned in :data:`mdview.render.assets.ASSETS` and checksummed in
``vendor/lock.json``, so reproducibility comes from the manifest rather than
from ~4 MB of minified blobs in git -- while the *app* still never touches the
network after this one-time step (requirement: fully offline).

Run via ``uv run mdview fetch-assets`` (idempotent; ``--force`` re-downloads).
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Final

from mdview.core.errors import AssetError
from mdview.render.assets import ASSETS, AssetSpec, LOCKFILE, vendor_dir

__all__ = ["FetchReport", "fetch_assets", "verify_assets"]

_TIMEOUT_SECONDS: Final = 60.0


@dataclass(slots=True)
class FetchReport:
    """What one ``fetch_assets()`` run did."""

    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    files_written: int = 0
    lock_path: Path | None = None

    def summary(self) -> str:
        return (
            f"fetched {len(self.downloaded)} package(s) "
            f"({self.files_written} files), reused {len(self.skipped)}, "
            f"lock={self.lock_path}"
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AssetError(f"could not download {url}: {exc}") from exc


def _read_lock(root: Path) -> dict[str, object]:
    path = root / LOCKFILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise AssetError(f"{path} is corrupt ({exc}); delete it and re-fetch") from exc
    return data if isinstance(data, dict) else {}


def _extract(spec: AssetSpec, tarball: bytes, root: Path) -> dict[str, str]:
    """Extract the manifest-matching members of an npm tarball; returns hashes."""
    hashes: dict[str, str] = {}
    with tarfile.open(fileobj=BytesIO(tarball), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not spec.matches(member.name):
                continue
            target = (root / spec.vendor_path(member.name)).resolve()
            if not str(target).startswith(str(root.resolve())):  # tar path-traversal guard
                raise AssetError(f"unsafe path in tarball: {member.name}")
            handle = archive.extractfile(member)
            if handle is None:  # pragma: no cover - getmembers said it is a file
                continue
            data = handle.read()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            hashes[str(target.relative_to(root))] = _sha256(data)
    if not hashes:
        raise AssetError(
            f"{spec.npm_package} tarball contained none of {spec.include} -- "
            "the package layout changed; update mdview.render.assets.ASSETS"
        )
    return hashes


def fetch_assets(*, force: bool = False) -> FetchReport:
    """Fetch every manifest entry that is not already on disk."""
    root = vendor_dir()
    lock = _read_lock(root)
    versions = lock.get("versions", {})
    files = lock.get("files", {})
    versions = dict(versions) if isinstance(versions, dict) else {}
    files = dict(files) if isinstance(files, dict) else {}
    report = FetchReport()

    for spec in ASSETS:
        marker = root / spec.name
        up_to_date = (
            not force
            and marker.is_dir()
            and versions.get(spec.name) == spec.version
            and any(rel.startswith(f"{spec.name}/") for rel in files)
        )
        if up_to_date:
            report.skipped.append(spec.name)
            continue
        data = _download(spec.tarball_url)
        hashes = _extract(spec, data, root)
        for rel, digest in hashes.items():
            files[rel] = digest
        versions[spec.name] = spec.version
        report.downloaded.append(f"{spec.name}@{spec.version}")
        report.files_written += len(hashes)

    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / LOCKFILE
    lock_path.write_text(
        json.dumps({"versions": versions, "files": files}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report.lock_path = lock_path
    return report


def verify_assets() -> list[str]:
    """Return a list of human-readable problems (empty means the vendor dir is sound)."""
    root = vendor_dir()
    lock = _read_lock(root)
    files = lock.get("files", {})
    problems: list[str] = []
    if not isinstance(files, dict) or not files:
        return ["vendor/lock.json is missing or empty -- run: uv run mdview fetch-assets"]
    for rel, expected in sorted(files.items()):
        path = root / str(rel)
        if not path.is_file():
            problems.append(f"missing: {rel}")
            continue
        if isinstance(expected, str) and _sha256(path.read_bytes()) != expected:
            problems.append(f"checksum mismatch: {rel}")
    return problems
