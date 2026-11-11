"""Command-line interface: ``uv run mdview …`` / the bundled app's argv path.

The GUI import (PySide6) is deferred into :func:`_gui_command` and friends, so
``render``, ``palettes``, ``fetch-assets`` and ``assets`` run headless, quickly,
and are covered by plain unit tests.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mdview import __version__
from mdview.core.appearance import Appearance
from mdview.core.errors import MDViewError
from mdview.core.paths import AppDir, app_dir, builtin_palettes_dir, settings_path
from mdview.core.settings import Settings, load_settings_or_default
from mdview.core.theme import selection_for

EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_MISSING_ASSETS = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdview",
        description="MDView -- offline Markdown viewer for macOS.",
    )
    parser.add_argument("--version", action="version", version=f"mdview {__version__}")
    sub = parser.add_subparsers(dest="command")

    open_cmd = sub.add_parser("open", help="open files in the GUI (the default command)")
    open_cmd.add_argument("paths", nargs="*", type=Path, help="Markdown files to open")

    render_cmd = sub.add_parser(
        "render", help="render one file to a standalone HTML page (headless)"
    )
    render_cmd.add_argument("path", type=Path)
    render_cmd.add_argument("-o", "--output", type=Path, help="write here (default: stdout)")
    render_cmd.add_argument("--palette", help="palette id (default: your saved choice)")
    render_cmd.add_argument(
        "--appearance",
        choices=("auto", "light", "dark"),
        default="auto",
        help="page appearance (default: the palette's own)",
    )
    render_cmd.add_argument(
        "--font-size", type=int, help="base font size px (default: your saved choice)"
    )
    render_cmd.add_argument(
        "--for-browser",
        action="store_true",
        help="emit file:// asset URLs so the page also works in a real browser",
    )

    palettes_cmd = sub.add_parser("palettes", help="list available palettes")
    palettes_cmd.add_argument(
        "--appearance", choices=("light", "dark"), help="only this appearance"
    )

    fetch_cmd = sub.add_parser("fetch-assets", help="download the vendored KaTeX/Mermaid")
    fetch_cmd.add_argument("--force", action="store_true", help="re-download even if present")

    sub.add_parser("assets", help="show the vendored-asset status")
    return parser


def _settings() -> Settings:
    settings, _warning = load_settings_or_default(settings_path())
    return settings


def _registry():  # noqa: ANN202 - PaletteRegistry, imported here to keep the header light
    from mdview.core.palette import PaletteRegistry

    return PaletteRegistry.from_dirs(builtin_palettes_dir(), app_dir(AppDir.PALETTES))


def cmd_palettes(args: argparse.Namespace) -> int:
    registry = _registry()
    for palette_id, name in registry.names(appearance=args.appearance):
        palette = registry.get(palette_id)
        kind = "built-in" if palette.builtin else "user    "
        print(f"{palette_id:24} {palette.appearance:5}  {kind}  {name}")
    return 0


def cmd_assets(_: argparse.Namespace) -> int:
    from mdview.render.assets import vendored_report

    report = vendored_report()
    print(json.dumps(report, indent=2))
    return 0 if report["missing"] == [] else EXIT_MISSING_ASSETS


def cmd_fetch_assets(args: argparse.Namespace) -> int:
    from mdview.render.fetch import fetch_assets

    report = fetch_assets(force=args.force)
    print(report.summary())
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from mdview.core.document import Document, inspect_openable
    from mdview.core.errors import AssetError
    from mdview.render.page import PageOptions, render_page

    # Same policy as the GUI: any readable text file renders as Markdown;
    # directories and binaries are usage errors, missing files a plain error.
    reason = inspect_openable(args.path)
    if reason is not None:
        print(f"mdview: {reason}", file=sys.stderr)
        return EXIT_ERROR if reason.startswith("file not found:") else EXIT_USAGE

    document = Document.open(args.path)

    registry = _registry()
    settings = _settings()
    system: Appearance = "dark" if (args.palette or "").endswith("-dark") else "light"
    override = args.palette
    sel = selection_for(registry, settings, system, override=override)
    appearance = sel.appearance if args.appearance == "auto" else args.appearance
    font_size = args.font_size or settings.font_size_px
    options = PageOptions(
        palette=sel.palette,
        font_size_px=font_size,
        appearance=appearance,
        title=document.title,
        document_dir=document.path.parent,
        asset_scheme="file" if args.for_browser else "mdview",
        page_id="render",
    )
    try:
        page = render_page(document.read(), options)
    except AssetError as exc:
        print(f"mdview: {exc}", file=sys.stderr)
        print("hint: uv run mdview fetch-assets", file=sys.stderr)
        return EXIT_MISSING_ASSETS
    if args.output is not None:
        args.output.write_text(page.html, encoding="utf-8")
        size_kb = page.byte_size() / 1024
        print(f"wrote {args.output} ({size_kb:.0f} KiB)")
    else:
        sys.stdout.write(page.html)
    return 0


def preflight_open(paths: list[Path]) -> list[str]:
    """Reasons why any of ``paths`` cannot be opened (empty = all fine).

    The GUI will show an error tab for these, so the app still opens the
    good files; the CLI additionally turns a non-empty result into a
    non-zero exit code, which is what scripts checking ``mdview open`` need.
    """
    from mdview.core.document import inspect_openable

    reasons = []
    for path in paths:
        reason = inspect_openable(path)
        if reason is not None:
            reasons.append(reason)
    return reasons


def cmd_open_gui(paths: list[Path]) -> int:
    from mdview.viewer.application import run

    failures = preflight_open(paths)
    for reason in failures:
        print(f"mdview: {reason}", file=sys.stderr)
    code = run(list(sys.argv[:1]), paths=paths)
    return EXIT_ERROR if failures and code == 0 else code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    # Bare `mdview file.md` behaves like `mdview open file.md`.
    known_commands = {"open", "render", "palettes", "fetch-assets", "assets", "-h", "--help"}
    if argv and argv[0] not in known_commands and not argv[0].startswith("-"):
        argv = ["open", *argv]
    args = parser.parse_args(argv)

    handlers = {
        None: lambda _a: cmd_open_gui([]),
        "open": lambda a: cmd_open_gui(list(a.paths)),
        "palettes": cmd_palettes,
        "assets": cmd_assets,
        "fetch-assets": cmd_fetch_assets,
        "render": cmd_render,
    }
    try:
        return int(handlers[args.command](args))
    except MDViewError as exc:
        print(f"mdview: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
