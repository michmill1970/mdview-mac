"""Open-file policy and error tabs (pure core, no Qt).

Phase 6 defect 3: ``mdview open demo.md kitchen-sink.md`` used to open the
good file and swallow the missing one.  The policy that replaced the silence
lives here (``inspect_openable``), the visible consequence lives in the tab
model (error tabs), and the GUI/CLI are thin translators over both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdview.core.document import error_page_markdown, inspect_openable, is_supported
from mdview.core.palette import Palette
from mdview.core.tabs import TabModel


class TestInspectOpenable:
    def test_readable_markdown_and_text_pass(self, tmp_path: Path) -> None:
        good = tmp_path / "a.md"
        good.write_text("# hi\n", encoding="utf-8")
        txt = tmp_path / "notes.txt"  # decision: .txt opens anyway (friendlier)
        txt.write_text("plain text\n", encoding="utf-8")
        empty = tmp_path / "empty.md"
        empty.write_text("", encoding="utf-8")
        assert inspect_openable(good) is None
        assert inspect_openable(txt) is None
        assert inspect_openable(empty) is None
        assert is_supported(txt) is False  # suffix still only gates Finder

    def test_missing_file_reports_path(self, tmp_path: Path) -> None:
        gone = tmp_path / "kitchen-sink.md"
        reason = inspect_openable(gone)
        assert reason is not None
        assert reason.startswith("file not found:")
        assert "kitchen-sink.md" in reason

    def test_directory_is_not_a_file(self, tmp_path: Path) -> None:
        reason = inspect_openable(tmp_path)
        assert reason is not None
        assert "directory" in reason

    def test_binary_is_not_text(self, tmp_path: Path) -> None:
        blob = tmp_path / "blob.md"
        blob.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rJUNK\x00")
        reason = inspect_openable(blob)
        assert reason is not None
        assert reason.startswith("not a text file:")

    def test_unreadable_file_reports_oserror(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.md"
        secret.write_text("hi", encoding="utf-8")
        secret.chmod(0o000)
        try:
            reason = inspect_openable(secret)
        finally:
            secret.chmod(0o644)
        if secret.owner() == Path("/dev/null").owner():  # running as root: chmod is moot
            pytest.skip("cannot make a file unreadable as root")
        assert reason is not None
        assert reason.startswith("cannot read")


class TestErrorPageMarkdown:
    def test_names_path_and_reason(self, tmp_path: Path) -> None:
        path = tmp_path / "kitchen sink.md"
        page = error_page_markdown(path, "file not found: /nope/kitchen sink.md")
        assert "Can’t open `kitchen sink.md`" in page  # noqa: RUF001
        assert "file not found:" in page
        assert str(path) in page

    def test_is_escaped_safe_markdown(self, dark_palette: Palette) -> None:
        # Paths are user data: they go through the Markdown renderer, which
        # escapes HTML, so a nasty name cannot inject markup.
        from mdview.render.markdown import render_body

        page = error_page_markdown("<script>alert(1)</script>.md", "file not found")
        assert "<script>" in page  # raw text in the *source* is fine ...
        body = render_body(page, dark_palette).html
        assert "<script>alert" not in body  # ... but never reaches the DOM as a tag


class TestTabModelErrorTabs:
    def test_open_error_creates_flagged_tab(self, tmp_path: Path) -> None:
        model = TabModel()
        gone = tmp_path / "gone.md"
        tab = model.open_error(gone, f"file not found: {gone}")
        assert tab.error is not None
        assert tab.title.startswith("⚠")
        assert model.active_tab_id == tab.tab_id
        # The document handle exists but must never have been read.
        assert tab.document.path == gone

    def test_error_tab_upgrades_on_successful_reopen(self, tmp_path: Path) -> None:
        model = TabModel()
        path = tmp_path / "late.md"
        errored = model.open_error(path, "file not found")
        path.write_text("# Late arrival\n", encoding="utf-8")
        upgraded = model.open_path(path)
        assert upgraded.tab_id == errored.tab_id  # same tab, not a duplicate
        assert upgraded.error is None
        assert upgraded.title == "late"
        assert model.count == 1

    def test_reopening_error_path_updates_reason_in_place(self, tmp_path: Path) -> None:
        model = TabModel()
        path = tmp_path / "gone.md"
        first = model.open_error(path, "file not found")
        second = model.open_error(path, "not a text file")
        assert first.tab_id == second.tab_id
        assert model.count == 1
        assert second.error == "not a text file"

    def test_error_tabs_are_deduped_against_real_tabs(self, tmp_path: Path) -> None:
        model = TabModel()
        path = tmp_path / "a.md"
        path.write_text("# A\n", encoding="utf-8")
        real = model.open_path(path)
        later = model.open_error(path, "cannot read")  # e.g. raced with a delete
        assert later.tab_id == real.tab_id
        assert model.count == 1

    def test_restore_skips_missing_but_flags_existing_broken(self, tmp_path: Path) -> None:
        good = tmp_path / "keep.md"
        good.write_text("# keep\n", encoding="utf-8")
        blob = tmp_path / "blob.md"
        blob.write_bytes(b"abc\x00def")
        model = TabModel()
        tabs = model.restore([good, tmp_path / "gone.md", blob])
        assert len(tabs) == 2  # missing skipped, blob kept as an error tab
        assert tabs[0].error is None
        assert tabs[1].error is not None
        assert "not a text file" in tabs[1].error
        assert model.active_tab_id == tabs[-1].tab_id
