# MDView for macOS — Design Document

Personal-use Markdown viewer for macOS 26 (Tahoe, arm64): multi-tab, offline,
themable, with real GFM + math + diagrams. Python 3.12+, `uv`-managed.

This document records the design decisions, the reasoning behind them, and the
platform facts they were verified against. Phase 1 scope is complete: every
behaviour described here is implemented and covered by the test suite.

---

## 1. Requirements (as committed)

| # | Requirement |
|---|-------------|
| R1 | View `.md` / `.markdown` on macOS 26 Tahoe arm64; Finder double-click |
| R2 | Multi-tab browsing |
| R3 | Font size via `Cmd+=` / `Cmd+-` / `Cmd+0` **and** a UI control |
| R4 | Smooth scrolling; native text selection & copy |
| R5 | Editable palettes: built-ins + in-app editor writing user files, live apply |
| R6 | Follow system light/dark automatically |
| R7 | GFM tables / task lists / strikethrough, fenced-code highlighting, KaTeX, mermaid — **all assets local, zero CDN** |
| R8 | Personal-use `.app` via PyInstaller, ad-hoc signed, launched via Info.plist document types |
| R9 | Modern Python: uv, `pyproject.toml` as single source of truth, `src/` layout, full type hints, ruff lint+format, pytest, Python 3.12+ |
| R10 | Strict pure-core / thin-GUI split; core is 100 % testable without Qt |
| R11 | Drag `.md` / `.markdown` from Finder onto the window → open in tabs, with drop feedback |

## 2. Framework decision

Candidate UI toolkits were scored against five weighted criteria:

| Criterion (weight) | QtWidgets/QtWebEngine (PySide6) | pywebview (Cocoa WKWebView) |
|---|---|---|
| Native macOS integration (30 %) — menus, shortcuts, Finder, appearance | Strong: real NSMenu bridge, `QFileOpenEvent`, `color-scheme` | Strong (it *is* Cocoa) but menu/shortcut layer is thin |
| Rendering fidelity (25 %) — GFM, KaTeX, mermaid, smooth scroll, selection | Full Chromium via QtWebEngine | WKWebView; equal fidelity *in principle* |
| Testability (20 %) — headless CI on Linux, no macOS-only dead-ends | Excellent: offscreen Qt + pytest-qt; QtWebEngine runs headless here | Poor: WKWebView needs a real macOS UI session; nothing is testable in CI |
| Packaging maturity (15 %) — PyInstaller, signing, one-binary app | Mature: maintained PyInstaller hooks for PySide6 *including* QtWebEngine | Fragile: needs a browser runtime assumption, no real bundling story |
| Dependency weight / long-term health (10 %) | One well-maintained binding, Qt LTS cadence | Small but its macOS backend carries open Tahoe-era bugs |

**Decision: PySide6 6.12 — `QtWidgets` shell + `QWebEngineView` content.**

Rationale, in one paragraph: rendering fidelity forces a real browser engine
(KaTeX, mermaid, and GFM fidelity are not achievable in `QTextBrowser`/
`NSTextView`), which leaves exactly two credible Python options — QtWebEngine
(embedded Chromium) or pywebview (system WKWebView). pywebview would win on
binary weight and native chrome, but it fails the two criteria that matter most
for *this* project: it cannot be tested at all outside a logged-in macOS
session — so the "pure core + verified GUI" requirement would be unenforceable
— and its Cocoa backend has open macOS-Tahoe regressions, which is precisely
the OS this app targets. PySide6 inverts those weaknesses: QtWebEngine is
Chromium (identical behaviour in dev-container and shipped app), the whole GUI
runs offscreen under pytest here, the menus/shortcuts/appearance integration is
first-class, and PyInstaller ships maintained hooks that already know how to
assemble a QtWebEngine `.app`. The extra ~150 MB of bundled Chromium is an
acceptable price for personal-use software that must simply work offline.

**Rejected alternatives** (criteria above): *pywebview* — untestable headless,
open Tahoe bugs. *Flet / Toga* — Flutter/OP-style UIs re-implement text
selection, scrolling and context menus poorly; rendering a Markdown document
would end up embedded in a WebView anyway, losing the "no second renderer"
argument while keeping all the integration problems. *tkinter* — no modern
text rendering, no HTML, effectively Tk 8.6 forever; disqualifying on fidelity.
*QTextBrowser* — a subset HTML/CSS engine: no KaTeX, no mermaid, no
CSS custom-property palette story; disqualifying on R7.

## 3. Architecture

