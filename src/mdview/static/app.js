// mdview page runtime.
//
// Responsibilities (and nothing else):
//   1. hydrate math placeholders with the bundled KaTeX,
//   2. hydrate ```mermaid blocks with the bundled Mermaid (injected on demand
//      as a <script>, so a document without diagrams never pays for the 2 MB
//      parser; the vendored build is the IIFE bundle that sets globalThis),
//   3. expose window.mdview* helpers the Qt layer calls for *live* font-size and
//      palette changes, avoiding a reload (which would lose scroll position).
//
// There is no network access here and no innerHTML from document content:
// KaTeX/Mermaid receive their input through data attributes as plain text.

const config = (() => {
  try {
    return JSON.parse(document.getElementById("mdview-config").textContent);
  } catch (cause) {
    console.warn("mdview: bad config", cause);
    return {};
  }
})();

/** Render every math placeholder with KaTeX (already loaded as `window.katex`). */
function renderMath() {
  const nodes = document.querySelectorAll("[data-tex]");
  if (!nodes.length) return 0;
  const katex = window.katex;
  if (!katex) {
    console.warn("mdview: katex not loaded; leaving math as source text");
    return 0;
  }
  let ok = 0;
  for (const node of nodes) {
    const tex = node.getAttribute("data-tex") ?? "";
    const display = node.classList.contains("md-math-block");
    try {
      katex.render(tex, node, { displayMode: display, throwOnError: false, strict: false });
      node.dataset.mdviewState = "done";
      ok += 1;
    } catch (error) {
      // A broken equation must not blank the page: keep the source visible.
      node.dataset.mdviewState = "error";
      node.title = String(error);
    }
  }
  return ok;
}

/** Turn ```mermaid <pre> blocks into SVG. Keeps the source as fallback text. */
async function renderMermaid() {
  const blocks = document.querySelectorAll(".md-mermaid");
  if (!blocks.length) return 0;
  if (!config.mermaidUrl) return 0;
  let mermaid;
  try {
    // The vendored mermaid.min.js is the IIFE build (sets globalThis.mermaid);
    // a <script> tag loads it under the scheme's CSP without requiring ESM.
    await new Promise((resolve, reject) => {
      const tag = document.createElement("script");
      tag.src = config.mermaidUrl;
      tag.onload = () => resolve();
      tag.onerror = () => reject(new Error(`script load failed: ${config.mermaidUrl}`));
      document.head.append(tag);
    });
    mermaid = globalThis.mermaid;
    if (!mermaid) throw new Error("globalThis.mermaid missing after script load");
  } catch (error) {
    for (const block of blocks) block.dataset.mdviewState = "error";
    console.warn("mdview: mermaid failed to load", error);
    return 0;
  }
  let done = 0;
  for (const block of blocks) {
    const source = block.textContent ?? "";
    try {
      const holder = await renderDiagram(mermaid, source);
      block.replaceWith(holder);
      done += 1;
    } catch (error) {
      block.dataset.mdviewState = "error";
      block.title = String(error);
    }
  }
  return done;
}

let mermaidSeq = 0;

/** 'base' theme + palette-derived themeVariables -- Mermaid ships no identity
 *  of its own then; the colours come from the active mdview palette. */
function mermaidInitOptions() {
  return {
    startOnLoad: false,
    securityLevel: "strict",
    theme: config.mermaidTheme ?? "base",
    themeVariables: config.mermaidThemeVariables ?? {},
    fontFamily: "inherit",
  };
}

/** Render one source string into a fresh holder div (keeps the source on the
 *  holder so a later palette change can re-render it). */
async function renderDiagram(mermaid, source) {
  // Initialise per render: a live palette change landing between two diagrams
  // takes effect for the next one without waiting for a page reload.
  mermaid.initialize(mermaidInitOptions());
  const { svg } = await mermaid.render(`mdview-diagram-${mermaidSeq++}`, source);
  const holder = document.createElement("div");
  holder.className = "md-mermaid-svg";
  holder.dataset.mdviewSource = source;
  holder.innerHTML = svg; // mermaid output is sanitised (securityLevel: strict)
  return holder;
}

/** Live palette change: new themeVariables -> re-render every hydrated
 *  diagram in place.  Cheap no-op before hydration (init picks the values up
 *  on first render) and on documents without diagrams. */
