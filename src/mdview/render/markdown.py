"""GitHub-flavored Markdown -> HTML (pure).

Pipeline: ``markdown-it-py`` (GFM-ish preset) -> custom render rules -> HTML
string containing only elements the stylesheet and ``app.js`` know about.

Deliberate choices:

* ``html=False`` — raw HTML inside a ``.md`` file is **escaped**, which is the
  single biggest security lever we have when opening a Markdown file from the
  internet.  No ``<script>`` from a document ever reaches the view.
* Code blocks are highlighted by Pygments here in Python (palette-driven), not
  by highlight.js in the page.
* Math and Mermaid are *deferred*: the core emits inert placeholders
  (``<span class="md-math" data-tex="…">`` / ``<pre class="md-mermaid">``) that the
  bundled KaTeX / Mermaid scripts hydrate.  The core therefore has no JS and
  stays unit-testable; ``used_math`` / ``used_mermaid`` tell the page assembler
  which scripts to include at all.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from html import escape
from typing import Any, cast

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.rules_core import StateCore
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from mdview.core.palette import Palette
from mdview.render.highlight import highlight

__all__ = ["MARKDOWN_FEATURES", "MarkdownRenderer", "RenderedBody", "render_body"]

#: Every syntax element the renderer can produce (documented in DESIGN.md).
MARKDOWN_FEATURES: tuple[str, ...] = (
    "tables",
    "strikethrough",
    "task_lists",
    "fenced_code",
    "footnotes",
    "math",
    "mermaid",
)

_UNSAFE_URL_RE = re.compile(r"^\s*(?:javascript|vbscript|file)\s*:", re.IGNORECASE)
_MAILTO_RE = re.compile(r"^\s*mailto:", re.IGNORECASE)
_FENCE_LANG_RE = re.compile(r"^(\S+)")
#: A blockquote's final line may be an attribution ("— the product owner").
#: Em/en dashes as escapes on purpose (RUF001 wants them out of literals).
_ATTRIBUTION_RE = re.compile(r"^(?:\u2014|\u2013|--)[ \t]+\S")

#: markdown-it-py 4.x renderer-rule shape: ``(tokens, idx, options, env) -> str``
#: (``Renderer.render`` calls overridden rules with exactly these four arguments).
#: ``env`` is markdown-it's free-form per-render dict; we never read from it.
#: markdown-it exports no public alias for this, so we own it here (the only one).
RenderRule = Callable[[list[Token], int, Mapping[str, Any], object], str]


@dataclass(slots=True)
class RenderedBody:
    """One rendered document body plus what the page assembler must enable."""

    html: str
    used_math: bool = False
    used_mermaid: bool = False
    #: Fence languages seen, in order of first appearance (debug/QA output).
    languages: tuple[str, ...] = field(default_factory=tuple)


class _Tracker:
    """Collects render-time facts (which heavyweight JS the page will need)."""

    def __init__(self) -> None:
        self.used_math = False
        self.used_mermaid = False
        self.languages: list[str] = []

    def note_language(self, lang: str) -> None:
        if lang and lang not in self.languages:
            self.languages.append(lang)


# ------------------------------------------------------------------ rules --


def _make_fence_rule(tracker: _Tracker, palette: Palette) -> RenderRule:
    def rule(
        tokens: list[Any],
        idx: int,
        options: Mapping[str, Any],
        env: object,
    ) -> str:
        token = tokens[idx]
        info = (token.info or "").strip()
        if info.lower() == "mermaid":
            tracker.used_mermaid = True
            # Escape what could open markup (& and <) but keep `>` raw: mermaid
            # arrows (`-->`) survive verbatim into Mermaid's textContent input.
            safe = token.content.replace("&", "&amp;").replace("<", "&lt;")
            return f'<pre class="md-mermaid">{safe}</pre>\n'
        match = _FENCE_LANG_RE.match(info)
        lang = match.group(1).lower() if match else ""
        tracker.note_language(lang)
        result = highlight(token.content, lang, palette.colors)
        return f"{result.html}\n"

    return rule


def _make_math_rules(tracker: _Tracker) -> tuple[RenderRule, RenderRule]:
    def inline(
        tokens: list[Any],
        idx: int,
        options: Mapping[str, Any],
        env: object,
    ) -> str:
        tracker.used_math = True
        tex = tokens[idx].content
        return f'<span class="md-math" data-tex="{escape(tex, quote=True)}">{escape(tex)}</span>'

    def block(
        tokens: list[Any],
        idx: int,
        options: Mapping[str, Any],
        env: object,
    ) -> str:
        tracker.used_math = True
        tex = tokens[idx].content
        return (
            f'<div class="md-math-block" data-tex="{escape(tex, quote=True)}">{escape(tex)}</div>\n'
        )

    return inline, block


def _render_open_tag(token: Token) -> str:
    """Serialize ``<tag attrs…>`` — same output as markdown-it's renderToken
    would give for a non-self-closing inline token, minus the private renderer."""
    parts = [token.tag]
    for name, value in token.attrs.items():
        parts.append(f'{name}="{escape(str(value), quote=True)}"')
    return f"<{' '.join(parts)}>"


def _link_open_rule(
    tokens: list[Any],
    idx: int,
    options: Mapping[str, Any],
    env: object,
) -> str:
    """Annotate links so the GUI can route them, and drop dangerous schemes."""
    token = tokens[idx]
    url = token.attrGet("href") or ""
    # markdown-it itself already blanks javascript:/vbscript:/file: URLs (their
    # href arrives empty); both paths end at one inert in-page anchor.
    if not url or _UNSAFE_URL_RE.match(url):
        token.attrSet("href", "#")
    elif not _MAILTO_RE.match(url):
        token.attrSet("rel", "noreferrer noopener")
        token.attrSet("data-external", "1")
    return _render_open_tag(token)


def _wrap_attribution(children: list[Token]) -> None:
    """Mark a paragraph's final line as attribution when it opens with a dash.

    ``> quote\n> — someone`` is one paragraph in CommonMark; the stylesheet
    needs the attribution line as its own element to keep it muted while the
    body reads at full contrast.  We only ever wrap a *whole final line*
    starting with an em/en/double dash, never mid-sentence text.
    """
    if not children:
        return
    last = children[-1]
    if last.type != "text" or (len(children) > 1 and children[-2].type != "softbreak"):
        return
    if not _ATTRIBUTION_RE.match(last.content):
        return
    open_tag = Token("mdview_span_open", "span", 1)
    open_tag.attrSet("class", "md-quote-attribution")
    close_tag = Token("mdview_span_close", "span", -1)
    children[-1:] = [open_tag, last, close_tag]


def _attribution_rule(state: StateCore) -> None:
    """Core rule: attribution lines inside blockquotes get the marker span."""
    depth = 0
    for token in state.tokens:
        if token.type == "blockquote_open":
            depth += 1
        elif token.type == "blockquote_close":
            depth -= 1
        elif token.type == "inline" and depth > 0 and token.children:
            _wrap_attribution(token.children)


# ---------------------------------------------------------------- builder --


def build_markdown_it(palette: Palette, tracker: _Tracker) -> MarkdownIt:
    """The single Markdown configuration of the app."""
    md = MarkdownIt(
        "commonmark",
        {"html": False, "xhtmlOut": False, "langPrefix": "language-", "breaks": False},
    )
    md.enable("table")
    md.enable("strikethrough")
    tasklists_plugin(md, label=False)
    dollarmath_plugin(md, allow_labels=False)
    footnote_plugin(md)
    md.core.ruler.push("mdview_attribution", _attribution_rule)
    math_inline, math_block = _make_math_rules(tracker)
    # MarkdownIt's default renderer is always RendererHTML, but the protocol
    # it is typed against does not expose ``rules``.
    renderer = cast("RendererHTML", md.renderer)
    rules = cast("dict[str, RenderRule]", renderer.rules)
    rules["fence"] = _make_fence_rule(tracker, palette)
    rules["math_inline"] = math_inline
    rules["math_block"] = math_block
    rules["link_open"] = _link_open_rule
    return md


class MarkdownRenderer:
    """Reusable renderer bound to a palette (highlighting colours differ)."""

    def __init__(self, palette: Palette) -> None:
        self._palette = palette

    def render(self, source: str) -> RenderedBody:
        tracker = _Tracker()
        md = build_markdown_it(self._palette, tracker)
        html = md.render(source)
        return RenderedBody(
            html=html,
            used_math=tracker.used_math,
            used_mermaid=tracker.used_mermaid,
            languages=tuple(tracker.languages),
        )


def render_body(source: str, palette: Palette) -> RenderedBody:
    """One-shot convenience used by the CLI and the tests."""
    return MarkdownRenderer(palette).render(source)