```
┌─────────────────────────────  src/mdview  ─────────────────────────────┐
│ core/          pure Python, no Qt imports                              │
│   paths.py      data/palette/vendor dirs (MDVIEW_DATA_DIR override)    │
│   errors.py     MDViewError hierarchy (AppError, PaletteError, …)      │
│   palette.py    Palette schema + PaletteRegistry (built-ins + user)    │
│   settings.py   Settings dataclass ⇄ settings.json (tolerant loader)   │
│   appearance.py AppearanceMode system|light|dark → resolved Appearance │
│   fonts.py      FontSize (px model) + CSS custom-property emission     │
│   document.py   document facts (path, title), filename/title rules     │
│   tabs.py       TabModel: tab ids ⇄ paths, ordering, dirty tracking    │
│   theme.py      small shared theme helpers                             │
│                                                                        │
│ render/        pure Python: Markdown → complete HTML page              │
│   markdown.py   markdown-it-py + GFM/task/strike/math/mermaid rules    │
│   highlight.py  Pygments highlight with palette-derived dynamic Style  │
│   theme_css.py  generated token CSS (:root vars, code colors, fonts)   │
│   page.py       render_page(): CSP, asset refs, img absolutisation     │
│   assets.py     vendored-asset manifest + status + lock.json checks    │
│   fetch.py      `fetch-assets`: npm tarballs → vendor dir (sha256)     │
│                                                                        │
│ viewer/        thin Qt layer (only module set that imports PySide6)    │
│   application.py MDViewApplication (QFileOpenEvent), AppContext,       │
│                  profile/scheme bootstrap, container Chromium flags    │
│   scheme.py      mdview:// handler (page|static|assets|doc hosts),     │
│                  PageStore, OfflineInterceptor                         │
│   content_view.py QWebEngineView + ContentViewPage (console bridge,    │
│                  nav policy, live font/palette scripting)              │
│   tab_view.py    QTabWidget ⇄ TabModel glue                            │
│   drop_guard.py  file-drop policy + drag forwarding from the web view  │
│   main_window.py menus, shortcuts, toolbar font control, appearance    │
│   palette_editor.py  in-app palette editor dialog (live preview)       │
│   capture.py     widget → PNG screenshots (used by GUI tests)         │
└────────────────────────────────────────────────────────────────────────┘
```

**The split and its contract.** `core/` and `render/` never import PySide6;
`viewer/` holds no logic that can be moved out. The contract between them is
the on-disk render API: `render_page(source, PageOptions) -> RenderedPage`
(html + `base_url` + used-feature flags) and `render_body(source, palette)`.
The GUI feeds document text in and receives a self-contained HTML page plus a
`base_url`; it never inspects Markdown itself. Every rule in this document
that is testable without a display *is* tested in `tests/test_render_*.py` /
`tests/test_core_*.py` with zero Qt involvement (enforced: those files import
no Qt).

**Data flow, opening a document:**

```
open(path) → TabModel.add → read_text (document.py) → render_body (markdown-it:
GFM, task-lists, strike, math→<span data-tex>, mermaid→<pre>) → Pygments with
a palette-built Style → theme_css(palette) → render_page assembles the HTML
template (CSP meta, config JSON, app.js tag, absolutised <img> srcs) →
PageStore.put(tab_id, html) → QWebEngineView.load("mdview://page/tab-N")
→ scheme handler streams the HTML; the page then requests its CSS/JS/KaTeX/
mermaid and document-relative images back through mdview:// (static/assets/doc
hosts) → app.js hydrates KaTeX + mermaid, exposes window.mdview for *live*
font/palette changes without reload.
```

External edits are picked up by explicit reload (`Cmd+R` / menu / watcher-free
reload hook used in tests), so scroll position survives everything *except*
reload — and reload keeps selection out of scope by design.

## 4. The `mdview://` scheme and security model

A custom scheme with four hostnames keeps every privilege boundary explicit
(`scheme.py`):

| URL | Serves | Notes |
|---|---|---|
| `mdview://page/<tab-id>` | the in-memory HTML (`PageStore`) | one entry per live tab, dropped on close |
| `mdview://static/<file>` | `app.js`, `mdview.css` | name allowlist + path containment |
| `mdview://assets/<pkg>/<file>` | vendored KaTeX/Mermaid | same hardening; scheme is CORS-enabled **only** so KaTeX can `fetch()` its fonts |
| `mdview://doc/<pct-encoded-dir>/<rel>` | document-relative resources (images) | resolved under the document's directory with a component-wise containment check |

