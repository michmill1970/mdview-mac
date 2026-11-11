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

# PyInstaller's macOS framework copy-out mangles Qt's non-standard
# frameworks badly (verified on the Mac, PySide6 6.12): the payload lands
# under <fw>/Versions/Resources/ instead of <fw>/Versions/A/:
#   - Helpers/          the QtWebEngineProcess.app helper tree (Phase 8);
#   - Resources/        the ENTIRE framework Resources payload, under a
#                       doubled "Resources" — qtwebengine_resources*.pak,
#                       icudtl.dat, qtwebengine_locales/,
#                       v8_context_snapshot.*.bin, Info.plist,
#                       PrivacyInfo.xcprivacy (found on the real bundle);
#   - anything else     further stray entries.
# The framework-root symlinks (Helpers -> Versions/Current/Helpers,
# Resources -> Versions/Current/Resources) then dangle: QtWebEngine never
# finds QtWebEngineProcess, cannot resolve qtwebengine_resources.pak or
# icudtl.dat, and aborts at launch (Abort trap: 6).  Move everything back
# under Versions/A/: Helpers only when A/Helpers is still absent (Phase 8
# semantics), the doubled Resources/ merged into A/Resources/ overwriting
# duplicates (A/Resources/ initially holds only Info.plist there), and any
# further stray entry moved to A/.  Then drop the emptied
# Versions/Resources.  Glob covers every *.framework, not just
# QtWebEngineCore; after a completed repair Versions/Resources is gone, so
# re-runs are no-ops.

# merge_into SRC DST: move every entry of SRC into DST; existing files are
# overwritten, existing directories merged (a plain `mv` of a colliding
# directory would nest it instead of merging).
merge_into() {
    mi_src="$1"
    mi_dst="$2"
    for mi_entry in "$mi_src"/*; do
        [ -e "$mi_entry" ] || [ -L "$mi_entry" ] || continue
        mi_name="${mi_entry##*/}"
        if [ -d "$mi_entry" ] && [ -d "$mi_dst/$mi_name" ]; then
            cp -R "$mi_entry/." "$mi_dst/$mi_name/"
            rm -rf "$mi_entry"
        else
            mv -f "$mi_entry" "$mi_dst/"
        fi
    done
}

find "$APP" -type d -name '*.framework' | while IFS= read -r fw; do
    stray="$fw/Versions/Resources"
    [ -d "$stray" ] || continue
    echo "repairing framework: $fw"
    if [ -d "$stray/Helpers" ] && [ ! -d "$fw/Versions/A/Helpers" ]; then
        mv -f "$stray/Helpers" "$fw/Versions/A/Helpers"
    fi
    if [ -d "$stray/Resources" ]; then
        mkdir -p "$fw/Versions/A/Resources"
        merge_into "$stray/Resources" "$fw/Versions/A/Resources"
    fi
    merge_into "$stray" "$fw/Versions/A"
    rmdir "$stray" 2>/dev/null \
        || echo "warning: $stray not empty — left in place" >&2
done

# Qt resolves the helper and the Resources payload through the
# framework-root symlink chain; confirm everything QtWebEngine needs at
# launch resolves through it before signing anything — a dangling chain
# here is exactly the launch crash.
qwc="$(find "$APP" -type d -name 'QtWebEngineCore.framework' | head -n 1 || true)"
if [ -n "$qwc" ]; then
    if [ ! -x "$qwc/Helpers/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess" ]; then
        echo "error: $qwc/Helpers/QtWebEngineProcess.app does not resolve through the Versions symlink chain — framework repair did not take" >&2
        exit 1
    fi
    for probe in "Resources/qtwebengine_resources.pak" "Resources/icudtl.dat"; do
        if [ ! -e "$qwc/$probe" ]; then
            echo "error: $qwc/$probe does not resolve through the framework-root Resources symlink — QtWebEngine aborts at launch" >&2
            exit 1
        fi
    done
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
if [ -n "$qwc" ]; then
    echo "signing framework: $qwc"
    codesign --force --sign - "$qwc"
fi
codesign --force --deep --sign - "$APP"
# Verify deliberately WITHOUT --strict: Qt ships non-standard frameworks —
# PySide6's QtWebEngineCore.framework has NO Versions/A/Info.plist even in
# the pristine wheel — so `codesign --verify --strict` fails with "embedded
# framework contains modified or invalid version" on ANY PySide6 bundle,
# healthy or not (verified on the Mac).  Non-strict --deep verify still
# catches the failures that actually bite (broken or invalidated
# signatures, unsigned/dangling helper) and passes (exit 0) on the
# repaired bundle.
codesign --verify --deep --verbose=2 "$APP" || {
    echo "error: codesign verification failed (exact error above) — do not ship this bundle" >&2
    exit 1
}

echo "built: $APP"
echo "double-click a .md file (registers via Info.plist) or: open \"$APP\" examples/demo.md"
