"""Vendored-asset manifest and the fetch pipeline (tested with a fake registry)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from mdview.core.errors import AssetError
from mdview.render import fetch as fetch_module
from mdview.render.assets import ASSETS, asset_url, require_vendored, static_url, vendored_report
from mdview.render.fetch import fetch_assets, verify_assets

KATEX = next(spec for spec in ASSETS if spec.name == "katex")


def make_tarball(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


# ------------------------------------------------------------- AssetSpec --


def test_vendor_path_strips_npm_root_and_prefix() -> None:
    assert KATEX.vendor_path("package/dist/fonts/x.ttf") == "katex/fonts/x.ttf"
    assert KATEX.matches("package/dist/katex.min.js")
    assert not KATEX.matches("package/README.md")
    assert KATEX.tarball_url.startswith("https://registry.npmjs.org/katex/-/katex-")


# ---------------------------------------------------------------- report --


def test_report_lists_everything_missing_initially() -> None:
    report = vendored_report()
    assert report["present"] == 0
    assert "katex/katex.min.js" in report["missing"]
    assert "mermaid/mermaid.min.js" in report["missing"]
    assert str(report["versions"]["katex"]).startswith("0.19.1")


def test_require_vendored_raises_with_fix_hint() -> None:
    with pytest.raises(AssetError, match="fetch-assets"):
        require_vendored()


def test_verify_assets_on_empty_dir() -> None:
    problems = verify_assets()
    assert problems
    assert "lock.json" in problems[0]


# ----------------------------------------------------------------- fetch --


def test_fetch_end_to_end_with_fake_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tarballs = {
        KATEX.tarball_url: make_tarball(
            {
                "package/dist/katex.min.js": b"katex-js",
                "package/dist/katex.min.css": b"katex-css",
                "package/dist/fonts/k.ttf": b"font",
                "package/package.json": b"{}",  # must be ignored
            }
        ),
        next(s for s in ASSETS if s.name == "mermaid").tarball_url: make_tarball(
            {"package/dist/mermaid.min.js": b"mermaid"}
        ),
    }
    monkeypatch.setattr(fetch_module, "_download", tarballs.__getitem__)

    report = fetch_assets()
    assert sorted(report.downloaded) == ["katex@0.19.1", "mermaid@12.3.0"]
    assert report.files_written == 4
    assert report.lock_path is not None
    assert report.lock_path.is_file()

    require_vendored()  # must not raise anymore
    assert vendored_report()["missing"] == []
    assert verify_assets() == []

    # Idempotency: second run downloads nothing.
    second = fetch_assets()
    assert second.downloaded == []
    assert sorted(second.skipped) == ["katex", "mermaid"]


def test_checksum_detects_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = KATEX.tarball_url
    mermaid = next(s for s in ASSETS if s.name == "mermaid")
    monkeypatch.setattr(
        fetch_module,
        "_download",
        {
            url: make_tarball(
                {
                    "package/dist/katex.min.js": b"v1",
                    "package/dist/katex.min.css": b"c",
                    "package/dist/fonts/f.ttf": b"f",
                }
            ),
            mermaid.tarball_url: make_tarball({"package/dist/mermaid.min.js": b"m"}),
        }.__getitem__,
    )
    fetch_assets()
    (Path(fetch_module.vendor_dir()) / "katex" / "katex.min.js").write_bytes(b"tampered")
    problems = verify_assets()
    assert any("checksum mismatch" in p for p in problems)


def test_path_traversal_member_is_rejected(tmp_path: Path) -> None:
    evil = make_tarball({"package/dist/fonts/../../../escape.txt": b"nope"})
    with pytest.raises(AssetError, match="unsafe path"):
        fetch_module._extract(KATEX, evil, tmp_path)


def test_sibling_prefix_escape_is_rejected(tmp_path: Path) -> None:
    """``/x/vendor-evil`` must not pass a ``/x/vendor`` containment check.

    The old string-prefix guard let exactly this through and wrote the file
    outside the root before crashing in ``relative_to``.
    """
    root = tmp_path / "vendor"
    root.mkdir()
    evil = make_tarball({"package/dist/fonts/../../../vendor-evil/e.ttf": b"nope"})
    with pytest.raises(AssetError, match="unsafe path"):
        fetch_module._extract(KATEX, evil, root)
    assert not (tmp_path / "vendor-evil").exists()


# ------------------------------------------------- symlinked roots (macOS) --


def test_extract_survives_symlinked_vendor_root(tmp_path: Path) -> None:
    """Reproduces the macOS field failure on Linux.

    On macOS ``/tmp``/``/var`` are symlinks to ``/private/...``, so extraction
    targets come back resolved while the root does not.  A Linux symlink to
    the vendor dir simulates that condition exactly: the old code raised
    ``ValueError`` from ``relative_to`` (28 test errors on the Mac).
    """
    real = tmp_path / "vendor-real"
    real.mkdir()
    link = tmp_path / "vendor-link"
    link.symlink_to(real, target_is_directory=True)
    tarball = make_tarball(
        {
            "package/dist/katex.min.js": b"katex-js",
            "package/dist/katex.min.css": b"katex-css",
        }
    )
    hashes = fetch_module._extract(KATEX, tarball, link)  # unresolved root!
    assert sorted(hashes) == ["katex/katex.min.css", "katex/katex.min.js"]
    assert (real / "katex" / "katex.min.js").read_bytes() == b"katex-js"


def test_fetch_end_to_end_with_symlinked_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full fetch -> verify -> idempotency cycle behind a symlinked data dir."""
    from mdview.core import paths

    real = tmp_path / "data"
    real.mkdir()
    link = tmp_path / "data-link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(link))
    mermaid = next(s for s in ASSETS if s.name == "mermaid")
    monkeypatch.setattr(
        fetch_module,
        "_download",
        {
            KATEX.tarball_url: make_tarball(
                {
                    "package/dist/katex.min.js": b"k",
                    "package/dist/katex.min.css": b"c",
                    "package/dist/fonts/f.ttf": b"f",
                }
            ),
            mermaid.tarball_url: make_tarball({"package/dist/mermaid.min.js": b"m"}),
        }.__getitem__,
    )

    report = fetch_assets()
    assert sorted(report.downloaded) == ["katex@0.19.1", "mermaid@12.3.0"]
    assert report.files_written == 4
    assert verify_assets() == []
    require_vendored()

    second = fetch_assets()
    assert second.downloaded == []  # still detected as up to date via the symlink


def test_empty_match_raises_helpful_error() -> None:
    blob = make_tarball({"package/README.md": b"x"})
    with pytest.raises(AssetError, match="package layout changed"):
        fetch_module._extract(KATEX, blob, Path("/tmp/whatever"))


def test_corrupt_lock_is_reported(tmp_path: Path) -> None:
    root = fetch_module.vendor_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "lock.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(AssetError, match="corrupt"):
        fetch_module._read_lock(root)


# ------------------------------------------------------------------ URLs --


def test_asset_and_static_urls() -> None:
    assert asset_url("katex", "katex.min.js") == "mdview://assets/katex/katex.min.js"
    assert static_url("app.js") == "mdview://static/app.js"
    assert asset_url("katex", "/katex.min.css").count("//") == 1  # only the scheme's "//"
    assert asset_url("katex", "katex.min.js", scheme="file").startswith("file://")