Scheme flags are registered once before `QApplication` (`register_mdview_scheme`,
SCHEME_NAME as bytes). Because Qt6 `QUrl` neither strips dot segments nor offers
a `path(mode)` overload, the doc host parses `requestUrl().toEncoded()` and
splits the encoded directory at its first literal `/` — and the containment
check that this forces is load-bearing, not decorative.

Layers of defence for R7/R8 ("renders nothing remote, by construction"):

1. **CSP** on every generated page: `default-src 'none'; script-src mdview:;
   style-src mdview: 'unsafe-inline'; img-src mdview: data: https:; font-src
   mdview:; connect-src 'none'; base-uri 'none'; form-action 'none'`. Inline
   *style* is required for the generated token CSS block; inline **script** is
   never allowed, `unsafe-eval` is absent, and no `http(s)` script/style/font
   source exists. (`img-src https:` permits only *display* of remote images —
   a document may reference one; nothing executable can arrive that way. With
   the optional `asset_scheme="file"` for browser debugging, CSP is omitted —
   `file:` cannot express the policy.)
2. **No `<base>` tag**: relative `<img>` sources are rewritten to absolute
   `mdview://doc/…` URLs during assembly (`_absolutize_images`), which keeps
   `base-uri 'none'` meaningful and leaves in-page `#anchors` same-document.
3. **`OfflineInterceptor`**: a `QWebEngineUrlRequestInterceptor` that blocks
   every non-`mdview:` request and *records* what it blocked; the smoke test
   asserts the blocklist stayed empty, so "nothing off-scheme was even
   attempted" is verified, not hoped.
4. **No cache, no cookies, off-the-record profile** (`NoCache`,
   `NoPersistentCookies`) — a hostile document cannot plant state.
5. Navigation policy: `acceptNavigationRequest` keeps main-frame navigation
   inside `mdview:`; ordinary links open in the system browser via
   `QDesktopServices`.
6. Math input is re-serialised through KaTeX with `strict`/`throwOnError:false`
   options; mermaid renders with `securityLevel: "strict"`. Markdown links
   reject `javascript:`/`vbscript:`/`file:` schemes.

## 5. Rendering pipeline

`markdown-it-py` 4.3 with `mdit-py-plugins` (task lists) + built-in GFM table/
strikethrough, plus custom renderer rules (4-arg `(tokens, idx, options, env)`
shape in 4.x):

- **Code fences** → Pygments. The Pygments `Style` is *generated from the
  active palette* (12 `syntax_*` tokens), so highlighted code re-colours
  instantly on palette change — no prebuilt theme files, one source of truth.
- **Math** → `$…$`/`$$…$$` become `<span class="md-math" data-tex>`; the page
  hydrates with **local KaTeX 0.19.1** at load. Chosen over server-side
  TeX→HTML so display math gets KaTeX's own CSS/fonts (also local) and renders
  identically in every environment.
- **Mermaid** → `<pre class="md-mermaid">`; **local mermaid 12.3.0** hydrates
  it on demand. The vendored `mermaid.min.js` is esbuild's IIFE bundle (its
  tail assigns `globalThis.mermaid`), so app.js injects it as a classic
  `<script>` — `import()` would scope the bundle's own `var` to the module and
  break its trailing global assignment (verified live; the failure mode is
  `Cannot read properties of undefined (reading 'mermaid')`). Documents without
  diagrams never fetch the ~2 MB parser. Mermaid initialises with
  `theme: "base"` + `themeVariables` from `render/mermaid_theme.py`: one
  palette token per role (node fill=`surface`, border=`accent`, text=`text`,
  edges=`muted_text` ≥ 4.5:1 on every built-in background, clusters/notes=
  `code_bg`) — Mermaid's built-in light/dark themes rendered off-palette
  lavender nodes on dark palettes (Phase 5 vision review). Because a palette
  change never reloads the page, holders keep their source on
  `data-mdview-source` and `window.mdview.setMermaidTheme(vars)` re-renders
  hydrated diagrams in place (`content_view.reapply_theme` sends both the CSS
  and the variables).
- **Blockquote attribution** → a blockquote paragraph's final line starting
  with an em/en/double dash is wrapped (`core` rule) as
  `<span class="md-quote-attribution">`; the stylesheet reads the quote body
  at full `text` contrast, italic, on an accent rule, and mutes only that
  span (Phase 5: a muted body was the weakest contrast on the page).

**Vendoring (zero CDN at runtime).** `mdview fetch-assets` downloads
`katex@0.19.1` and `mermaid@12.3.0` from the *npm registry*, extracts only the
manifest-listed files, and writes a `lock.json` of sha256 hashes; every later
launch verifies the hashes and re-renders nothing. `render/assets.py` reports
status (`mdview assets`), and a missing vendor tree degrades gracefully: the
page renders with an inline notice where math/mermaid would appear instead of
silently dropping content.

