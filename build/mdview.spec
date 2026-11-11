# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MDView.app (personal use, ad-hoc signed).

Run via ``build/make-app.sh`` (which also repairs PyInstaller's mangled
QtWebEngineCore framework layout and handles the mandatory nested
``QtWebEngineProcess`` re-signing); it produces ``dist/MDView.app``
(PyInstaller 6.x writes the onedir BUNDLE at the top level, NOT inside the
sibling ``dist/MDView/`` COLLECT directory — that dir is neither signed
nor shipped).

Vendored KaTeX/Mermaid are deliberately **not** frozen into the bundle: they
are fetched once by ``mdview fetch-assets`` into the writable user data dir
(``~/Library/Application Support/MDView/vendor``) — on first launch when
missing — so the bundle stays small and re-fetching keeps working.  Only our
own ``static/`` and ``palettes/`` are shipped as datas; ``package_dir()``
resolves to ``Contents/Resources`` inside the bundle, where PyInstaller
unpacks them, while ``data_dir()``/``vendor_dir()`` always resolve to the
Application Support tree (see DESIGN.md §8–§9).
"""

from pathlib import Path

ROOT = Path(SPEC).resolve().parent.parent  # repo root (build/.. )
SRC = ROOT / "src"

datas = [
    (str(SRC / "mdview" / "static"), "mdview/static"),
    (str(SRC / "mdview" / "palettes"), "mdview/palettes"),
]

a = Analysis(
    [str(SRC / "mdview" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "mdview.viewer",  # imported dynamically from mdview.__main__
        # Belt and braces for the render stack: PyInstaller's PySide6 hooks
        # collect QtWebEngineWidgets/QtWebEngineCore (plus QtWebEngineProcess
        # and its resources) automatically, but a bundle without the helper
        # shows a blank window, so the spec pins the intent explicitly.
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
    ],
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
        # This is what fixes the menu bar showing "Python" when the app is
        # run unbundled: Launch Services uses CFBundleName for the app menu.
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
                ],
                # Must match core.document.SUPPORTED_SUFFIXES exactly.  No
                # "*" and no public.plain-text: MDView must not present
                # itself for every text file on the system.
                "CFBundleTypeExtensions": ["md", "markdown"],
            }
        ],
        # Double-click path: Launch Services delivers a QFileOpenEvent, which
        # MDViewApplication.event() turns into an open request (DESIGN.md §9).
        "NSDocumentsFolderUsageDescription": "MDView opens Markdown documents you choose.",
    },
)
