#!/bin/sh
# Build MDView.app for personal use — run on an Apple-silicon Mac.
#
#   ./build/make-app.sh      # icon + build + framework repair + ad-hoc sign + verify
#
# Re-running is safe: the icns is regenerated from the committed iconset on
# every run, PyInstaller runs with --clean --noconfirm, the framework repair
# below is guarded (no-op once applied), and the signing steps are idempotent
# (codesign --force replaces old signatures).
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

# --- App icon (iconutil -> build/MDView.icns) ---------------------------------
# assets/mdview-icon.svg (1024px) is the source of truth; assets/icons/iconset/
# holds the 10 rendered, iconutil-named PNGs (transparent squircle corners).
# Both are committed: rendering the SVG's filters needs a renderer this Linux
# container does not have, so the icns — not the artwork — is the build artefact.
#
# iconutil insists the input DIRECTORY itself carries the `.iconset` suffix:
# pointed at assets/icons/iconset it aborts with the useless
# "<dir>:error: Invalid Iconset" — Apple's iconset docs require the extension,
# and that exact error is the standard symptom of a suffix-less folder.  So the
# 10 PNGs are staged into a `.iconset`-named copy first, which also guarantees
# no stray file (e.g. .DS_Store) enters the icon set.
ICONSET_SRC="assets/icons/iconset"
ICNS="build/MDView.icns"
ICONSET_STAGE="build/.iconset/MDView.iconset" # gitignored staging dir

# macOS-only tool: this script is macOS-only, so a missing iconutil is a hard
# environment error, never something to work around.
command -v iconutil >/dev/null 2>&1 || {
    echo "error: iconutil not found — it ships with macOS; run this script on a Mac" >&2
    exit 1
}
[ -d "$ICONSET_SRC" ] || {
    echo "error: $ICONSET_SRC missing — the committed iconset is the icon's only source" >&2
    exit 1
}
# iconutil's own failure text is just "Failed to generate ICNS", so name the
# missing entries here instead (all 10 are required for a complete set).
for icon_name in 16x16 16x16@2x 32x32 32x32@2x 128x128 128x128@2x 256x256 256x256@2x \
    512x512 512x512@2x; do
    if [ ! -f "$ICONSET_SRC/icon_$icon_name.png" ]; then
        echo "error: $ICONSET_SRC/icon_$icon_name.png missing — iconset is incomplete" >&2
        exit 1
    fi
done

rm -rf build/.iconset
mkdir -p "$ICONSET_STAGE"
cp "$ICONSET_SRC"/icon_*.png "$ICONSET_STAGE"/
rm -f "$ICNS"
iconutil -c icns "$ICONSET_STAGE" -o "$ICNS" || {
    echo "error: iconutil -c icns $ICONSET_STAGE -o $ICNS failed" >&2
    exit 1
}
# Fail hard now rather than inside PyInstaller's spec execution (where the
# error surfaces as "Icon input file ... not found" after minutes of analysis).
if [ ! -s "$ICNS" ]; then
    echo "error: iconutil produced no $ICNS" >&2
    exit 1
fi
echo "icon: $ICNS ($(wc -c <"$ICNS" | tr -d ' ') bytes)"

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

# --- Post-build gate: the icon is really in the bundle -----------------------
# PyInstaller copies BUNDLE(icon=...) to Contents/Resources/<basename> and
# writes CFBundleIconFile with that same basename (PyInstaller 6.22.3,
# building/osx.py).  Assert both, and that the key resolves to an actual file,
# because the failure mode is silent: the bundle launches perfectly while
# Finder/Dock keep showing the generic app icon.  Checking before signing also
# keeps a broken icon from wasting a `codesign --deep` pass.
#
# The gate is a function so tests/test_app_icon.py can run exactly this shell
# logic on Linux/macOS CI against a synthetic bundle, with only `plutil`
# shimmed (the one macOS-only command in it).
verify_icon() {
    vi_app="$1"
    vi_res="$vi_app/Contents/Resources"
    vi_plist="$vi_app/Contents/Info.plist"

    if [ ! -f "$vi_res/MDView.icns" ]; then
        echo "error: $vi_res/MDView.icns missing — BUNDLE(icon=...) did not install the icns" >&2
        return 1
    fi
    if [ ! -f "$vi_plist" ]; then
        echo "error: $vi_plist missing — cannot read CFBundleIconFile" >&2
        return 1
    fi
    command -v plutil >/dev/null 2>&1 || {
        echo "error: plutil not found — it ships with macOS; cannot verify CFBundleIconFile" >&2
        return 1
    }
    # `plutil -extract ... raw` exits non-zero when the key is absent, and
    # prints the reason itself — do not swallow stderr, it is the diagnosis.
    vi_key="$(plutil -extract CFBundleIconFile raw -o - "$vi_plist")" || {
        echo "error: $vi_plist has no CFBundleIconFile — Launch Services will not use the icns" >&2
        return 1
    }
    if [ -z "$vi_key" ]; then
        echo "error: CFBundleIconFile is empty in $vi_plist" >&2
        return 1
    fi
    # macOS accepts the value with or without the extension; resolve whichever
    # form the plist actually uses.
    case "$vi_key" in
        *.icns) vi_file="$vi_res/$vi_key" ;;
        *) vi_file="$vi_res/$vi_key.icns" ;;
    esac
    if [ ! -f "$vi_file" ]; then
        echo "error: CFBundleIconFile=$vi_key does not resolve to a file under $vi_res" >&2
        return 1
    fi
    # Byte-identical to the icns this run generated: rules out a stale icon
    # surviving from an earlier build (PyInstaller copies the file unchanged).
    if ! cmp -s "$ICNS" "$vi_file"; then
        echo "error: $vi_file differs from the freshly generated $ICNS" >&2
        return 1
    fi
    echo "icon gate: $vi_app CFBundleIconFile=$vi_key -> $vi_file" \
        "$(wc -c <"$vi_file" | tr -d ' ') bytes (identical to $ICNS)"
}

verify_icon "$APP"

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