## 6. Appearance: palettes, editor, system mode

A palette is a small, user-editable JSON document (`format: 1`):
`id`, `name`, `appearance: light|dark`, `description`, `base_font_size_px`,
and exactly 24 named colours (`background, surface, text, muted_text, heading,
link, link_hover, accent, border, selection, selection_text, code_* ×3,
syntax_* ×8, scrollbar`). `PaletteRegistry.from_dirs()` layers the four
built-ins (`github-light`, `github-dark`, `dracula-dark`,
`high-contrast-light`) under `~/.local/share`-style user dir
(`paths.user_palettes_dir()`, overridable via `MDVIEW_DATA_DIR`); user files
shadow built-ins by id and are plain JSON, so hand-editing works too.

`theme_css.page_css(palette, font_size_px, appearance=…)` emits the whole
token layer (`:root` custom properties + generated code/scrollbar rules + the
`color-scheme:` line that drives native widgets — form controls, scrollbars,
and page background before first paint). Live palette switching replaces that
one `<style>` block via `window.mdview.setPaletteCss(...)` — no reload, so
scroll position and selection survive.

**The live-call queue (Phase 7 race).** Live-control injections used to call
`window.mdview.X(...)` directly — but app.js is a `type="module"` tag, so
`window.mdview` exists only once it evaluates (after parsing), while
QtWebEngine runs injected `runJavaScript` as soon as the document's main-world
context exists. On a (re)load that window lands *between* navigation and
app.js, where the direct call threw `Cannot read properties of undefined
(reading 'setPaletteCss')` and the user's font/palette change was silently
lost (reproduced on macOS full-suite; fast Linux runs hid it). Three candidate
fixes: retry-with-setTimeout in the snippet (can reorder two queued changes),
Python waiting on `mdviewReady` (adds latency, still races a mid-flight
reload), or a page-side queue drained at init — the queue wins because it
needs no waits, cannot throw (the injected envelope creates
`window.__mdviewPending` itself), and FIFO drain preserves call order across
the race: Qt pushes `["setPaletteCss", [css]]` and calls `window.__mdviewFlush`
if app.js is up; if not, the entries stay queued and app.js drains them once
`window.mdview` is installed — *after* the render-time defaults, so the user's
most recent state wins — and before `mdviewReady` flips to `'1'`, so readiness
implies "final theme applied". There is a second half to the window, though:
Qt's *load state* flips asynchronously after `reload()`, so an injection can
also still land on the **outgoing** document and die with it. The page-side
queue cannot follow a doomed window, so `ContentView` additionally retains the
latest live state (last call per method, in send order) and replays it on every
`loadFinished` — all live calls are idempotent whole-state assignments, so a
replay is a no-op when the original applied and can never misorder a newer
user change (same-page JS is FIFO). `ContentView` also stamps
`mdviewReady='0'` (over the same ordered JS channel) before every `load()`/
`reload()`: the old document's `'1'` would otherwise remain observable until
the new navigation commits, inviting exactly the injection this protocol
exists to absorb.

The **palette editor** (`PaletteEditor` dialog, `Cmd+Shift+P`) lists all
palettes, duplicates built-ins into the user directory, edits name/appearance/
all 24 colours (swatch buttons → native colour picker + hex fields), previews
every change in the open document immediately (`palette_previewed`), and saves
atomically (`palette_saved` → registry reload).

**System light/dark**: `Settings.appearance_mode ∈ {system, light, dark}`;
`system` resolves through `QApplication.styleHints()` colour scheme (verified
API path in these bindings) with a live change hook, and the *resolved*
appearance picks which palette pair entry applies — `palette_light` /
`palette_dark` are separate settings, so "light mode uses dracula, dark mode
uses github-dark" is expressible. `QWebEnginePage.setColorScheme` does **not**
exist in PySide6 6.12; the appearance contract is kept entirely in the page
(`color-scheme:` CSS + template attribute), which is also why a palette-only
change needs no re-navigation.

## 7. Tabs, fonts, windows

`TabModel` (pure) assigns opaque `tab-N` ids, keeps ordering and the active
index, and maps ids ⇄ paths; `TabView` mirrors it into `QTabWidget` (close
buttons, dirty dot after external change) and routes view creation through
`MainWindow`. Closing a tab removes its `PageStore` entry. `Cmd+1…9` select tabs;
`Ctrl/Cmd+Tab` / `Ctrl/Cmd+Shift+Tab` cycle. Session restore (R2): open paths +
active tab + scroll offsets persist in `settings.json` when `restore_session`
is on.

