#!/bin/sh
# Build MDView.app for personal use — run on an Apple-silicon Mac.
#
#   ./build/make-app.sh          # build + ad-hoc sign + verify
#
# Re-running is safe: PyInstaller runs with --clean --noconfirm and the
# signing steps are idempotent (codesign --force replaces old signatures).
#
# Vendored KaTeX/Mermaid are deliberately NOT frozen into the bundle: they
# live in the user data dir (~/Library/Application Support/MDView/vendor)
# and the app fetches them once on first launch (`mdview fetch-assets`);
# afterwards it is fully offline.  See DESIGN.md §9.
#
# The app is ad-hoc signed ("-"): fine on your own machine, not distributable.
#
# POSIX sh only (runs under macOS /bin/sh) — no bash-isms.
set -eu
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "error: install uv first (https://docs.astral.sh/uv/)" >&2; exit 1; }

# --workpath keeps PyInstaller's intermediates out of the tracked build/ dir.
uv run --with pyinstaller pyinstaller \
    --clean --noconfirm \
    --distpath dist --workpath dist/.work \
    build/mdview.spec

# onedir + BUNDLE: the .app nests inside the COLLECT directory of the same name.
APP="dist/MDView/MDView.app"
if [ ! -d "$APP" ]; then
    echo "error: $APP was not produced — check the PyInstaller output above" >&2
    exit 1
fi

# The nested QtWebEngineProcess.app MUST be re-signed innermost-first, or the
# outer ad-hoc signature invalidates the helper and the window renders blank
# on arm64 (see DESIGN.md §9).
helper="$(find "$APP" -path '*QtWebEngineProcess.app' -type d | head -n 1 || true)"
if [ -n "$helper" ]; then
    echo "signing helper: $helper"
    codesign --force --sign - "$helper/Contents/MacOS/QtWebEngineProcess"
    codesign --force --sign - "$helper"
else
    echo "warning: no QtWebEngineProcess.app found inside $APP" >&2
fi

# Ad-hoc sign the bundle (inner helpers already signed above), then verify.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "built: $APP"
echo "double-click a .md file (registers via Info.plist) or: open \"$APP\" examples/demo.md"
