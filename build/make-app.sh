#!/usr/bin/env bash
# Build MDView.app for personal use — run on an Apple-silicon Mac.
#
#   ./build/make-app.sh              # build + ad-hoc sign + launch the demo
#   MDVIEW_WITH_VENDOR=1 ./build/make-app.sh   # also ship the fetched vendor tree
#
# The app is ad-hoc signed ("-"): fine on your own machine, not distributable.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "error: install uv first (https://docs.astral.sh/uv/)" >&2; exit 1; }

# --workpath keeps PyInstaller's intermediates out of the tracked build/ dir.
uv run --with pyinstaller pyinstaller \
    --clean --noconfirm \
    --distpath dist --workpath dist/.work \
    build/mdview.spec

APP="dist/MDView.app"

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

codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "built: $APP"
echo "double-click a .md file (registers via Info.plist) or: open \"$APP\" examples/demo.md"