**Font size** uses a px model (`core/fonts.py`): `FontSize(size_px)` with
`percent()` for display, clamped 10–32 px, steps of ±1 px (2 px for a big
jump). It reaches the page as a `--mdv-font-size` CSS custom property, so
everything sizes off one variable. Inputs: `Cmd+=` / `Cmd+-` / `Cmd+0`
(`QKeySequence.StandardKey.ZoomIn/ZoomOut/Open…` — `Actual Size` is explicit
`Ctrl+0`), a toolbar `QSpinBox`/slider control (the "UI control" of R3), and
the menu; all three stay in sync because the model is the only writer.
Smooth scrolling (R4) is a `scroll-behavior: smooth` + wheel-tuning toggle
(`smooth_scrolling`); selection/copy are Chromium-native and deliberately
unhooked.

**macOS chrome notes** (Phase 4 field report). `TabView` enables document-mode
tabs *off macOS only* (`_USE_DOCUMENT_MODE_TABS`): qtbase's `QMacStyle`
`PE_IndicatorTabClose` is commented "Make close button visible only on the
hovered tab" and hides every non-hovered tab's close button while
`documentMode` is on — unacceptable discoverability for a viewer whose tabs
each own a document. With document mode off, QMacStyle draws each closable
tab's button always and (on macOS 26 "Liquid Glass") renders tabs as native
button bezels with a clearly selected tab, so no stylesheet hacks are needed;
Linux/offscreen keeps document mode so the Fusion look the tests were tuned
against is unchanged. `Cmd+W` is `QKeySequence.StandardKey.Close` (⌘W on
macOS, Ctrl+F4 on Linux), verified by a real key-click test. Two accepted
platform behaviors, deliberately *not* fought: the window title renders
left-aligned beside the traffic lights (AppKit's native titlebar rendering
under Qt 6.9 — Qt exposes no placement API, and `setUnifiedTitleAndToolBarOnMac`
is pinned `False` so the titlebar stays a plain titlebar), and the app menu
says "Python" when run unbundled (it comes from `Info.plist`'s `CFBundleName`
= MDView in the packaged app).

**Open-file policy** (Phase 6 field report). A path the user named
explicitly — CLI argument, `File ▸ Open…` — is opened *as Markdown*
whatever its suffix: a `.txt` is friendlier to view than to refuse. Only
genuinely unviewable inputs fail: missing files, directories, unreadable
files, and files whose first 8 KiB contain a NUL byte (binary). The suffix
set `SUPPORTED_SUFFIXES` governs **Finder registration** (Info.plist must
match it) and, since Phase 10, **what an in-window drop offers** — see
below; once a path is accepted it is opened by this same policy, so
"accepted then failed" is visible as an error tab. Both the GUI and the CLI
classify through one pure-core function, `core.document.inspect_openable`,
so their verdicts cannot drift; `mdview render` maps them to exit codes
(missing → 1, directory/binary → 2), and `mdview open` opens every good
path and exits non-zero if any failed, printing each reason to stderr.
Startup session restore is the one quiet caller: a path that vanished while
the app was closed is skipped, not announced.

**Drag & drop of files** (Phase 10). Dropping `.md`/`.markdown` files from
Finder onto the window opens them as tabs, anywhere on the window (tab bar,
content, empty state), in drop order, through the same `open_paths()` route
as `File ▸ Open…` — so titles, error tabs, the palette of the new tab and
zoom all come free. Policy lives in
`viewer/drop_guard.markdown_paths_from_mime`: local-file URLs whose suffix is
in `SUPPORTED_SUFFIXES` (case-insensitive), kept in drop order; a `.png` in a
mixed drop vanishes silently, and a drag with no Markdown in it is refused
outright, so the feedback never promises what the drop will not deliver.

The integration problem is QtWebEngine, and it is not what the obvious
answer ("`setAcceptDrops(True)` on the window") suggests. Measured against
Qt 6.12 (offscreen, asserted by tests in `tests/test_drag_drop.py`):
an unguarded `QWebEngineView` *already* has `acceptDrops()` true — Chromium
needs it for HTML5 drag-and-drop — so it claims the drag, accepts it, and
the drop dies inside the page. And event filters cannot simply be bolted
onto the view: `QApplication::notify` retargets a drag to the nearest
ancestor that accepts drops *before any filter runs*, so a filter on a
widget that refuses drops never fires. `DragForwardFilter` therefore (a)
marks each widget it watches as a drop target, which stops the retargeting
there, (b) forwards `DragEnter`/`DragMove`/`DragLeave`/`Drop` to the
window's `DropSink` methods and consumes them, so the page sees nothing and
cannot navigate to a dropped `file://`, and (c) re-installs itself on every
widget added underneath — the render surface is a `QQuickWidget` delegate
created on first show with Chromium's own input widgets nested inside it,
so a one-shot `findChildren()` pass would miss them. `MainWindow` itself is
the sink: `setAcceptDrops(True)` plus the four Qt virtuals delegating to the
same four methods, which is also what the filtered events call.

