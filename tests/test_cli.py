"""CLI contract: exit codes, stdout/stderr, and the bare-argv rewrite.

Everything here runs in-process (no subprocess) and headless: the GUI entry
point is monkeypatched, and ``render``/``palettes``/``assets`` never import
PySide6 at all (asserted by the architecture test).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdview import __main__ as cli
from mdview import __version__


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[list[Path]]:
    """Capture ``cmd_open_gui`` calls instead of starting Qt."""
    calls: list[list[Path]] = []

    def fake_open(paths: list[Path]) -> int:
        calls.append(list(paths))
        return 0

    monkeypatch.setattr(cli, "cmd_open_gui", fake_open)
    return calls


class TestArgumentParsing:
    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])
        assert excinfo.value.code == 0
        assert __version__ in capsys.readouterr().out

    def test_bare_file_rewrites_to_open(self, opened: list[list[Path]]) -> None:
        assert cli.main(["notes.md"]) == 0
        assert opened == [[Path("notes.md")]]

    def test_bare_multiple_files(self, opened: list[list[Path]]) -> None:
        assert cli.main(["a.md", "b.markdown"]) == 0
        assert opened == [[Path("a.md"), Path("b.markdown")]]

    def test_explicit_open_command(self, opened: list[list[Path]]) -> None:
        assert cli.main(["open", "x.md"]) == 0
        assert opened == [[Path("x.md")]]

    def test_no_arguments_opens_gui_without_paths(self, opened: list[list[Path]]) -> None:
        assert cli.main([]) == 0
        assert opened == [[]]


class TestPalettesCommand:
    def test_lists_builtins(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["palettes"]) == 0
        out = capsys.readouterr().out
        assert "github-light" in out
        assert "github-dark" in out
        assert "built-in" in out

    def test_appearance_filter(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["palettes", "--appearance", "dark"]) == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines, "dark palettes missing"
        assert all(" dark " in line for line in lines)


class TestAssetsCommand:
    def test_missing_assets_exit_3(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:  # isolated_data_dir leaves vendor empty
        assert cli.main(["assets"]) == cli.EXIT_MISSING_ASSETS
        report = json.loads(capsys.readouterr().out)
        assert report["missing"]

    def test_seeded_vendor_exit_0(
        self, seeded_vendor: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["assets"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["missing"] == []
        assert report["present"] > 0


class TestRenderCommand:
    def test_missing_vendor_exits_3_with_hint(
        self, demo_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["render", str(demo_file)]) == cli.EXIT_MISSING_ASSETS
        err = capsys.readouterr().err
        assert "fetch-assets" in err

    def test_renders_full_page(
        self, seeded_vendor: Path, demo_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["render", str(demo_file)]) == 0
        html = capsys.readouterr().out
        assert html.startswith("<!DOCTYPE html>")
        assert "mdview://assets/katex/katex.min.js" in html
        assert "mdview://assets/mermaid/mermaid.min.js" in html  # config JSON
        assert 'data-mermaid="1"' in html
        assert 'data-math="1"' in html

    def test_output_file_and_browser_mode(
        self,
        seeded_vendor: Path,
        demo_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = tmp_path / "page.html"
        argv = ["render", str(demo_file), "-o", str(out), "--for-browser"]
        assert cli.main(argv) == 0
        html = out.read_text(encoding="utf-8")
        assert "file://" in html
        assert "Content-Security-Policy" not in html  # browser mode drops mdview: CSP
        assert "wrote" in capsys.readouterr().out

    def test_font_size_flag(
        self, seeded_vendor: Path, demo_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["render", str(demo_file), "--font-size", "21"]) == 0
        assert "--mdv-font-size: 21px" in capsys.readouterr().out

    def test_palette_override(
        self, seeded_vendor: Path, demo_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["render", str(demo_file), "--palette", "dracula-dark"]) == 0
        html = capsys.readouterr().out
        assert 'data-appearance="dark"' in html

    def test_txt_extension_renders_now(
        self, seeded_vendor: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Phase 6 policy: any readable text file renders as Markdown.

        ``.txt`` used to exit 2 ("not a Markdown file"); viewing it is the
        friendlier choice (DESIGN.md "Open-file policy").
        """
        text = tmp_path / "notes.txt"
        text.write_text("# plain wins\n", encoding="utf-8")
        assert cli.main(["render", str(text)]) == 0
        assert "plain wins" in capsys.readouterr().out

    def test_directory_exit_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["render", str(tmp_path)]) == cli.EXIT_USAGE
        assert "directory" in capsys.readouterr().err

    def test_missing_file_exit_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["render", str(tmp_path / "gone.md")]) == cli.EXIT_ERROR
        assert "mdview:" in capsys.readouterr().err


class TestOpenExitCodes:
    """Phase 6: ``mdview open good.md missing.md`` must report partial failure.

    The app still opens (and renders) every good path; the *exit code* tells
    scripts that something failed.  ``mdview.viewer.application.run`` is
    patched so no Qt event loop runs in-process.
    """

    @pytest.fixture
    def launched(self, monkeypatch: pytest.MonkeyPatch) -> list[list[Path]]:
        calls: list[list[Path]] = []

        def fake_run(argv: list[str], *, paths: object = ()) -> int:
            assert isinstance(paths, list)
            calls.append(list(paths))
            return 0

        monkeypatch.setattr("mdview.viewer.application.run", fake_run)
        return calls

    def test_all_paths_good_exit_0(
        self, launched: list[list[Path]], demo_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["open", str(demo_file)]) == 0
        assert launched == [[demo_file]]
        assert capsys.readouterr().err == ""

    def test_missing_path_nonzero_but_still_opens(
        self,
        launched: list[list[Path]],
        demo_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        missing = tmp_path / "kitchen-sink.md"
        code = cli.main(["open", str(demo_file), str(missing)])
        assert code == cli.EXIT_ERROR
        # The failure must not block the successful tab: both paths went to the GUI.
        assert launched == [[demo_file, missing]]
        err = capsys.readouterr().err
        assert "File not found" in err
        assert "kitchen-sink.md" in err

    def test_app_exit_code_wins_when_app_fails(
        self, launched: list[list[Path]], demo_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("mdview.viewer.application.run", lambda argv, *, paths=(): 7)
        assert cli.main(["open", str(demo_file)]) == 7

    def test_preflight_reasons(self, demo_file: Path, tmp_path: Path) -> None:
        missing = tmp_path / "gone.md"
        blob = tmp_path / "blob.md"
        blob.write_bytes(b"x\x00y")
        reasons = cli.preflight_open([demo_file, missing, tmp_path, blob])
        assert len(reasons) == 3
        assert any("File not found" in r for r in reasons)
        assert any("directory" in r for r in reasons)
        assert any("Not a text file" in r for r in reasons)
        assert cli.preflight_open([demo_file]) == []
