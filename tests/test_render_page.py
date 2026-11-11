"""Page assembly: CSP, URLs, config JSON, CSS plumbing -- and the asset gate."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mdview.core.errors import AssetError
from mdview.core.palette import Palette
from mdview.render.page import PageOptions, base_url_for, render_page


@pytest.fixture
def options(dark_palette: Palette, demo_file: Path) -> PageOptions:
    return PageOptions(
        palette=dark_palette,
        font_size_px=17,
        appearance="dark",
        title="Demo & <Co>",
        document_dir=demo_file.parent,
        page_id="t1",
        skip_asset_check=True,
    )


def test_asset_gate_requires_vendored_files(demo_file: Path, dark_palette: Palette) -> None:
    with pytest.raises(AssetError, match="fetch-assets"):
        render_page("# x", PageOptions(palette=dark_palette, document_dir=demo_file.parent))


def test_task_checkboxes_are_themed_not_native() -> None:
    """Native checkbox rendering (a faint grey box) must stay replaced.

    The palette-editor review flagged checked task boxes as near-invisible on
    light palettes; mdview.css therefore draws them itself, accent-filled with
    a white check, and every palette must be able to drive that styling.
    """
    css_path = Path(__file__).resolve().parent.parent / "src" / "mdview" / "static" / "mdview.css"
    css = css_path.read_text(encoding="utf-8")
    assert "appearance: none" in css
    box = css.split('input[type="checkbox"]', 1)[1]
    base_rule, checked_rules = box.split(":checked", 1)
    assert "var(--mdv-border)" in base_rule  # unchecked boxes keep a real border
    assert "var(--mdv-accent)" in checked_rules
    assert "rotate(45deg)" in checked_rules  # the white check is a CSS border glyph
    assert "#fff" in checked_rules


def test_page_structure_and_escaping(dark_palette: Palette, options: PageOptions) -> None:
    page = render_page("# Hi $x^2$\n\n```mermaid\nA-->B\n```\n", options)
    html = page.html
    assert "<!DOCTYPE html>" in html
    assert "<title>Demo &amp; &lt;Co&gt;</title>" in html
    assert 'data-math="1"' in html
    assert 'data-mermaid="1"' in html
    assert '<style id="mdview-palette-style">' in html
    assert "--mdv-font-size: 17px" in html
    assert "color-scheme: dark" in html
    assert page.used_math
    assert page.used_mermaid
    assert page.languages in {("mermaid",), ()}


def test_math_script_only_when_needed(dark_palette: Palette) -> None:
    opts = PageOptions(palette=dark_palette, skip_asset_check=True)
    plain = render_page("no math here", opts)
    mathy = render_page("$x$", opts)
    assert "katex.min.js" not in plain.html
    assert "katex.min.js" in mathy.html
    assert "mermaid" not in plain.html.split("<body")[0]


def test_config_json_is_valid_and_decodes(dark_palette: Palette, options: PageOptions) -> None:
    page = render_page("x", options)
    start = page.html.index('<script id="mdview-config" type="application/json">')
    blob = page.html[start:].split(">", 1)[1].split("</script>")[0]
    config = json.loads(blob.replace("<\\/", "</"))
    assert config["appearance"] == "dark"
    assert config["mermaidTheme"] == "base"
    assert config["mermaidUrl"].startswith("mdview://assets/mermaid/")
    assert config["palette"] == "github-dark"


def test_init_payload_carries_palette_theme_variables(
    dark_palette: Palette, light_palette: Palette
) -> None:
    """Phase 5: diagrams must inherit the active palette, not Mermaid's own.

    The init payload is the page's config JSON; app.js hands ``theme`` +
    ``mermaidThemeVariables`` straight to ``mermaid.initialize`` (and to the
    live re-theme path), so asserting the payload is asserting the diagram
    colours -- at pure-Python level, no browser needed.
    """
    options = PageOptions(palette=dark_palette, appearance="dark", skip_asset_check=True)
    page = render_page("```mermaid\nA-->B\n```", options)
    start = page.html.index('<script id="mdview-config" type="application/json">')
    blob = page.html[start:].split(">", 1)[1].split("</script>")[0]
    config = json.loads(blob.replace("<\\/", "</"))
    theme_vars = config["mermaidThemeVariables"]
    colors = dark_palette.colors
    assert config["mermaidTheme"] == "base"
    assert theme_vars["background"] == colors["background"]
    assert theme_vars["primaryColor"] == colors["surface"]
    assert theme_vars["primaryBorderColor"] == colors["accent"]
    assert theme_vars["primaryTextColor"] == colors["text"]
    assert theme_vars["lineColor"] == colors["muted_text"]
    assert theme_vars["clusterBorder"] == colors["border"]
    # A light render of the same page differs in exactly the palette tokens.
    light_options = PageOptions(palette=light_palette, skip_asset_check=True)
    light_page = render_page("```mermaid\nA-->B\n```", light_options)
    light_start = light_page.html.index('<script id="mdview-config" type="application/json">')
    light_blob = light_page.html[light_start:].split(">", 1)[1].split("</script>")[0]
    light_vars = json.loads(light_blob.replace("<\\/", "</"))["mermaidThemeVariables"]
    assert light_vars["primaryColor"] == light_palette.colors["surface"]
    assert light_vars["lineColor"] == light_palette.colors["muted_text"]
    assert set(light_vars) == set(theme_vars)


def test_app_js_initialises_mermaid_with_palette_variables() -> None:
    """The JS half of the contract (string level): init reads the palette vars."""
    js_path = Path(__file__).resolve().parent.parent / "src" / "mdview" / "static" / "app.js"
    js = js_path.read_text(encoding="utf-8")
    assert 'theme: config.mermaidTheme ?? "base"' in js
    assert "themeVariables: config.mermaidThemeVariables ?? {}" in js
    # Live palette changes re-render hydrated diagrams (the page never reloads).
    assert "setMermaidTheme" in js
    assert "dataset.mdviewSource" in js


def test_blockquote_body_is_full_contrast() -> None:
    """Phase 5: muted blockquote body was the weakest contrast on the page.

    Body text now reads at full text colour, italic, on an accent rule; only
    the attribution/cite line (renderer-wrapped .md-quote-attribution) stays
    muted.
    """
    css_path = Path(__file__).resolve().parent.parent / "src" / "mdview" / "static" / "mdview.css"
    css = css_path.read_text(encoding="utf-8")
    quote_rule = css.split(".md-content blockquote {", 1)[1].split("}", 1)[0]
    assert "var(--mdv-text)" in quote_rule
    assert "var(--mdv-accent)" in quote_rule
    assert "font-style: italic" in quote_rule
    assert "var(--mdv-muted-text)" not in quote_rule  # the body must not be dimmed
    attribution_rule = css.split(".md-content blockquote .md-quote-attribution", 1)[1]
    attribution_rule = attribution_rule.split("}", 1)[0]
    assert "var(--mdv-muted-text)" in attribution_rule


def test_content_security_policy_shape(dark_palette: Palette, options: PageOptions) -> None:
    page = render_page("x", options)
    tag = page.html.split('<meta http-equiv="Content-Security-Policy"')[1].split(">")[0]
    csp = tag
    assert "default-src 'none'" in csp
    assert "mdview:" in csp
    assert "unsafe-eval" not in csp
    assert "https:" not in csp  # offline guarantee at the policy level too


def test_relative_images_resolve_via_doc_host(dark_palette: Palette, demo_file: Path) -> None:
    options = PageOptions(
        palette=dark_palette, document_dir=demo_file.parent, skip_asset_check=True
    )
    base = base_url_for(options)
    assert base.startswith("mdview://doc/")
    assert demo_file.parent.name in base  # the directory is in the URL (encoded)
    page = render_page("![alt](pic.png)", options)
    assert f'src="{page.base_url}pic.png"' in page.html  # absolutised to the doc host
    assert 'src="pic.png"' not in page.html  # never left <base>-relative...
    assert "<base" not in page.html  # ...and there is no <base> (CSP base-uri 'none')


def test_page_host_fallback_without_document_dir(dark_palette: Palette) -> None:
    options = PageOptions(palette=dark_palette, page_id="abc", skip_asset_check=True)
    assert base_url_for(options) == "mdview://page/abc/"


def test_for_browser_mode_uses_file_urls(dark_palette: Palette, demo_file: Path) -> None:
    options = PageOptions(
        palette=dark_palette,
        document_dir=demo_file.parent,
        asset_scheme="file",
        skip_asset_check=True,
    )
    base = base_url_for(options)
    assert base.startswith("file://")
    assert base.endswith("/")
    page = render_page("x", options)
    assert "file://" in page.html
    assert "mdview://" not in page.html


def test_content_bottom_padding_clears_the_zoom_bar() -> None:
    """Phase 6: the last content line must never hug the bottom zoom bar.

    The Qt side guarantees the web viewport ends above the bar; this is the
    page-side half of that contract -- ``.md-content`` keeps 4em of reading
    rhythm *plus a constant 24px* of bottom padding.
    """
    css_path = Path(__file__).resolve().parent.parent / "src" / "mdview" / "static" / "mdview.css"
    css = css_path.read_text(encoding="utf-8")
    rule = re.search(r"\.md-content\s*\{[^}]*?padding:([^;]+);", css, re.DOTALL)
    assert rule is not None, ".md-content lost its padding declaration"
    padding = " ".join(rule.group(1).split())
    assert padding.endswith("calc(var(--mdv-font-size) * 4 + 24px);") or padding.endswith(
        "calc(var(--mdv-font-size) * 4 + 24px)"
    ), f"unexpected bottom padding: {padding}"
