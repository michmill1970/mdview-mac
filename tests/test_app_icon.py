"""Phase 11: the app icon's committed artwork and its wiring into the bundle.

Nothing here needs macOS, ``iconutil`` or an SVG renderer — the .icns is a
build artefact produced on the Mac by ``build/make-app.sh``, so these tests
assert everything that *is* checkable anywhere: the iconset is complete, named
exactly the way ``iconutil`` demands, sized exactly the way its names promise,
and referenced by both the spec and the build script.

The one macOS-only command in the build script's icon gate is ``plutil``;
``run_icon_gate`` re-runs the real gate logic from ``make-app.sh`` against a
synthetic bundle with ``plutil`` replaced by a ``plistlib``-backed shim, so the
gate's resolution rules (and its failure modes) are covered on Linux too.
"""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import struct
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SVG_MASTER = REPO / "assets" / "mdview-icon.svg"
ICONSET = REPO / "assets" / "icons" / "iconset"
SPEC = REPO / "build" / "mdview.spec"
MAKE_APP = REPO / "build" / "make-app.sh"
GITIGNORE = REPO / ".gitignore"

#: The exact ten file names iconutil requires (it rejects the set otherwise).
ICONSET_NAMES = (
    "icon_16x16.png",
    "icon_16x16@2x.png",
    "icon_32x32.png",
    "icon_32x32@2x.png",
    "icon_128x128.png",
    "icon_128x128@2x.png",
    "icon_256x256.png",
    "icon_256x256@2x.png",
    "icon_512x512.png",
    "icon_512x512@2x.png",
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FAKE_ICNS = b"icns" + b"\x00" * 12 + b"generated-by-the-build"


def _png_header(path: Path) -> tuple[int, int, int]:
    """(width, height, color_type) straight from the PNG IHDR chunk."""
    data = path.read_bytes()
    assert data[:8] == PNG_SIGNATURE, f"{path} is not a PNG"
    assert data[12:16] == b"IHDR", f"{path} has no IHDR chunk"
    width, height, _bit_depth, color_type = struct.unpack_from(">IIBB", data, 16)
    return width, height, color_type


def _declared_px(name: str) -> int:
    """icon_512x512@2x.png -> 1024: the pixel edge its iconutil name declares."""
    match = re.fullmatch(r"icon_(\d+)x\1(@2x)?\.png", name)
    assert match, f"{name} is not an iconutil icon name"
    return int(match.group(1)) * (2 if match.group(2) else 1)


# --- the committed artwork ---------------------------------------------------


def test_svg_master_is_committed_at_1024() -> None:
    """assets/mdview-icon.svg is the source of truth for the rendered set."""
    text = SVG_MASTER.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<svg"), f"{SVG_MASTER} is not an SVG document"
    assert 'width="1024"' in text
    assert 'height="1024"' in text
    assert 'viewBox="0 0 1024 1024"' in text


def test_iconset_holds_exactly_the_ten_iconutil_names() -> None:
    """Missing or extra entries make iconutil fail with 'Failed to generate ICNS'."""
    present = sorted(p.name for p in ICONSET.iterdir() if p.is_file())
    assert present == sorted(ICONSET_NAMES), f"{ICONSET} must hold exactly the iconutil set"


@pytest.mark.parametrize("name", ICONSET_NAMES)
def test_each_iconset_png_matches_its_name(name: str) -> None:
    """iconutil cross-checks each file's real size against its name."""
    width, height, color_type = _png_header(ICONSET / name)
    edge = _declared_px(name)
    assert (width, height) == (edge, edge), f"{name} is {width}x{height}, wants {edge}x{edge}"
    # RGBA (6): the squircle corners are transparent, which is what lets macOS
    # composite the icon instead of showing a black square.
    assert color_type == 6, f"{name} is not RGBA (color type {color_type})"


def test_generated_icns_is_not_tracked_in_git() -> None:
    """The .icns and its staging dir are build artefacts, regenerated every run."""
    gitignore = GITIGNORE.read_text(encoding="utf-8")
    assert "/build/MDView.icns" in gitignore
    assert "/build/.iconset/" in gitignore


# --- the wiring in the macOS build ------------------------------------------


def test_spec_installs_the_icns_and_names_it_in_the_plist() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    assert re.search(r'^ICNS = ROOT / "build" / "MDView\.icns"$', spec, re.M), (
        "the spec must point at the icns make-app.sh generates"
    )
    assert re.search(r"^\s+icon=str\(ICNS\),$", spec, re.M), "BUNDLE(icon=...) must pass the icns"
    assert '"CFBundleIconFile": ICNS.name' in spec, "CFBundleIconFile must come from the same path"
    # A relative icon would be resolved against build/, not the repo root.
    assert not re.search(r"""icon=['"]build/""", spec), (
        "a relative icon path resolves against build/"
    )


def test_make_app_generates_the_icns_before_pyinstaller_runs() -> None:
    script = MAKE_APP.read_text(encoding="utf-8")
    iconutil = script.index('iconutil -c icns "$ICONSET_STAGE"')
    pyinstaller = script.index("build/mdview.spec")
    assert script.index('ICNS="build/MDView.icns"') < iconutil < pyinstaller
    # iconutil refuses a directory without the `.iconset` suffix ("Invalid
    # Iconset"), so the staged copy — never assets/icons/iconset itself — is
    # what it is pointed at.
    assert 'ICONSET_STAGE="build/.iconset/MDView.iconset"' in script
    assert 'cp "$ICONSET_SRC"/icon_*.png "$ICONSET_STAGE"/' in script
    # Fail hard, both on a missing tool and on a missing/empty product.
    assert "command -v iconutil >/dev/null 2>&1" in script
    assert 'if [ ! -s "$ICNS" ]; then' in script


def test_make_app_gates_the_installed_icon_before_signing() -> None:
    script = MAKE_APP.read_text(encoding="utf-8")
    gate = script.index('verify_icon "$APP"')
    assert script.index("build/mdview.spec") < gate < script.index("codesign --force --deep")
    assert 'vi_res="$vi_app/Contents/Resources"' in script
    assert '"$vi_res/MDView.icns"' in script
    assert "plutil -extract CFBundleIconFile raw -o -" in script
    assert script.startswith("#!/bin/sh")
    assert re.search(r"^set -eu$", script, re.M), "the gate relies on set -e to abort the build"


# --- the gate's own logic, with only plutil shimmed --------------------------


_PLUTIL_SHIM = """#!/usr/bin/env python3
import plistlib
import sys

argv = sys.argv[1:]
if argv[:1] != ["-extract"] or argv[2:5] != ["raw", "-o", "-"] or len(argv) != 6:
    print("plutil shim: unsupported invocation: %r" % (argv,), file=sys.stderr)
    raise SystemExit(2)
with open(argv[5], "rb") as handle:
    info = plistlib.load(handle)
if argv[1] not in info:
    print("plutil: Could not extract CFBundleIconFile: key not found", file=sys.stderr)
    raise SystemExit(1)
sys.stdout.write(str(info[argv[1]]))
"""


def _verify_icon_source() -> str:
    """The real gate function, lifted verbatim out of make-app.sh."""
    match = re.search(
        r"^verify_icon\(\) \{.*?^\}$", MAKE_APP.read_text(encoding="utf-8"), re.M | re.S
    )
    assert match, "make-app.sh must define verify_icon() (the post-build icon gate)"
    return match.group(0)


def _fake_bundle(root: Path, *, plist_value: str | None, install_icns: bool = True) -> Path:
    """A minimal MDView.app whose Info.plist is an XML plist, like PyInstaller's."""
    app = root / "MDView.app"
    (app / "Contents" / "Resources").mkdir(parents=True)
    if install_icns:
        (app / "Contents" / "Resources" / "MDView.icns").write_bytes(FAKE_ICNS)
    info: dict[str, object] = {"CFBundleName": "MDView", "CFBundleExecutable": "MDView"}
    if plist_value is not None:
        info["CFBundleIconFile"] = plist_value
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    return app


@pytest.fixture
def run_icon_gate(tmp_path: Path):
    """Run make-app.sh's verify_icon() against a synthetic bundle."""
    shim_dir = tmp_path / "shim-bin"
    shim_dir.mkdir()
    shim = shim_dir / "plutil"
    shim.write_text(_PLUTIL_SHIM, encoding="utf-8")
    shim.chmod(0o755)

    def run(app: Path, *, generated: bytes = FAKE_ICNS) -> subprocess.CompletedProcess[str]:
        icns = tmp_path / "MDView.icns"  # what iconutil produced earlier this run
        icns.write_bytes(generated)
        gate = tmp_path / "icon-gate.sh"
        gate.write_text(
            "#!/bin/sh\nset -eu\n"
            f"ICNS={shlex.quote(str(icns))}\n"
            f"{_verify_icon_source()}\n"
            'verify_icon "$1"\n',
            encoding="utf-8",
        )
        gate.chmod(0o755)
        env = dict(os.environ, PATH=f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
        return subprocess.run(
            ["/bin/sh", str(gate), str(app)], capture_output=True, text=True, env=env
        )

    return run


def test_gate_accepts_a_bundle_whose_plist_resolves_to_the_icns(
    tmp_path: Path, run_icon_gate
) -> None:
    app = _fake_bundle(tmp_path / "good", plist_value="MDView.icns")
    result = run_icon_gate(app)
    assert result.returncode == 0, result.stderr
    assert "CFBundleIconFile=MDView.icns" in result.stdout
    assert str(app / "Contents" / "Resources" / "MDView.icns") in result.stdout


def test_gate_accepts_the_extensionless_plist_form(tmp_path: Path, run_icon_gate) -> None:
    """Launch Services also accepts 'MDView'; the gate must resolve it too."""
    app = _fake_bundle(tmp_path / "bare", plist_value="MDView")
    assert run_icon_gate(app).returncode == 0


def test_gate_fails_when_the_plist_has_no_icon_key(tmp_path: Path, run_icon_gate) -> None:
    app = _fake_bundle(tmp_path / "no-key", plist_value=None)
    result = run_icon_gate(app)
    assert result.returncode != 0
    assert "no CFBundleIconFile" in result.stderr


def test_gate_fails_when_the_named_file_is_not_in_resources(tmp_path: Path, run_icon_gate) -> None:
    app = _fake_bundle(tmp_path / "wrong-name", plist_value="Stale.icns")
    result = run_icon_gate(app)
    assert result.returncode != 0
    assert "does not resolve to a file" in result.stderr


def test_gate_fails_when_the_icns_was_never_installed(tmp_path: Path, run_icon_gate) -> None:
    app = _fake_bundle(tmp_path / "no-file", plist_value="MDView.icns", install_icns=False)
    result = run_icon_gate(app)
    assert result.returncode != 0
    assert "did not install the icns" in result.stderr


def test_gate_fails_when_the_bundled_icns_is_stale(tmp_path: Path, run_icon_gate) -> None:
    app = _fake_bundle(tmp_path / "stale", plist_value="MDView.icns")
    result = run_icon_gate(app, generated=FAKE_ICNS + b"fresh")
    assert result.returncode != 0
    assert "differs from the freshly generated" in result.stderr