Feedback is a `QWidget` child of the central `QStackedWidget`, raised above
everything and sized to it on every `resizeEvent`: a 2 px dashed accent frame
and a "Drop Markdown files to open" caption, styled from the resolved palette
(accent colour plus a 78 %-alpha wash of the background over the page). It is
`WA_TransparentForMouseEvents`, which is what keeps it from intercepting the
drop it advertises — drag delivery hit-tests with `childAt()`, and a
mouse-transparent widget is skipped along with its whole subtree. Showing is
re-asserted on every `DragMove` and hiding is debounced by 50 ms, because the
child enter/leave seams fire `DragLeave` constantly during a real drag.
`QFileOpenEvent` (Finder double-click, drop on the Dock icon) keeps its own
route into `open_paths()`, tested as parity against in-window drops.

**Finder file association** (Phase 12). Double-click, multi-select
"Open With", and Dock drops all arrive as one `QFileOpenEvent` *per file* —
whether MDView is already running or Launch Services is starting it from that
very click. A running instance opens each event live through `open_paths()`,
so a batch lands as ordered tabs with the last file active, exactly like a
multi-file drop. Cold launch is the race: the first events can hit
`MDViewApplication.event()` while `bootstrap()` is still constructing the
window. The app therefore **queues open paths in arrival order** until
`set_open_handler` installs the window's route — that registration flushes
the queue as a *single ordered batch* (one `open_paths()` call, not N racing
ones) — and since `run()` shows the window only after bootstrap, the first
visible frame already carries the launched tabs; the empty no-tab state never
gets a frame. `run()` also skips session restore when the launch delivered
files (an explicit open request is not a resumption, and restored tabs would
bury the launched ones), and duplicate paths keep the shared route's existing
focus-the-existing-tab behavior. The plist side: `CFBundleDocumentTypes`
carries `LSHandlerRank: Viewer` (offered as a viewer, not an Alternate
afterthought) beside `CFBundleTypeRole: Viewer`, the `public.markdown` UTI,
and the `md`/`markdown` extensions.

**Error tabs** (Phase 6). A failed open never fails silently: the path gets
a normal tab titled `⚠ name` whose `Tab.error` holds the reason and whose
body is `error_page_markdown()` rendered through the *same* pipeline as any
document — themed, escaped, no special-case HTML. Re-opening the path once
fixed upgrades the tab in place (same `tab_id`, watcher attaches then).
Session restore classifies persisted paths the same way: existing-but-broken
files come back as error tabs, missing ones are dropped.

**Menu bar and zoom bar** (Phase 6). Qt 6 exposes no public
`WindowRole`/`HelpRole` (the private `QPlatformMenuItem` roles are not in
the API), so the Cocoa integration is title-driven: menus literally titled
"Window"/"Help" are detected by `QCocoaMenuBar`, slotted natively, and gain
the window list and Spotlight help search respectively; the conventional
Minimize/Zoom/Bring All to Front items are ours on every platform. The
bottom zoom bar is one `QToolBar` hosting a single widget whose layout owns
the margins (`16,4,16,4` + trailing stretch) — a bare toolbar with text
actions clipped its first label ("oom Out") flush at the window edge;
`QToolButton`s with `defaultAction` keep tooltip and accessibility free.
Content can't hug the bar: `.md-content` pads its bottom by the bar height
plus 24 px, asserted from the CSS by contract test and pixel-tested by the
screenshot pack (`scripts/screenshot_pack.py`, incl. `missing-file.png`
showing an error tab).

## 8. Settings & on-disk layout

```
<data dir>  (MDVIEW_DATA_DIR > ~/.local/share/mdview on Linux,
             ~/Library/Application Support/MDView on macOS)
├── settings.json      versioned, tolerant loader (unknown keys survive →)
├── palettes/*.json    user palettes
└── vendor/            KaTeX + mermaid trees + lock.json
```

`Settings` fields: `version`, `font_size_px`, `appearance_mode`,
`palette_light`, `palette_dark`, `restore_session`, `active_tab_path`,
`smooth_scrolling`. Writes are atomic (temp + `os.replace`).