async function setMermaidTheme(variables) {
  // Defensive: only an object reaches mermaid.initialize (never a string).
  config.mermaidThemeVariables =
    variables && typeof variables === "object" ? variables : {};
  const mermaid = globalThis.mermaid;
  const holders = [...document.querySelectorAll(".md-mermaid-svg")];
  if (!mermaid || !holders.length) return 0;
  let done = 0;
  for (const holder of holders) {
    try {
      const fresh = await renderDiagram(mermaid, holder.dataset.mdviewSource ?? "");
      holder.replaceWith(fresh);
      done += 1;
    } catch (error) {
      // A re-theme failure must not blank an already-visible diagram.
      console.warn("mdview: mermaid re-theme failed", error);
    }
  }
  return done;
}

/** Live font size: one CSS variable, no reload, scroll position untouched. */
function setFontSize(px) {
  const size = Math.max(8, Math.min(72, Number(px) || 16));
  document.documentElement.style.setProperty("--mdv-font-size", `${size}px`);
  document.documentElement.style.setProperty("--mdv-code-size", `${Math.max(9, size - 1)}px`);
  return size;
}

/** Live palette: swap the inline <style> block generated by mdview.render.page. */
function setPaletteCss(css) {
  let style = document.getElementById("mdview-palette-style");
  if (!style) {
    const first = document.head.querySelector("style");
    if (first) {
      first.id = "mdview-palette-style";
      style = first;
    }
  }
  if (!style) return false;
  style.textContent = css;
  document.documentElement.dataset.appearance = config.appearance ?? "light";
  return true;
}

function setSmoothScrolling(enabled) {
  document.documentElement.style.setProperty(
    "--mdv-scroll-behavior",
    enabled ? "smooth" : "auto",
  );
}

window.mdview = {
  setFontSize,
  setPaletteCss,
  setMermaidTheme,
  setSmoothScrolling,
  scrollY: () => window.scrollY,
  setScrollY: (y) => window.scrollTo({ top: Number(y) || 0, behavior: "auto" }),
  /** Totals for the headless smoke tests (they assert on these, not on pixels). */
  stats: () => ({
    contentChars: (document.getElementById("mdview-content")?.textContent ?? "").length,
    math: document.querySelectorAll(".katex").length,
    mermaid: document.querySelectorAll(".md-mermaid-svg").length,
    mermaidVars: Object.keys(config.mermaidThemeVariables ?? {}).length,
    code: document.querySelectorAll(".md-code").length,
    tables: document.querySelectorAll(".md-content table").length,
    fontSize: getComputedStyle(document.documentElement).getPropertyValue("--mdv-font-size").trim(),
    background: getComputedStyle(document.body).backgroundColor,
  }),
};

// --- Live-call queue (Phase 7 race fix; protocol: DESIGN.md §6) ---
// This script is a `type="module"` tag: it evaluates only after parsing, and
// only then does `window.mdview` exist. QtWebEngine, however, runs injected
// runJavaScript as soon as the document's main-world context exists -- on a
// (re)load that can be *before* this module evaluated, so a direct
// `window.mdview.setPaletteCss(...)` injection threw
// "Cannot read properties of undefined" and the user's font/palette change was
// silently lost (macOS full-suite failure, Phase 7). The protocol instead:
// Qt pushes ["methodName", [args...]] entries onto window.__mdviewPending --
// the injected snippet creates the array itself, so it can never throw -- then
// calls window.__mdviewFlush when we are up. Calls landing pre-evaluation stay
// queued and FIFO-drain here, so order survives the race and nothing is lost.
const flushPending = () => {
  const queue = window.__mdviewPending;
  while (queue?.length) {
    const [name, args] = queue.shift();
    try {
      const method = window.mdview[name];
      if (typeof method === "function") method(...args);
    } catch (error) {
      // One failed queued call must not strand the calls behind it.
      console.warn(`mdview: queued ${name} failed`, error);
    }
  }
};
window.__mdviewFlush = flushPending;

document.documentElement.dataset.mdviewReady = "0";
renderMath();
setPaletteCss(document.getElementById("mdview-palette-style")?.textContent ?? "");
// Drained *after* the render-time defaults above: a queued live call is the
// user's most recent state and must win over the values baked into the HTML.
flushPending();
document.documentElement.dataset.mdviewReady = "1";
renderMermaid().finally(() => {
  document.documentElement.dataset.mdviewDiagrams = String(
    document.querySelectorAll(".md-mermaid-svg").length,
  );
});
