"""Markdown -> HTML body: GFM features, placeholders, escaping, link policy."""

from __future__ import annotations

import pytest

from mdview.core.palette import Palette
from mdview.render.markdown import MARKDOWN_FEATURES, render_body


def test_feature_list_is_complete() -> None:
    assert set(MARKDOWN_FEATURES) == {
        "tables",
        "strikethrough",
        "task_lists",
        "fenced_code",
        "footnotes",
        "math",
        "mermaid",
    }


def test_tables_and_strikethrough_and_tasks(dark_palette: Palette) -> None:
    body = render_body(
        "| a | b |\n| - | - |\n| 1 | 2 |\n\n~~gone~~\n\n- [x] one\n- [ ] two\n", dark_palette
    )
    assert "<table>" in body.html
    assert "<del>" in body.html or "<s>" in body.html
    assert 'type="checkbox"' in body.html
    assert "checked" in body.html


def test_math_becomes_inert_placeholders_not_katex(dark_palette: Palette) -> None:
    body = render_body("inline $x^2$ here\n\n$$y = mx + b$$\n", dark_palette)
    assert body.used_math is True
    assert 'class="md-math"' in body.html
    assert 'class="md-math-block"' in body.html
    assert "data-tex" in body.html
    assert "<script" not in body.html  # hydration happens later in app.js


def test_tex_special_chars_survive_data_attribute(dark_palette: Palette) -> None:
    body = render_body(r'$$a = "b" & c < d$$', dark_palette)
    assert 'data-tex="a = &quot;b&quot; &amp; c &lt; d"' in body.html


def test_mermaid_fence_is_a_placeholder_and_flags_the_page(dark_palette: Palette) -> None:
    body = render_body("```mermaid\ngraph TD; A-->B;\n```", dark_palette)
    assert body.used_mermaid is True
    assert '<pre class="md-mermaid">' in body.html
    assert "A--&gt;B" not in body.html  # --> stays inside <pre> text, arrow intact
    assert "A-->B" in body.html


def test_fences_report_languages(dark_palette: Palette) -> None:
    body = render_body("```python\nx=1\n```\n\n```rust\nfn x(){}\n```\n", dark_palette)
    assert body.languages == ("python", "rust")
    assert body.html.count('class="md-code"') == 2


def test_raw_html_is_escaped_not_executed(dark_palette: Palette) -> None:
    source = '<img src=x onerror="alert(1)">\n\n<script>alert(2)</script>\n'
    body = render_body(source, dark_palette)
    assert "<img" not in body.html
    assert "<script" not in body.html.lower()
    assert "onerror" in body.html  # visible as text, not as an attribute


def test_dangerous_link_schemes_are_neutralised(dark_palette: Palette) -> None:
    # markdown-it-py 4.x refuses to parse javascript:/vbscript: link
    # destinations at all (they stay escaped text); `_link_open_rule`'s "#"
    # rewrite is defense-in-depth for links that arrive with a blanked href.
    body = render_body("[x](javascript:alert(1)) [y](JaVaScRiPt:2) [z](vbscript:x)", dark_palette)
    assert "<a" not in body.html
    assert 'href="javascript' not in body.html.lower()


def test_external_links_are_annotated_for_the_gui(dark_palette: Palette) -> None:
    body = render_body("[site](https://example.com) [mail](mailto:a@example.com)", dark_palette)
    assert 'data-external="1"' in body.html
    assert 'rel="noreferrer noopener"' in body.html
    mailto = body.html.split("mailto:a@example.com")
    assert len(mailto) == 2
    assert "data-external" not in mailto[1][:200]


def test_footnotes(dark_palette: Palette) -> None:
    body = render_body("text[^a]\n\n[^a]: note body\n", dark_palette)
    assert "footnote" in body.html.lower()
    assert "note body" in body.html


def test_blockquote_attribution_line_gets_marker_span(dark_palette: Palette) -> None:
    """A trailing ``— …`` line is the quote's attribution, not body text.

    The stylesheet mutes ``.md-quote-attribution`` while the blockquote body
    reads at full contrast (Phase 5), so the renderer must mark the line.
    """
    body = render_body(
        '> "Open offline on a plane."\n> — the product owner, every meeting\n', dark_palette
    )
    assert '<span class="md-quote-attribution">— the product owner, every meeting</span>' in (
        body.html
    )
    assert "&quot;Open offline on a plane.&quot;" in body.html  # body itself stays plain


def test_attribution_wrapping_is_scoped_to_blockquotes(dark_palette: Palette) -> None:
    body = render_body(
        "> plain quote, no attribution\n\n— a dash outside a quote\n\n> a\n> -- two dashes too\n",
        dark_palette,
    )
    assert "<p>plain quote, no attribution</p>" in body.html
    assert body.html.count('class="md-quote-attribution"') == 1  # only the trailing quote line
    assert '<span class="md-quote-attribution">-- two dashes too</span>' in body.html
    assert '<span class="md-quote-attribution">— a dash' not in body.html


@pytest.mark.parametrize(
    ("source", "used_math", "used_mermaid"),
    [("plain text", False, False), ("$x$", True, False), ("```mermaid\nA\n```", False, True)],
)
def test_usage_flags(
    dark_palette: Palette, source: str, used_math: bool, used_mermaid: bool
) -> None:
    body = render_body(source, dark_palette)
    assert body.used_math is used_math
    assert body.used_mermaid is used_mermaid