## 9. macOS packaging (personal use)

- `build/mdview.spec` (PyInstaller): onedir app bundle, produced at
  `dist/MDView.app` (PyInstaller 6.x writes the BUNDLE at the top level,
  *not* inside the sibling `dist/MDView/` COLLECT directory — that dir is
  duplicated weight we neither sign nor ship), pulling `src/mdview` with
  its `static/` + `palettes/` datas, the **maintained PySide6 QtWebEngine
  hooks** plus belt-and-braces `hiddenimports` for
  `PySide6.QtWebEngineWidgets`/`QtWebEngineCore`, and an embedded
  `Info.plist`: `CFBundleDocumentTypes` for `.md`/`.markdown` only
  (`net.daringfireball.markdown` + `public.markdown`, Viewer role *and*
  Viewer rank since Phase 12), UTI declarations for `.md`/`.markdown`,
  `NSHighResolutionCapable`, `LSMinimumSystemVersion` 26.0,
  `CFBundleName/Identifier`, arm64.
- Finder double-click → Launch Services posts a `QFileOpenEvent` *per file*,
  *not* argv: `MDViewApplication.event()` handles it, and events that arrive
  before the main window exists (cold launch) are **queued in arrival order**
  and flushed as one ordered batch when `bootstrap()` installs the window's
  route (`set_open_handler`) — before `run()` ever shows the window, so there
  is no empty no-tab frame and the last file ends up active (Phase 12; §7).
  Launching with files also suppresses session restore. This is the single
  most load-bearing macOS detail in the project. Dropping a file on the
  **Dock icon** posts the same event, so Dock drops, double-click,
  multi-select "Open With", and in-window drags (§7) are four doors to one
  `open_paths()` call.
- App icon (Phase 11): `assets/mdview-icon.svg` (1024px) is the source of
  truth and `assets/icons/iconset/` holds its 10 rendered, iconutil-named PNGs
  (transparent squircle corners). Both are committed — rendering the SVG's
  `feDropShadow`/`feGaussianBlur` filters needs a renderer the Linux dev box
  doesn't have — so the artefact that *is* generated per build is
  `build/MDView.icns` (gitignored), by `iconutil -c icns` in `make-app.sh`,
  before PyInstaller. Two traps, both handled: **iconutil validates the input
  directory's own name** — Apple requires the `.iconset` extension, and a
  suffix-less folder is the standard cause of the cryptic `error: Invalid
  Iconset` — so `assets/icons/iconset` is staged into
  `build/.iconset/MDView.iconset` and *that* is what iconutil is pointed at;
  and **`BUNDLE(icon=...)` resolves a relative path against the spec file's
  directory** (read out of PyInstaller 6.22.3,
  `building/osx.py`: `os.path.join(CONF['specpath'], self.icon)`), so the spec
  passes an absolute path — the literal `build/MDView.icns` would look in
  `build/build/`. BUNDLE then copies the file to `Contents/Resources/` and
  defaults `CFBundleIconFile` to its basename; the spec states that key
  explicitly from the same constant, and `make-app.sh`'s `verify_icon` gate
  hard-fails unless `Contents/Resources/MDView.icns` exists, `plutil -extract
  CFBundleIconFile raw` names it, the value resolves to that file (with or
  without the extension), and the two files are byte-identical — a wrong icon
  never breaks a launch, it just silently shows the generic one.
  `tests/test_app_icon.py` covers the artwork, the wiring and the gate logic
  itself (real `verify_icon` shell, `plutil` shimmed with `plistlib`) with no
  macOS dependency.
