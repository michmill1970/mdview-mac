"""Phase 12: the Finder file-association open path, driven offscreen.

Launch Services hands files to MDView as one ``QFileOpenEvent`` per file --
double-click, multi-select "Open With", Dock drop, whether the app is already
running or being launched by the very click.  Two timelines must both land
every file as a tab, in order, last one active:

* **Cold launch:** events can arrive before ``bootstrap()`` finishes building
  the window.  They queue in ``MDViewApplication`` (arrival order) and flush
  as one batch the moment ``set_open_handler`` wires the window -- which runs
  inside bootstrap, i.e. *before* ``run()`` shows anything, so the empty
  no-tab state never gets a visible frame.
* **Running instance:** the same events arrive after the handler exists and
  each opens live through ``open_paths()`` -- the drag-and-drop route.

Everything here is synthesized with real ``QFileOpenEvent`` objects against
the offscreen platform (no macOS-only APIs); the Info.plist half is asserted
by parsing the spec with ``ast`` (no iconutil/plutil).  Duplicate opens keep
whatever the shared open path already did -- pinned here, so this phase can
only preserve behaviour, never drift it.
"""

from __future__ import annotations

import ast
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication

from mdview.core.paths import settings_path
from mdview.core.settings import Settings, save_settings
from mdview.viewer.application import (
    AppContext,
    bootstrap,
    create_application,
    startup_paths,
)

SPEC = Path(__file__).resolve().parent.parent / "build" / "mdview.spec"


def _md(tmp_path: Path, name: str, body: str = "# hi\n") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def cold_app():
    """The session-wide app returned to cold-launch state: no window, no handler."""
    app = create_application([])
    app._reset_open_state()
    yield app
    app._reset_open_state()


@pytest.fixture
def cold_bootstrap(cold_app, seeded_vendor):
    """bootstrap() on demand -- after the caller has queued its cold-launch
    events -- with window teardown handled here."""
    contexts: list[AppContext] = []

    def start() -> AppContext:
        context = bootstrap(["mdview-test"], ensure_bundled_assets=False)
        contexts.append(context)
        return context

    yield start
    for context in contexts:
        context.window.close()


@pytest.fixture
def ctx(seeded_vendor):
    """A running instance: bootstrapped window, app open-state reset before and
    after so a previous test's handler never intercepts a cold-launch scenario."""
    app = create_application([])
    app._reset_open_state()
    context = bootstrap(["mdview-test"], ensure_bundled_assets=False)
    yield context
    context.window.close()
    app._reset_open_state()


