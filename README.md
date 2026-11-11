# MDView

Offline, personal-use Markdown viewer for macOS 26 (Tahoe, arm64).
Multi-tab, real Chromium rendering (QtWebEngine), local KaTeX + Mermaid +
Pygments, editable light/dark palettes that follow the system appearance.

Python 3.12+ · [uv](https://docs.astral.sh/uv/) · PySide6 6.12 · no CDN, ever.

> Design decisions, architecture, and the verified platform quirks behind
> every choice: **[DESIGN.md](DESIGN.md)**.

## Quick start (development)

```sh
uv sync                      # create venv, install pinned deps
uv run mdview examples/demo.md          # open the GUI
uv run mdview --help                    # open|render|palettes|assets|fetch-assets
```

First GUI run needs the vendored JS assets once:

```sh
uv run mdview fetch-assets   # katex@0.19.1 + mermaid@12.3.0 from npm, sha256-locked
uv run mdview assets         # verify local copies / lockfile
```

Headless environments (CI/containers) need offscreen Qt — the test suite sets
this itself, but for `uv run mdview` manually: `QT_QPA_PLATFORM=offscreen`.

## Quality gates

```sh
uv run pytest                # everything (214 tests, incl. live QtWebEngine)
uv run pytest -m "not gui"   # pure core + render layer, no Qt widgets
uv run ruff check .          # lint
uv run ruff format --check . # format
uv run mypy src/mdview       # strict type-check of the shipped package
```

Test markers: `-m gui` (offscreen widgets via pytest-qt), `-m webengine`
(actual Chromium smoke: KaTeX, mermaid, images, offline contract, screenshot).
The first `-m webengine` run of a session pays a ~3 s Chromium boot.

## Building the macOS app (personal use)

On an Apple-silicon Mac:

```sh
uv sync --group dev
uv run mdview fetch-assets           # so the vendor tree can ship inside
cd build && ./make-app.sh            # PyInstaller + ad-hoc re-sign (inner helper first)
open dist/MDView.app examples/demo.md
```

`MDView.app` registers itself for `.md`/`.markdown` via its Info.plist, so
Finder double-click works after one launch. It is **ad-hoc signed** (`-`),
which is fine on your own machine; it is not notarised and must not be
distributed.

## Where things live

| What | Path |
|---|---|
| Settings, session | `~/Library/Application Support/mdview/settings.json` |
| Your palettes | `~/Library/Application Support/mdview/palettes/*.json` |
| Vendored JS | `~/Library/Application Support/mdview/vendor/` |
| Override root | `MDVIEW_DATA_DIR=/some/dir` (used by the tests) |

## Features at a glance

- `.md` / `.markdown`, multi-tab (`Cmd+1…9`, `Ctrl+Tab`), session restore
- Opens any readable text file by path (drag, CLI, `File ▸ Open…`) as Markdown
  — Finder double-click registration stays `.md`/`.markdown`; a path that
  can't open (missing, directory, binary) gets a ⚠ error tab naming the reason
  instead of failing silently, and `mdview open a.md gone.md` exits non-zero
  while still opening the good file (`render`: missing → 1, unusable → 2)
- `Cmd+=` / `Cmd+-` / `Cmd+0` **and** a toolbar font control (10–32 px)
- GFM tables, task lists, strikethrough; Pygments code highlighting coloured
  by the active palette; KaTeX math; Mermaid diagrams — all assets on disk
- Palettes: GitHub Light/Dark, Dracula, High-Contrast Light built in; edit or
  duplicate in-app (`Shift+Cmd+P`) with live preview; per-appearance selection;
  follows system light/dark automatically
- Security: strict CSP, custom `mdview://` scheme (page/static/assets/doc),
  request interceptor that blocks everything remote — offline is enforced, not
  hoped for
