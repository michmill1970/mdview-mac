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
  (``<span class="md-math" data-tex="…">`` / ``<pre class="mermaid">``) that the
  bundled KaTeX / Mermaid scripts hydrate.  The core therefore has no JS and
  stays unit-testable; ``used_math`` / ``used_mermaid`` tell the page assembler
  which scripts to include at all.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from html import escape
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererProtocol
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

#: markdown-it renderer-rule shape: (tokens, idx, options, env, slf) -> str.
RenderRule = Callable[[list[Any], int, Mapping[str, Any], Any, RendererProtocol], str]


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
# markdown-it calls render rules as ``rule(tokens, idx, options, env, slf)``.


def _make_fence_rule(tracker: _Tracker, palette: Palette) -> RenderRule:
    def rule(
        tokens: list[Any],
        idx: int,
        options: Mapping[str, Any],
        env: Any,
        slf: RendererProtocol,
    ) -> str:
        token = tokens[idx]
        info = (token.info or "").strip()
        if info.lower() == "mermaid":
            tracker.used_mermaid = True
            return f'<pre class="md-mermaid">{escape(token.content)}</pre>\n'
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
        env: Any,
        slf: RendererProtocol,
    ) -> str:
        tracker.used_math = True
        tex = tokens[idx].content
        return f'<span class="md-math" data-tex="{escape(tex, quote=True)}">{escape(tex)}</span>'

    def block(
        tokens: list[Any],
        idx: int,
        options: Mapping[str, Any],
        env: Any,
        slf: RendererProtocol,
    ) -> str:
        tracker.used_math = True
        tex = tokens[idx].content
        return (
            f'<div class="md-math-block" data-tex="{escape(tex, quote=True)}">{escape(tex)}</div>\n'
        )

    return inline, block


def _link_open_rule(
    tokens: list[Any],
    idx: int,
    options: Mapping[str, Any],
    env: Any,
    slf: RendererProtocol,
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
    return slf.renderToken(tokens, idx, options)


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
    math_inline, math_block = _make_math_rules(tracker)
    md.renderer.rules["fence"] = _make_fence_rule(tracker, palette)
    md.renderer.rules["math_inline"] = math_inline
    md.renderer.rules["math_block"] = math_block
    md.renderer.rules["link_open"] = _link_open_rule
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