def _wait_until(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    """Pump the event loop until ``condition`` holds, or fail after ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail("condition not met before timeout")
        QApplication.processEvents()


# ------------------------------------------------------------- cold launch --


@pytest.mark.gui
@pytest.mark.webengine
class TestColdLaunchQueue:
    def test_events_before_window_ready_are_queued_in_arrival_order(
        self, cold_app, tmp_path
    ) -> None:
        a = _md(tmp_path, "a.md", "# A\n")
        b = _md(tmp_path, "b.md", "# B\n")
        c = _md(tmp_path, "c.md", "# C\n")
        for path in (a, b, c):
            assert QApplication.sendEvent(cold_app, QFileOpenEvent(str(path)))
        # Nothing opened -- there is no window to open into yet -- but nothing
        # was lost either: the queue holds the batch in arrival order.
        assert cold_app.pending_open_paths() == (str(a), str(b), str(c))
        assert cold_app.launch_open_paths() == ()

    def test_ready_window_receives_the_queue_as_one_ordered_batch(self, cold_app, tmp_path) -> None:
        paths = [_md(tmp_path, f"q{i}.md") for i in range(3)]
        for path in paths:
            QApplication.sendEvent(cold_app, QFileOpenEvent(str(path)))
        calls: list[list[str]] = []
        cold_app.set_open_handler(calls.append)
        assert calls == [[str(p) for p in paths]]  # one batch, arrival order
        assert cold_app.pending_open_paths() == ()
        assert cold_app.launch_open_paths() == tuple(str(p) for p in paths)

    def test_cold_launch_opens_every_tab_before_the_window_is_ever_shown(
        self, cold_app, cold_bootstrap, tmp_path
    ) -> None:
        paths = [_md(tmp_path, f"launch{i}.md", f"# Launch {i}\n") for i in range(3)]
        for path in paths:
            QApplication.sendEvent(cold_app, QFileOpenEvent(str(path)))
        context = cold_bootstrap()
        window = context.window
        assert window._model.open_paths() == tuple(str(p) for p in paths)
        active = window.current_tab()
        assert active is not None
        assert active.document.key == str(paths[-1])  # last file is the active tab
        # The no-flash guarantee: bootstrap returned *after* opening, and
        # run() only calls show() after bootstrap -- the window that would
        # first paint is already a tabbed window on the tab stack, never the
        # empty no-tab state.
        assert not window.isVisible()
        assert window._stack.currentIndex() == 1

    def test_url_form_events_take_the_same_queue(self, cold_app, cold_bootstrap, tmp_path) -> None:
        """Launch Services can deliver the URL form; toLocalFile must queue too."""
        path = _md(tmp_path, "url-form.md", "# URL\n")
        QApplication.sendEvent(cold_app, QFileOpenEvent(QUrl.fromLocalFile(str(path))))
        assert cold_app.pending_open_paths() == (str(path),)
        context = cold_bootstrap()
        assert context.window._model.open_paths() == (str(path),)

    def test_mixed_valid_and_missing_cold_open_yields_the_existing_error_tab(
        self, cold_app, cold_bootstrap, tmp_path
    ) -> None:
        first = _md(tmp_path, "first.md", "# First\n")
        gone = tmp_path / "vanished.md"
        third = _md(tmp_path, "third.md", "# Third\n")
        for path in (first, gone, third):
            QApplication.sendEvent(cold_app, QFileOpenEvent(str(path)))
        context = cold_bootstrap()
        window = context.window
        tabs = window._model.tabs
        assert [tab.document.key for tab in tabs] == [str(first), str(gone), str(third)]
        assert tabs[0].error is None
        assert tabs[1].error is not None
        assert tabs[1].error.startswith("File not found")
        assert tabs[2].error is None
        assert window.current_tab() is tabs[2]  # last one active, error or not

    def test_duplicate_paths_in_the_queue_keep_the_existing_dedupe(
        self, cold_app, cold_bootstrap, tmp_path
    ) -> None:
        path = _md(tmp_path, "twice.md", "# Twice\n")
        QApplication.sendEvent(cold_app, QFileOpenEvent(str(path)))
        QApplication.sendEvent(cold_app, QFileOpenEvent(str(path)))
        context = cold_bootstrap()
        assert context.window.tab_count() == 1

    def test_finder_launch_suppresses_session_restore(
        self, cold_app, cold_bootstrap, tmp_path
    ) -> None:
        """Double-clicking a file is an explicit open request, not a resumption:
        last session's tabs must not pile on top and steal the active tab."""
        saved = _md(tmp_path, "session.md", "# Session\n")
        launched = _md(tmp_path, "launched.md", "# Launched\n")
        save_settings(Settings(open_files=(str(saved),)), settings_path())
        QApplication.sendEvent(cold_app, QFileOpenEvent(str(launched)))
        context = cold_bootstrap()
        assert context.settings.open_files == (str(saved),)  # bootstrap read the session
        assert context.app.launch_open_paths() == (str(launched),)
        # run()'s startup decision, extracted for exactly this assertion:
        assert (
            startup_paths(
                finder_opened=context.app.launch_open_paths(),
                saved_session=context.settings.open_files,
            )
            == []
        )
        assert startup_paths(saved_session=context.settings.open_files) == [saved]
        # ...and the window proves it: only the launched file is open.
        assert context.window._model.open_paths() == (str(launched),)


# --------------------------------------------------------- running instance --


@pytest.mark.gui
@pytest.mark.webengine
class TestRunningInstance:
    def test_multi_select_batch_opens_every_file_with_last_active(self, ctx, tmp_path) -> None:
        """Finder multi-select "Open With" arrives as one posted QFileOpenEvent
        per file; the live window opens them in delivery order."""
        window = ctx.window
        files = [_md(tmp_path, f"sel{i}.md", f"# Sel {i}\n") for i in range(4)]
        for path in files:
            QApplication.postEvent(ctx.app, QFileOpenEvent(str(path)))
        _wait_until(lambda: window.tab_count() == len(files))
        assert window._model.open_paths() == tuple(str(p) for p in files)
        active = window.current_tab()
        assert active is not None
        assert active.document.key == str(files[-1])

    def test_rapid_successive_events_open_every_file_with_last_active(self, ctx, tmp_path) -> None:
        """Synchronous back-to-back deliveries (the other face of an openFiles:
        batch): every event opens; the last one lands active."""
        window = ctx.window
        files = [_md(tmp_path, f"rapid{i}.md", f"# Rapid {i}\n") for i in range(3)]
        for path in files:
            assert QApplication.sendEvent(ctx.app, QFileOpenEvent(str(path)))
        assert window._model.open_paths() == tuple(str(p) for p in files)
        active = window.current_tab()
        assert active is not None
        assert active.document.key == str(files[-1])

    def test_single_event_still_opens_one_clean_tab(self, ctx, demo_file) -> None:
        """The single-file path verified pre-Phase-12 (`open -a`): unchanged."""
        window = ctx.window
        QApplication.sendEvent(ctx.app, QFileOpenEvent(str(demo_file)))
        assert window.tab_count() == 1
        tab = window.current_tab()
        assert tab is not None
        assert tab.error is None

    def test_missing_file_running_instance_uses_the_error_tab(self, ctx, tmp_path) -> None:
        gone = tmp_path / "ghost.md"
        window = ctx.window
        QApplication.sendEvent(ctx.app, QFileOpenEvent(str(gone)))
        tab = window.current_tab()
        assert tab is not None
        assert tab.error is not None
        assert tab.error.startswith("File not found")
        assert tab.title == f"⚠ {gone.name}"

    def test_mixed_valid_and_missing_running_instance(self, ctx, tmp_path) -> None:
        first = _md(tmp_path, "ok-a.md", "# A\n")
        gone = tmp_path / "nope.md"
        third = _md(tmp_path, "ok-c.md", "# C\n")
        window = ctx.window
        for path in (first, gone, third):
            QApplication.sendEvent(ctx.app, QFileOpenEvent(str(path)))
        tabs = window._model.tabs
        assert [tab.document.key for tab in tabs] == [str(first), str(gone), str(third)]
        assert tabs[1].error is not None
        assert tabs[0].error is None
        assert tabs[2].error is None
        assert window.current_tab() is tabs[2]

    def test_repeat_open_keeps_the_current_dedupe_behavior(self, ctx, demo_file) -> None:
        """Requirement 4: behaviour unchanged. Duplicate opens on the shared
        ``open_paths`` route (drag-and-drop, File > Open, and the pre-existing
        QFileOpenEvent parity test) focus the existing tab instead of
        duplicating -- pinned so Phase 12 can only preserve it."""
        window = ctx.window
        QApplication.sendEvent(ctx.app, QFileOpenEvent(str(demo_file)))
        first_id = window.current_tab().tab_id
        QApplication.sendEvent(ctx.app, QFileOpenEvent(str(demo_file)))
        assert window.tab_count() == 1
        assert window.current_tab().tab_id == first_id  # same tab, focused again
        # The drop route is the same code path and must agree.
        window.open_paths([demo_file])
        assert window.tab_count() == 1
        assert window.current_tab().tab_id == first_id


# ---------------------------------------------------- spec Info.plist (ast) --


class TestSpecDocumentTypes:
    """The Launch Services half of the contract, parsed from ``mdview.spec``
    with ``ast`` -- the spec never runs, so no PyInstaller, iconutil, or
    plutil is needed anywhere."""

    @staticmethod
    def _info_plist() -> dict[str, ast.expr]:
        tree = ast.parse(SPEC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "BUNDLE":
                for keyword in node.keywords:
                    if keyword.arg == "info_plist" and isinstance(keyword.value, ast.Dict):
                        return {
                            key.value: value
                            for key, value in zip(
                                keyword.value.keys, keyword.value.values, strict=True
                            )
                            if isinstance(key, ast.Constant) and isinstance(key.value, str)
                        }
        raise AssertionError(f"{SPEC} must pass info_plist to BUNDLE")

    def test_markdown_documents_declare_viewer_rank_and_public_markdown_uti(self) -> None:
        (doc_type,) = ast.literal_eval(self._info_plist()["CFBundleDocumentTypes"])
        assert doc_type["LSHandlerRank"] == "Viewer"  # not the old "Alternate" fallback
        assert doc_type["CFBundleTypeRole"] == "Viewer"
        assert "public.markdown" in doc_type["LSItemContentTypes"]
        assert "net.daringfiretown.markdown" in doc_type["LSItemContentTypes"]
        assert set(doc_type["CFBundleTypeExtensions"]) == {"md", "markdown"}
        # Phase 12 changed the rank and nothing else: the entry's key set is frozen.
        assert set(doc_type) == {
            "CFBundleTypeName",
            "CFBundleTypeRole",
            "LSHandlerRank",
            "LSItemContentTypes",
            "CFBundleTypeExtensions",
        }

    def test_the_rest_of_the_plist_keys_survive_the_change(self) -> None:
        plist = self._info_plist()
        assert {
            "CFBundleIconFile",
            "CFBundleName",
            "CFBundleDisplayName",
            "CFBundleIdentifier",
            "CFBundleShortVersionString",
            "CFBundleVersion",
            "CFBundlePackageType",
            "LSMinimumSystemVersion",
            "NSHighResolutionCapable",
            "NSPrincipalClass",
            "LSApplicationCategoryType",
            "CFBundleDocumentTypes",
            "NSDocumentsFolderUsageDescription",
        } <= set(plist)
