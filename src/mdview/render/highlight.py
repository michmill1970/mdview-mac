"""Syntax highlighting via Pygments (pure, offline, palette-driven).

Highlighting happens **in Python at render time**, not in JavaScript in the
view: the result is plain ``<span class="tok-*">`` markup, so it works with
JavaScript disabled, it needs no JS payload, and -- because the Pygments style
is generated from the palette -- code colours follow the user's palette instead
of a fixed highlight.js theme.

Only a curated set of language aliases is mapped; an unknown fence language
falls back to escaped plain text (never an error).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from typing import Final

from pygments.formatters import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name
from pygments.style import Style
from pygments.token import (
    Comment,
    Error,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
    Token,
)

__all__ = ["HighlightResult", "highlight", "pygments_css", "resolve_lexer"]

#: Fence info string -> Pygments lexer name. Aliases not listed are looked up
#: directly in Pygments, which already knows ~600 short names.
_ALIAS_FIXES: Final[Mapping[str, str]] = {
    "shellsession": "bash",
    "console": "bash",
    "ts": "tsx",
    "py3": "python",
    "objective-c": "objective-c",
    "text": "",
    "txt": "",
    "plain": "",
    "plaintext": "",
}

_PLAIN_LANGS: Final[frozenset[str]] = frozenset({"", "text", "txt", "plain", "plaintext"})
_INFO_RE = re.compile(r"^(\S+)")

#: ``pygments.highlight`` bound to a palette-styled formatter.
HighlightFn = Callable[[str, Lexer], str]


@dataclass(frozen=True, slots=True)
class HighlightResult:
    """Highlighted markup for one fenced code block."""

    html: str
    #: Normalised language for the ``data-language`` attribute (QA + CSS hooks).
    language: str
    #: True when no lexer matched and the block is escaped plain text.
    is_plain: bool


def resolve_lexer(language: str | None) -> Lexer | None:
    """Return a lexer for a fence info string, or ``None`` for plain text."""
    lang = (language or "").strip().lower()
    if lang in _PLAIN_LANGS:
        return None
    lang = _ALIAS_FIXES.get(lang, lang)
    if not lang:
        return None
    try:
        lexer = get_lexer_by_name(lang)
    except Exception:  # noqa: BLE001 - Pygments raises ClassNotFound; a bad fence must not be fatal
        return None
    lexer.stripnl = True
    return lexer


@lru_cache(maxsize=64)
def _style_class(colors_key: tuple[tuple[str, str], ...]) -> type[Style]:
    """Build a Pygments style class whose colours come from a palette."""
    colors = dict(colors_key)
    styles: dict[Token, str] = {
        Token: f"{colors['code_text']} noinherit",
        Text: colors["code_text"],
        Keyword: colors["syntax_keyword"],
        Keyword.Constant: f"{colors['syntax_keyword']} bold",
        Keyword.Namespace: colors["syntax_keyword"],
        Keyword.Pseudo: colors["syntax_type"],
        Keyword.Reserved: colors["syntax_keyword"],
        Keyword.Type: colors["syntax_type"],
        Name: colors["code_text"],
        Name.Builtin: colors["syntax_type"],
        Name.Class: colors["syntax_type"],
        Name.Constant: colors["syntax_number"],
        Name.Decorator: colors["syntax_function"],
        Name.Entity: colors["syntax_type"],
        Name.Exception: colors["syntax_type"],
        Name.Function: colors["syntax_function"],
        Name.Label: colors["syntax_keyword"],
        Name.Namespace: colors["syntax_type"],
        Name.Property: colors["syntax_function"],
        Name.Tag: colors["syntax_keyword"],
        Name.Variable: colors["syntax_string"],
        Number: colors["syntax_number"],
        String: colors["syntax_string"],
        String.Affix: colors["syntax_keyword"],
        String.Escape: f"{colors['syntax_string']} bold",
        String.Other: colors["syntax_string"],
        Operator: colors["syntax_operator"],
        Operator.Word: colors["syntax_keyword"],
        Punctuation: colors["code_text"],
        Comment: f"{colors['syntax_comment']} italic",
        Comment.Preproc: f"{colors['syntax_keyword']} noitalic",
        Comment.Special: f"{colors['syntax_comment']} bold",
        Error: f"border:{colors['syntax_invalid']}",
        Generic.Deleted: colors["syntax_invalid"],
        Generic.Emph: "italic",
        Generic.Heading: f"{colors['syntax_type']} bold",
        Generic.Inserted: colors["syntax_string"],
        Generic.Output: colors["muted_text"],
        Generic.Strong: "bold",
        Generic.Subheading: f"{colors['syntax_type']} bold",
        Generic.EmphStrong: "bold italic",
    }
    return type(
        "MdViewPaletteStyle",
        (Style,),
        {
            "background_color": colors["code_bg"].lstrip("#"),
            "highlight_color": colors["selection"].lstrip("#"),
            "styles": styles,
        },
    )


def _colors_key(colors: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(colors.items()))


@lru_cache(maxsize=64)
def _formatter(colors_key: tuple[tuple[str, str], ...]) -> HtmlFormatter:
    return HtmlFormatter(
        style=_style_class(colors_key),
        nocss=True,
        cssclass="md-code-inner",
        wrapcode=True,
        preformatted=True,
        guess_lang=False,
    )


@lru_cache(maxsize=64)
def _highlighter(colors_key: tuple[tuple[str, str], ...]) -> HighlightFn:
    """Return ``pygments.highlight`` bound to a palette-styled formatter."""
    from pygments import highlight as _pygments_highlight

    formatter = _formatter(colors_key)

    def run(code: str, lexer: Lexer) -> str:
        return _pygments_highlight(code, lexer, formatter)

    return run


def highlight(code: str, language: str | None, colors: Mapping[str, str]) -> HighlightResult:
    """Highlight one fenced block. ``colors`` is ``palette.colors``.

    Both branches emit the same outer shape -- ``<div class="md-code"
    data-language="…">`` -- so the stylesheet and the headless DOM assertions
    only need one selector.
    """
    info = _INFO_RE.match((language or "").strip())
    lang = info.group(1).lower() if info else ""
    lexer = resolve_lexer(lang)
    body = code.rstrip("\n")
    if lexer is None:
        inner = f"<pre><code>{escape(body)}</code></pre>"
        return HighlightResult(
            html=f'<div class="md-code" data-language="{escape(lang)}">{inner}</div>',
            language=lang,
            is_plain=True,
        )
    inner = _highlighter(_colors_key(colors))(body, lexer)
    return HighlightResult(
        html=f'<div class="md-code" data-language="{escape(lang)}">{inner}</div>',
        language=lang,
        is_plain=False,
    )


def pygments_css(colors: Mapping[str, str]) -> str:
    """Token CSS for a palette, scoped to ``.md-code`` (injected into the page).

    Only ``get_token_style_defs`` is used: Pygments' own background defs drop
    the leading ``#`` from ``Style.background_color``, so the code background is
    set from the palette directly instead.
    """
    formatter = _formatter(_colors_key(colors))
    defs = "\n".join(formatter.get_token_style_defs(".md-code"))
    return (
        f".md-code {{ background: {colors['code_bg']}; color: {colors['code_text']}; }}\n"
        f".md-code pre, .md-code code {{ background: transparent; }}\n{defs}\n"
    )