- Ad-hoc signing after build (`build/make-app.sh`, POSIX `sh` + `set -eu`,
  idempotent re-runs). PyInstaller's framework copy-out dumps Qt's payload
  under `<framework>/Versions/Resources/` — the `QtWebEngineProcess.app`
  helper tree *and* the whole framework Resources payload under a doubled
  `Resources/Resources/` (`.pak`s, `icudtl.dat`, `qtwebengine_locales/`,
  V8 snapshots), which dangles the framework-root `Helpers`/`Resources`
  symlinks and aborts QtWebEngine at launch — so the script first merges
  that payload into `<framework>/Versions/A/` for every `*.framework`, then
  hard-fails unless `Helpers/QtWebEngineProcess.app/…/QtWebEngineProcess`,
  `Resources/qtwebengine_resources.pak` and `Resources/icudtl.dat` resolve
  through the framework-root symlinks. Only then sign: the nested
  `QtWebEngineProcess.app` *first* with its inherited entitlements, then
  the repaired framework, then the outer bundle, `codesign --force --sign
  -`. Skipping the nested helper is the classic cause of "silent white
  window" on arm64. Verification is `codesign --verify --deep --verbose=2`
  **without `--strict`**: PySide6 frameworks are non-standard —
  `QtWebEngineCore.framework` has no `Versions/A/Info.plist` even in the
  pristine wheel — so `--strict` fails ("embedded framework contains
  modified or invalid version") on *any* PySide6 bundle, healthy or not.
- Vendored assets (KaTeX/mermaid) are deliberately **not frozen into the
  bundle**: there is one on-disk location, the data-dir vendor tree
  (`app_dir(AppDir.VENDOR)` → `~/Library/Application Support/MDView/vendor`),
  populated by `fetch-assets` on first launch, so no `--with-vendor` build
  variant exists (the dead `MDVIEW_WITH_VENDOR` branch was removed in
  Phase 4). Read-only package data (`static/`, `palettes/`) resolves through
  `package_dir()`, which is `__file__`-based and therefore points inside the
  bundle automatically — no `sys._MEIPASS` special-casing anywhere.

## 10. Testing strategy

Three rings, mirrored by pytest markers (`gui`, `webengine`) and CI selection:

1. **Pure core/render** (no Qt import at all): palette registry & schema,
   settings round-trip & tolerant loading, font model, tab model, markdown
   rules (GFM/task/strike/math/mermaid fences), palette-derived Pygments
   style, generated CSS, CSP contents, img absolutisation, scheme URL
   contracts, offline template references, CLI commands.
2. **GUI contract tests** (`-m gui`, offscreen, pytest-qt): tab open/close/
   ordering, menu/shortcut wiring, appearance switching, font sync, settings
   persistence, palette-editor round-trip incl. live preview, and drag-and-drop
   opening (synthesised `QDragEnterEvent`/`QDropEvent` pairs at the window, the
   tab bar, the empty state and the render surface) with its feedback overlay.
   A session fixture creates the app's own `MDViewApplication` *before*
   pytest-qt (pytest-qt reuses the existing `QApplication` singleton).
3. **Live WebEngine smoke** (`-m webengine`): the product-level proof — a real
   document through `mdview://` with KaTeX, mermaid, Pygments, table, and a
   relative image; live font + palette changes without reload; the offline
   contract; zero `Uncaught`/`Refused to` page errors; a screenshot with real
   pixels; and reload-after-external-edit.

A fourth, smaller ring arrived with Phase 11: **build-asset contracts**
(`tests/test_app_icon.py`) — the committed icon artwork, the icns/
`CFBundleIconFile` wiring in `build/mdview.spec` + `build/make-app.sh`, and the
icon gate's own shell logic, re-run against a synthetic bundle with `plutil`
(the gate's only macOS-only command) shimmed by `plistlib`. No Qt, no macOS, no
`iconutil`.

Verified platform quirks the suite encodes (each discovered live, all
reproducible in this container): scheme handlers see raw percent-encoded URLs
only via `toEncoded()`; Qt6 `QUrl` keeps dot segments; `HttpCacheType.NoCache`;
`QAction.MenuRole` lacks Copy/SelectAll roles (use `TextHeuristicRole`);
`javaScriptConsoleMessage` is a virtual method, so a signal bridge class
(`ContentViewPage.console_message`) carries console output to Qt;
`runJavaScript` callbacks return `''` for object/array results in PySide6 6.12
(all probes wrap `JSON.stringify`); Chromium records *no* resource-timing
entries for scheme-handler responses, so the offline proof pairs the (still
asserted) zero-http resource list with the interceptor's blocklist and the
DOM's own `href/src` inventory; a `QWebEngineView` has `acceptDrops()` true by
default and keeps drags for itself, and Qt retargets a drag to the nearest
drop-accepting ancestor *before* event filters run — both reasons
`viewer/drop_guard.py` has to mark the widgets it watches as drop targets
(§7); `QApplication::notify` dispatches `DragMove`/`Drop`/`DragLeave` only to
the drag manager's live target, so a test that wants a drop must send an
accepted `DragEnter` first.

## 11. Phase boundaries & follow-ups (not Phase 1)

Explicitly deferred: file watcher + auto-reload, export-to-PDF, per-folder
recency, multiple windows, document outline/TOC, incremental render for
huge files, Mermaid theme theming, palette export/import UI, notarized
distribution, Apple-silicon universal binary, Spotlight metadata importer,
URL scheme (`mdview://open?path=…` from other apps), and any plugin story.
The architecture is expected to absorb all of these without layering changes;
none invalidates anything above.
