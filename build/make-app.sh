#!/bin/sh
# Build MDView.app for personal use — run on an Apple-silicon Mac.
#
#   ./build/make-app.sh      # build + framework repair + ad-hoc sign + verify
#
# Re-running is safe: PyInstaller runs with --clean --noconfirm, the
# framework repair below is guarded (no-op once applied), and the signing
# steps are idempotent (codesign --force replaces old signatures).
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

# PyInstaller 6.x writes the BUNDLE to dist/MDView.app (top level), NOT
# inside the sibling dist/MDView/ COLLECT support directory — that dir is
# duplicated weight we neither sign nor ship; just don't look for the .app
# inside it.
APP="dist/MDView.app"
if [ ! -d "$APP" ]; then
    echo "error: $APP was not produced — check the PyInstaller output above" >&2
    exit 1
fi

# PyInstaller misplaces the QtWebEngineProcess.app helper tree inside its
# frameworks: it lands at <fw>/Versions/Resources/Helpers/ instead of
# <fw>/Versions/A/Helpers/.  Two failures follow from that:
#   1. the framework-root Helpers symlink (-> Versions/Current/Helpers ->
#      Versions/A/Helpers) dangles, so Qt never finds QtWebEngineProcess at
#      launch ("Qt WebEngine Process" search-path log, then Abort trap: 6);
#   2. codesign --verify --strict rejects the framework: "embedded framework
#      contains modified or invalid version".
# Move the tree back under Versions/A/ and drop the stray Versions/Resources
# if it is now empty.  Glob covers every *.framework, not just
# QtWebEngineCore; guarded by A/Helpers being absent, so re-runs are no-ops.
find "$APP" -type d -name '*.framework' | while IFS= read -r fw; do
    stray="$fw/Versions/Resources/Helpers"
    if [ -d "$stray" ] && [ ! -d "$fw/Versions/A/Helpers" ]; then
        echo "repairing framework: $fw"
        mv "$stray" "$fw/Versions/A/Helpers"
        rmdir "$fw/Versions/Resources" 2>/dev/null \
            || echo "warning: $fw/Versions/Resources not empty — left in place" >&2
    fi
done

# Qt resolves the helper through the framework-root symlink chain; confirm
# it lands on the real binary before signing anything (a dangling chain
# here is exactly the launch crash).
qwc="$(find "$APP" -type d -name 'QtWebEngineCore.framework' | head -n 1 || true)"
if [ -n "$qwc" ] && [ ! -x "$qwc/Helpers/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess" ]; then
    echo "error: $qwc/Helpers/QtWebEngineProcess.app does not resolve through the Versions symlink chain — framework repair did not take" >&2
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

# Sign the repaired framework, then the whole bundle (helper signed above).
# A --strict verify failure after all this is a real problem — do not work
# around it, report the exact codesign error above.
if [ -n "$qwc" ]; then
    echo "signing framework: $qwc"
    codesign --force --sign - "$qwc"
fi
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP" || {
    echo "error: codesign verification failed (exact error above) — do not ship this bundle" >&2
    exit 1
}

echo "built: $APP"
echo "double-click a .md file (registers via Info.plist) or: open \"$APP\" examples/demo.md"
