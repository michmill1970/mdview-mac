# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MDView.app (personal use, ad-hoc signed).

Run via ``build/make-app.sh`` (which also handles the mandatory nested
``QtWebEngineProcess`` re-signing).  Set ``MDVIEW_WITH_VENDOR=1`` to ship the
fetched KaTeX/Mermaid tree inside the bundle; otherwise the app fetches them
into the user data dir on first launch.
"""

import os
from pathlib import Path

ROOT = Path(SPEC).resolve().parent.parent  # repo root (build/.. )
SRC = ROOT / "src"
BLOCKER = []  # kept explicit: QtWebEngine hooks are reliable in PySide6 6.12

with_vendor = os.environ.get("MDVIEW_WITH_VENDOR") == "1"

datas = [
    (str(SRC / "mdview" / "static"), "mdview/static"),
    (str(SRC / "mdview" / "palettes"), "mdview/palettes"),
]
if with_vendor:
    data_home = Path(
        os.environ.get("MDVIEW_DATA_DIR", str(Path.home() / "Library/Application Support"))
    )
    vendor_tree = data_home / "mdview" / "vendor"
    if not (vendor_tree / "lock.json").is_file():
        raise SystemExit(
            "MDVIEW_WITH_VENDOR=1 but no vendor tree at "
            f"{vendor_tree} — run `mdview fetch-assets` first."
        )
    datas.append((str(vendor_tree), "mdview/vendor/bundled"))

a = Analysis(
    [str(SRC / "mdview" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=["mdview.viewer"],  # imported dynamically via bootstrap
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MDView",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # windowed app; no Terminal window
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="MDView",
)

app = BUNDLE(
    coll,
    name="MDView.app",
    info_plist={
        "CFBundleName": "MDView",
        "CFBundleDisplayName": "MDView",
        "CFBundleIdentifier": "dev.personal.mdview",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": "26.0",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Markdown Document",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": [
                    "net.daringfiretown.markdown",
                    "public.markdown",
                    "public.plain-text",
                ],
                "CFBundleTypeExtensions": ["md", "markdown", "*"],
            }
        ],
        # Double-click path: Launch Services delivers a QFileOpenEvent, which
        # MDViewApplication.event() turns into an open request (DESIGN.md §9).
        "NSDocumentsFolderUsageDescription": "MDView opens Markdown documents you choose.",
    },
)
