"""The offline guarantee, as a contract test.

The page template promises (``mdview.render.page`` docstring) that no rendered
page can reach the network.  This module proves it statically: every reference
in the assembled HTML is ``mdview:``/``file:``/fragment, no ``http(s)`` URL or
CDN hostname appears anywhere, and the CSP is present and eval-free.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mdview.core.palette import Palette
from mdview.core.paths import static_dir
from mdview.render.page import CSP, PageOptions, render_page

_URL_REF = re.compile(r'(?:src|href)="([^"]+)"')


def _options(palette: Palette, document_dir: Path, **overrides: object) -> PageOptions:
    kwargs: dict[str, object] = {
        "palette": palette,
        "document_dir": document_dir,
        "page_id": "contract",
        **overrides,
    }
    return PageOptions(**kwargs)  # type: ignore[arg-type]


class TestInAppPage:
    @pytest.fixture
    def html(self, seeded_vendor: Path, demo_file: Path, dark_palette: Palette) -> str:
        page = render_page(
            demo_file.read_text(encoding="utf-8"), _options(dark_palette, demo_file.parent)
        )
        return page.html

    def test_every_reference_is_mdview_or_fragment(self, html: str) -> None:
        refs = _URL_REF.findall(html)
        assert len(refs) >= 3, "template produced no references?"
        for ref in refs:
            assert ref.startswith(("mdview://", "#")), ref

    def test_relative_images_absolutize_to_doc_host(
        self, seeded_vendor: Path, tmp_path: Path
    ) -> None:
        doc = tmp_path / "note.md"
        doc.write_text("![pic](sub/pic.png)\n\n![ext](https://x.example/a.png)\n", encoding="utf-8")
        options = _options(self._palette(tmp_path), doc.parent)
        page = render_page(doc.read_text(encoding="utf-8"), options)
        assert 'src="mdview://doc/' in page.html
        assert "sub/pic.png" in page.html  # path preserved, just prefixed
        assert 'src="https://x.example/a.png"' in page.html  # absolute src untouched
        assert "http://" not in page.html.replace("https://x.example/a.png", "")

    @staticmethod
    def _palette(document_dir: Path) -> Palette:
        from mdview.core.palette import PaletteRegistry
        from mdview.core.paths import builtin_palettes_dir

        return PaletteRegistry.from_dirs(builtin_palettes_dir()).get("github-light")

    def test_no_http_anywhere(self, html: str) -> None:
        assert "http://" not in html
        assert "https://" not in html
        for cdn in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com"):
            assert cdn not in html

    def test_csp_present_and_hardened(self, html: str) -> None:
        assert CSP in html
        assert "unsafe-eval" not in html
        assert "connect-src 'none'" in html
        assert "default-src 'none'" in html

    def test_config_json_is_valid_and_local(self, html: str) -> None:
        match = re.search(
            r'<script id="mdview-config" type="application/json">(.*?)</script>', html, re.S
        )
        assert match is not None
        config = json.loads(match.group(1).replace("<\\/", "</"))
        assert config["mermaidUrl"].startswith("mdview://assets/mermaid/")
        assert config["palette"] == "github-dark"

    def test_no_inline_event_handlers_or_scripts(self, html: str) -> None:
        body = html.split("<body", 1)[1]
        assert "onclick" not in body.lower()
        assert "<script>" not in body  # only src= scripts and the JSON config block


class TestBrowserPage:
    def test_file_mode_references_only_file_urls(
        self, seeded_vendor: Path, demo_file: Path, light_palette: Palette
    ) -> None:
        page = render_page(
            demo_file.read_text(encoding="utf-8"),
            _options(light_palette, demo_file.parent, asset_scheme="file"),
        )
        refs = _URL_REF.findall(page.html)
        assert refs
        for ref in refs:
            assert ref.startswith(("file://", "#")), ref
        assert "Content-Security-Policy" not in page.html  # CSP would name mdview: only
        assert page.base_url.startswith("file://")


class TestStaticRuntime:
    """The shipped page runtime itself must contain no network endpoints."""

    @pytest.mark.parametrize("name", ["app.js", "mdview.css"])
    def test_static_files_have_no_urls(self, name: str) -> None:
        text = (static_dir() / name).read_text(encoding="utf-8")
        assert re.search(r"https?://", text) is None, f"{name} references the network"
