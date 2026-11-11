"""Drag-and-drop opening (Phase 10), driven offscreen.

The drop path is synthesized the way Qt's drag manager delivers it: real
``QDragEnterEvent`` / ``QDropEvent`` objects (mime data kept alive on the
event) sent to the widget a Finder drag would hit -- the tab bar, the empty
state, or the web view and its render surface.  The window's ``DropSink``
handlers are the single landing point, so what these tests assert is what
Finder drops do.

Two Qt 6.12 behaviours are pinned here because the whole design rests on them
(both measured, not assumed): an unguarded ``QWebEngineView`` accepts drops
and keeps them, and drag events are retargeted to the nearest drop-accepting
ancestor *before* event filters run -- which is why the guard marks the
widgets it watches as drop targets.

``QFileOpenEvent`` (Finder double-click / Dock-icon drop) must keep routing
through the same ``open_paths`` logic -- tested here as the parity guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QMimeData, QObject, QPointF, Qt, QUrl
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFileOpenEvent,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QWidget

from mdview.core.document import inspect_openable
from mdview.viewer.application import AppContext, bootstrap
from mdview.viewer.drop_guard import DragForwardFilter, markdown_paths_from_mime
from mdview.viewer.main_window import MainWindow

pytestmark = [pytest.mark.gui, pytest.mark.webengine]


@pytest.fixture
def ctx(seeded_vendor: Path) -> object:
    context = bootstrap(["mdview-test"], ensure_bundled_assets=False)
    yield context
    context.window.close()


# ----------------------------------------------------------- event helpers --


def _mime_for(paths: list[Path], extra_urls: list[str] = ()) -> QMimeData:
    mime = QMimeData()
    urls = [QUrl.fromLocalFile(str(path)) for path in paths]
    urls += [QUrl(text) for text in extra_urls]
    mime.setUrls(urls)
    return mime


def _drop(paths: list[Path], extra_urls: list[str] = ()) -> QDropEvent:
    mime = _mime_for(paths, extra_urls)
    event = QDropEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event._mime = mime  # keep the QMimeData alive as long as the event lives
    return event


def _drag_enter(paths: list[Path], extra_urls: list[str] = ()) -> QDragEnterEvent:
    mime = _mime_for(paths, extra_urls)
    event = QDragEnterEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event._mime = mime
    return event


def _drag_move(paths: list[Path], extra_urls: list[str] = ()) -> QDragMoveEvent:
    mime = _mime_for(paths, extra_urls)
    event = QDragMoveEvent(
        QPointF(24, 24),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event._mime = mime
    return event


def _drag_leave() -> QDragLeaveEvent:
    return QDragLeaveEvent()


def _drag_and_drop(target: QWidget, paths: list[Path], extra_urls: list[str] = ()):
    """The real Qt 6 delivery flow, start to end.

    ``QApplication::notify`` dispatches ``DragMove``/``Drop``/``DragLeave``
    *only* to the ``QDragManager`` current target -- the widget whose
    ``DragEnter`` was accepted.  Tests therefore always enter first, exactly
    like a drag from Finder does.
    """
    enter = _drag_enter(paths, extra_urls)
    QApplication.sendEvent(target, enter)
    drop = _drop(paths, extra_urls)
    QApplication.sendEvent(target, drop)
    return enter, drop


def _md(tmp_path: Path, name: str, body: str = "# hi\n") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _open_titles(window: MainWindow) -> list[str]:
    return [tab.title for tab in window._model.tabs]


# -------------------------------------------------------------- mime policy --


class TestMarkdownPathsFromMime:
    def test_keeps_only_markdown_and_preserves_drop_order(self, tmp_path: Path) -> None:
        md = _md(tmp_path, "a.md")
        png = tmp_path / "photo.png"
        markdown = _md(tmp_path, "b.markdown")
        txt = tmp_path / "notes.txt"
        mime = _mime_for([markdown, png, md, txt])
        assert markdown_paths_from_mime(mime) == [markdown, md]

    def test_suffix_gate_is_case_insensitive(self, tmp_path: Path) -> None:
        upper = _md(tmp_path, "UPPER.MD")
        mixed = _md(tmp_path, "Mixed.Markdown")
        png_upper = tmp_path / "PHOTO.PNG"
        mime = _mime_for([upper, mixed, png_upper])
        assert markdown_paths_from_mime(mime) == [upper, mixed]

    def test_non_local_urls_are_ignored(self, tmp_path: Path) -> None:
        local = _md(tmp_path, "here.md")
        mime = _mime_for([local], extra_urls=["http://example.com/x.md", "data:text/x,hi"])
        assert markdown_paths_from_mime(mime) == [local]

    def test_empty_or_text_only_mime_yields_nothing(self) -> None:
        mime = QMimeData()
        mime.setText("just some text")
        assert markdown_paths_from_mime(mime) == []


# ------------------------------------------------------- the forwarding filter --


class _RecordingSink:
    """A :class:`DropSink` recording which handler dispatch reached, applying
    the same accept/ignore policy as ``MainWindow`` (the filter must never
    decide anything itself)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def drag_enter(self, event: QDragEnterEvent) -> None:
        self.calls.append("drag_enter")
        self._judge(event)

    def drag_move(self, event: QDragMoveEvent) -> None:
        self.calls.append("drag_move")
        self._judge(event)

    def drag_leave(self, event: QDragLeaveEvent) -> None:
        self.calls.append("drag_leave")
        event.accept()

    def drop(self, event: QDropEvent) -> None:
        self.calls.append("drop")
        self._judge(event)

    @staticmethod
    def _judge(event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> None:
        if markdown_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()


class TestDragForwardFilter:
    """The guard's contract: claim the four drag events for the sink and
    consume them, and leave every other event alone."""

    @staticmethod
    def _guarded(tmp_path: Path) -> tuple[QWidget, _RecordingSink]:
        widget = QWidget()  # stand-in for the renderer delegate
        sink = _RecordingSink()
        DragForwardFilter(sink, widget).install_on(widget)  # parented: stays alive
        return widget, sink

    def test_install_on_makes_the_widget_a_drop_target(self) -> None:
        """Required, not cosmetic: Qt retargets a drag to the nearest
        drop-accepting ancestor *before* filters run, so a filter on a widget
        that refuses drops would simply never fire."""
        widget = QWidget()
        assert not widget.acceptDrops()
        DragForwardFilter(_RecordingSink(), widget).install_on(widget)
        assert widget.acceptDrops()

    def test_drag_enter_is_forwarded_and_consumed(self, tmp_path: Path) -> None:
        widget, sink = self._guarded(tmp_path)
        enter = _drag_enter([_md(tmp_path, "x.md")])
        # sendEvent() reports True only because the filter consumed the event,
        # which is precisely what keeps it away from the web page.
        assert QApplication.sendEvent(widget, enter) is True
        assert enter.isAccepted()
        assert sink.calls == ["drag_enter"]

    def test_move_and_drop_are_forwarded(self, tmp_path: Path) -> None:
        widget, sink = self._guarded(tmp_path)
        target = _md(tmp_path, "x.md")
        # Enter must come first and the drop must precede any leave: Qt only
        # dispatches DragMove/Drop/DragLeave to the drag manager's live target.
        for event in (_drag_enter([target]), _drag_move([target]), _drop([target])):
            assert QApplication.sendEvent(widget, event) is True
        assert sink.calls == ["drag_enter", "drag_move", "drop"]

    def test_drag_leave_is_forwarded(self, tmp_path: Path) -> None:
        widget, sink = self._guarded(tmp_path)
        target = _md(tmp_path, "x.md")
        assert QApplication.sendEvent(widget, _drag_enter([target])) is True
        leave = _drag_leave()
        assert QApplication.sendEvent(widget, leave) is True
        assert sink.calls == ["drag_enter", "drag_leave"]

    def test_only_the_drag_events_are_claimed(self, tmp_path: Path) -> None:
        """Filter-level view of the same contract: drag types are consumed,
        everything else -- and non-widget objects -- pass straight through."""
        widget, _ = self._guarded(tmp_path)
        guard = next(obj for obj in widget.children() if isinstance(obj, DragForwardFilter))
        enter = _drag_enter([_md(tmp_path, "x.md")])
        assert guard.eventFilter(widget, enter) is True
        assert guard.eventFilter(widget, QEvent(QEvent.Type.User)) is False
        assert guard.eventFilter(QObject(widget), enter) is False

    def test_the_sink_decides_rejection(self, tmp_path: Path) -> None:
        """A text-only drag is forwarded unchanged: the filter claims delivery,
        the sink does the refusing (no overlay, OS shows the denied cursor)."""
        widget, sink = self._guarded(tmp_path)
        mime = QMimeData()
        mime.setText("not a file")
        enter = QDragEnterEvent(
            QPointF(20, 20),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        enter._mime = mime
        assert QApplication.sendEvent(widget, enter) is True
        assert sink.calls == ["drag_enter"]
        assert not enter.isAccepted()


class _DropCounter(QWidget):
    """A window-shaped sink: a widget that accepts drops and records them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.calls: list[str] = []

    def drag_enter(self, event: QDragEnterEvent) -> None:
        self.calls.append("drag_enter")
        event.acceptProposedAction()

    def drag_move(self, event: QDragMoveEvent) -> None:
        self.calls.append("drag_move")
        event.acceptProposedAction()

    def drag_leave(self, event: QDragLeaveEvent) -> None:
        self.calls.append("drag_leave")
        event.accept()

    def drop(self, event: QDropEvent) -> None:
        self.calls.append("drop")
        event.acceptProposedAction()


# ---------------------------------------------------------- mechanism proof --


class TestWhyTheGuardExists:
    """Pinned Qt facts, measured rather than assumed: an unguarded
    ``QWebEngineView`` accepts drops for its own HTML5 drag-and-drop support,
    so a content-area drop dies inside the page and never reaches the window.
    The guard is what routes it; if Qt ever stops claiming drags these tests
    say so, and the guard becomes dead weight worth deleting."""

    def test_the_web_view_claims_drops_and_the_guard_takes_them_back(
        self, tmp_path: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        """One view, both halves of the story: unguarded, the view claims the
        drop and the window never sees it; guard the same view and that very
        same drop lands in the sink."""
        host = _DropCounter()
        host.resize(300, 200)
        qtbot.addWidget(host)  # type: ignore[attr-defined]
        view = QWebEngineView(host)
        view.setGeometry(0, 0, 300, 200)
        host.show()
        qtbot.waitExposed(host)  # type: ignore[attr-defined]
        assert view.acceptDrops(), "QtWebEngine no longer claims drops by default"

        target = _md(tmp_path, "page.md")
        _drag_and_drop(view, [target])
        assert host.calls == [], "the page did not swallow the drop after all"

        DragForwardFilter(host, view).install_on(view)
        enter, event = _drag_and_drop(view, [target])
        assert enter.isAccepted()
        assert event.isAccepted()
        assert host.calls == ["drag_enter", "drop"]

    def test_the_guard_extends_itself_to_widgets_created_later(
        self, tmp_path: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        """Chromium's input widgets are born one level below the delegate,
        whenever the page feels like it; watching a subtree is recursive."""
        host = _DropCounter()
        host.resize(300, 200)
        qtbot.addWidget(host)  # type: ignore[attr-defined]
        guard = DragForwardFilter(host, host)
        guard.install_on(host)
        inner = QWidget(host)  # created after the watch was established
        deepest = QWidget(inner)
        deepest.setGeometry(0, 0, 50, 50)
        host.show()
        qtbot.waitExposed(host)  # type: ignore[attr-defined]
        assert deepest.property("mdviewDropGuarded")
        assert deepest.acceptDrops(), "without this Qt retargets the drag above the filter"

        target = _md(tmp_path, "deep.md")
        _drag_and_drop(deepest, [target])
        assert host.calls == ["drag_enter", "drop"]


# ------------------------------------------------------------ drop outcomes --


class TestDropOpensFiles:
    def test_multiple_files_open_as_tabs_in_drop_order(
        self, ctx: AppContext, tmp_path: Path
    ) -> None:
        window = ctx.window
        first = _md(tmp_path, "first.md", "# First\n")
        second = _md(tmp_path, "second.markdown", "# Second\n")
        enter, event = _drag_and_drop(window, [first, second])
        assert enter.isAccepted()
        assert event.isAccepted()
        assert event.dropAction() == Qt.DropAction.CopyAction
        assert window.tab_count() == 2
        assert window._model.open_paths() == (str(first), str(second))
        assert _open_titles(window) == ["First", "Second"]

    def test_mixed_drop_opens_markdown_and_ignores_the_rest(
        self, ctx: AppContext, demo_file: Path, tmp_path: Path
    ) -> None:
        """Non-Markdown entries in a mixed drop vanish silently: no tab, no
        error tab, no status-bar complaint."""
        window = ctx.window
        noise = tmp_path / "photo.png"
        enter, event = _drag_and_drop(window, [noise, demo_file])
        assert enter.isAccepted()
        assert event.isAccepted()
        assert window.tab_count() == 1
        tab = window.current_tab()
        assert tab is not None
        assert tab.error is None
        assert demo_file.name in str(tab.document.path)

    def test_non_markdown_only_drop_is_ignored(self, ctx: AppContext, tmp_path: Path) -> None:
        window = ctx.window
        enter, event = _drag_and_drop(window, [tmp_path / "photo.png", tmp_path / "sheet.csv"])
        assert not enter.isAccepted()  # no overlay, no dashed invitation either
        assert not event.isAccepted()
        assert window.tab_count() == 0
        assert window._drop_overlay.isHidden()

    def test_missing_markdown_gets_the_existing_error_tab(
        self, ctx: AppContext, tmp_path: Path
    ) -> None:
        """The drop reuses open_paths: a vanished file behaves exactly like a
        failed File > Open (themed error tab, same title convention)."""
        window = ctx.window
        gone = tmp_path / "kitchen-sink.md"
        _, event = _drag_and_drop(window, [gone])
        assert event.isAccepted()  # accepted drop; the *open* is what fails, visibly
        assert window.tab_count() == 1
        tab = window.current_tab()
        assert tab is not None
        assert tab.error is not None
        assert tab.title == f"⚠ {gone.name}"

    def test_unreadable_markdown_gets_the_existing_error_tab(
        self, ctx: AppContext, tmp_path: Path
    ) -> None:
        secret = tmp_path / "secret.md"
        secret.write_text("# hush\n", encoding="utf-8")
        secret.chmod(0o000)
        try:
            reason = inspect_openable(secret)  # root (CI container): chmod is moot
        finally:
            secret.chmod(0o644)
        if reason is None:
            pytest.skip("cannot make a file unreadable for this user")
        window = ctx.window
        secret.chmod(0o000)
        try:
            _, event = _drag_and_drop(window, [secret])
        finally:
            secret.chmod(0o644)
        assert event.isAccepted()
        tab = window.current_tab()
        assert tab is not None
        assert tab.error is not None
        assert tab.error.startswith("Cannot read")

    def test_drop_onto_the_empty_state_opens(self, ctx: AppContext, demo_file: Path) -> None:
        window = ctx.window
        assert window._stack.currentIndex() == 0  # empty state visible
        empty = window._stack.widget(0)
        assert empty is not None
        # Qt's notify() propagates drag events up the parent chain, so an
        # enter at the empty-state widget lands on the window's handlers.
        _, event = _drag_and_drop(empty, [demo_file])
        assert event.isAccepted()
        assert window.tab_count() == 1

    def test_drop_onto_the_tab_bar_opens(
        self, ctx: AppContext, demo_file: Path, tmp_path: Path
    ) -> None:
        window = ctx.window
        window.open_paths([demo_file])
        second = _md(tmp_path, "second.md", "# Second\n")
        _, event = _drag_and_drop(window._tab_view.tabBar(), [second])
        assert event.isAccepted()
        assert window.tab_count() == 2
        assert window._model.open_paths() == (str(demo_file), str(second))


# -------------------------------------------------- over the web view itself --


class TestDropsOverWebContent:
    """The integration constraint: a drag over the page is claimed by
    QtWebEngine's render surface, not by the window.  The drop guard
    event-filters the view and every widget child it grows later (Qt 6.12: a
    ``QQuickWidget`` delegate appearing on first show, with Chromium's own
    input widgets nested inside), so those events are claimed for the window
    exactly once and the page never gets to swallow them or navigate to a
    dropped ``file://``."""

    @staticmethod
    def _child_widgets(window: MainWindow) -> list[QWidget]:
        """The view's widget children -- exactly what the drop guard covers."""
        view = window.current_view()
        assert view is not None
        return list(view.findChildren(QWidget))

    def test_drag_and_drop_over_the_renderer_open_files(
        self, ctx: AppContext, demo_file: Path, tmp_path: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        window = ctx.window
        window.open_paths([demo_file])
        window.show()
        qtbot.waitExposed(window)  # type: ignore[attr-defined]
        children = self._child_widgets(window)
        # Tripwires for the documented Qt 6.12 facts: the delegate is a
        # QQuickWidget born on first show, and the guard caught it anyway.
        # Matched by class name on purpose -- importing QtQuickWidgets in a
        # process that also loaded QtWebEngine segfaults on headless runners.
        renderers = [
            widget for widget in children if widget.metaObject().className() == "QQuickWidget"
        ]
        assert renderers, "QtWebEngine created no renderer child; update the drop guard"
        for widget in children:
            assert widget.property("mdviewDropGuarded"), f"{widget} is not forwarding drops"
            assert widget.acceptDrops()

        for renderer in renderers:
            new_doc = _md(tmp_path, f"dropped-{len(window._model.tabs)}.md", "# Dropped\n")
            enter = _drag_enter([new_doc])
            QApplication.sendEvent(renderer, enter)
            assert enter.isAccepted(), "renderer drag-enter did not reach the window"
            assert window._drop_overlay.isVisible()
            event = _drop([new_doc])
            QApplication.sendEvent(renderer, event)
            assert event.isAccepted()
            assert window.tab_count() == 2
            assert window._drop_overlay.isHidden()
            window._actions["closeTab"].trigger()

    def test_drag_leave_over_the_renderer_hides_the_feedback(
        self, ctx: AppContext, demo_file: Path, tmp_path: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        window = ctx.window
        window.open_paths([demo_file])
        window.show()
        qtbot.waitExposed(window)  # type: ignore[attr-defined]
        renderers = self._child_widgets(window)
        assert renderers
        QApplication.sendEvent(renderers[0], _drag_enter([tmp_path / "x.md"]))
        assert window._drop_overlay.isVisible()
        QApplication.sendEvent(renderers[0], _drag_leave())
        qtbot.waitUntil(  # type: ignore[attr-defined]
            lambda: window._drop_overlay.isHidden(), timeout=2000
        )


# ------------------------------------------------------------ drop feedback --


class TestDropFeedbackOverlay:
    def test_overlay_shows_on_enter_and_hides_on_leave(
        self, ctx: AppContext, demo_file: Path, tmp_path: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        window = ctx.window
        window.show()
        qtbot.waitExposed(window)  # type: ignore[attr-defined]
        assert window._drop_overlay.isHidden()
        QApplication.sendEvent(window, _drag_enter([demo_file, tmp_path / "ghost.md"]))
        assert window._drop_overlay.isVisible()
        QApplication.sendEvent(window, _drag_leave())
        qtbot.waitUntil(  # type: ignore[attr-defined]
            lambda: window._drop_overlay.isHidden(), timeout=2000
        )

    def test_drop_hides_the_overlay_immediately(
        self, ctx: AppContext, demo_file: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        window = ctx.window
        window.show()
        qtbot.waitExposed(window)  # type: ignore[attr-defined]
        assert window._drop_overlay.isHidden()
        QApplication.sendEvent(window, _drag_enter([demo_file]))
        assert window._drop_overlay.isVisible()
        QApplication.sendEvent(window, _drop([demo_file]))
        assert window._drop_overlay.isHidden()
        assert window.tab_count() == 1

    def test_non_markdown_drag_shows_no_feedback_and_is_rejected(
        self, ctx: AppContext, tmp_path: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        """No dashed invitation for a drag the window will refuse: a
        Markdown-only promise must stay honest."""
        window = ctx.window
        window.show()
        qtbot.waitExposed(window)  # type: ignore[attr-defined]
        enter = _drag_enter([tmp_path / "photo.png"])
        QApplication.sendEvent(window, enter)
        assert not enter.isAccepted()
        assert window._drop_overlay.isHidden()

    def test_overlay_follows_the_active_palette(self, ctx: AppContext) -> None:
        """Styled from the resolved palette at theme time, so switching
        palette restyles it -- one source of truth for page and chrome."""
        window = ctx.window
        window._set_palette("dark", "dracula-dark")
        accent = window.selection().palette.colors["accent"]
        sheet = window._drop_overlay.styleSheet()
        assert accent in sheet
        assert "dashed" in sheet

    def test_overlay_never_steals_the_drop(
        self, ctx: AppContext, demo_file: Path, qtbot: pytest.FixtureRequest
    ) -> None:
        """The feedback must not block the drop it advertises: drag delivery
        hit-tests with childAt(), and a transparent overlay is skipped --
        including its label subtree."""
        window = ctx.window
        window.open_paths([demo_file])
        window.show()
        qtbot.waitExposed(window)  # type: ignore[attr-defined]
        window._show_drop_overlay()
        assert window._drop_overlay.isVisible()
        center = QPointF(window._stack.width() / 2, window._stack.height() / 2)
        hit = window._stack.childAt(center.toPoint())
        assert hit is not window._drop_overlay
        assert window._drop_overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


# ------------------------------------------------------ Finder / Dock parity --


class TestDockOpenEventParity:
    """``QFileOpenEvent`` (Finder double-click, Dock-icon drop) must keep
    opening tabs through the same ``open_paths`` route as in-window drops."""

    def test_file_open_event_opens_a_tab_and_dedupes(
        self, ctx: AppContext, demo_file: Path
    ) -> None:
        window = ctx.window
        assert QApplication.sendEvent(ctx.app, QFileOpenEvent(str(demo_file)))
        assert window.tab_count() == 1
        tab = window.current_tab()
        assert tab is not None
        assert tab.error is None
        # Re-opening the same path upgrades/focuses, never duplicates.
        QApplication.sendEvent(ctx.app, QFileOpenEvent(str(demo_file)))
        assert window.tab_count() == 1

    def test_file_open_event_and_drop_share_one_route(
        self, ctx: AppContext, demo_file: Path, tmp_path: Path
    ) -> None:
        window = ctx.window
        QApplication.sendEvent(ctx.app, QFileOpenEvent(str(demo_file)))
        second = _md(tmp_path, "second.md", "# Second\n")
        _, event = _drag_and_drop(window, [second])
        assert event.isAccepted()
        assert window.tab_count() == 2
        assert window._model.open_paths() == (str(demo_file), str(second))
