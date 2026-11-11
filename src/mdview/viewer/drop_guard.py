"""File-drop plumbing for the Qt shell (Phase 10).

Two small pieces, no widget state of its own:

* :func:`markdown_paths_from_mime` -- the drop-side policy: keep local file
  URLs with a ``.md``/``.markdown`` suffix (case-insensitive, same
  ``SUPPORTED_SUFFIXES`` gate as Finder registration), in drop order.
  Everything else in a mixed drop is silently ignored.
* :class:`DragForwardFilter` -- the integration fix.  A drag over the content
  area is delivered to QtWebEngine's render surface, never to the window, so
  a file drop would otherwise be swallowed by the page (at best: Chromium
  navigates to the dropped ``file://`` URL).  Two Qt behaviours make this
  awkward, both verified offscreen against Qt 6.12:

  1. ``QApplication::notify`` retargets a drag to the *nearest ancestor that
     accepts drops* before any event filter runs -- a filter on a widget that
     does not accept drops never fires.
  2. The render surface is not one widget: it is a ``QQuickWidget`` delegate
     created lazily on first show, with Chromium's own input widgets nested
     inside it, appearing and disappearing as pages load.

  So the filter marks every widget it watches as a drop target (stopping the
  retargeting there) and re-installs itself on every widget that later shows
  up in the watched subtree.  Watched drag events are forwarded to the
  window's drop sink (a :class:`DropSink`) and consumed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

from PySide6.QtCore import QChildEvent, QEvent, QMimeData, QObject
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QWidget

from mdview.core.document import is_supported

_DRAG_EVENT_TYPES = frozenset(
    {
        QEvent.Type.DragEnter,
        QEvent.Type.DragMove,
        QEvent.Type.DragLeave,
        QEvent.Type.Drop,
    }
)

#: Dynamic property marking a widget whose drags already belong to a guard.
_WATCHED_PROPERTY = "mdviewDropGuarded"


class DropSink(Protocol):
    """The four drag events, as a window implements them (Qt naming minus the
    ``Event`` suffix so the same methods serve as Qt virtuals and sink)."""

    def drag_enter(self, event: QDragEnterEvent) -> None: ...

    def drag_move(self, event: QDragMoveEvent) -> None: ...

    def drag_leave(self, event: QDragLeaveEvent) -> None: ...

    def drop(self, event: QDropEvent) -> None: ...


def markdown_paths_from_mime(mime: QMimeData) -> list[Path]:
    """Local Markdown file paths in *drop order*; everything else is ignored.

    Only ``.md``/``.markdown`` (case-insensitive) qualify -- deliberately the
    same suffix set Finder registers (``SUPPORTED_SUFFIXES``), so a drop
    invites exactly what double-click would.  Once a path passes this gate it
    opens through the ordinary open-file policy (error tabs included).
    """
    paths: list[Path] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        if is_supported(path):
            paths.append(path)
    return paths


class DragForwardFilter(QObject):
    """Claims drag events on the watched widget tree for a :class:`DropSink`.

    Returning ``True`` consumes the event: the page never sees it (no
    navigation to a dropped ``file://``), while the accept/ignore decision the
    sink made on the event object is what the window system reads back as the
    drag result -- Qt inspects the event after dispatch, not the receiver, so
    the cursor and the drop outcome stay correct.
    """

    def __init__(self, sink: DropSink, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sink = sink

    # ---------------------------------------------------------- watch list --
    def install_on(self, widget: QWidget) -> None:
        """Watch *widget*; idempotent, so re-walking a subtree is cheap.

        The marker is a Qt dynamic property rather than a Python attribute:
        Shiboken may hand out a fresh wrapper for the same C++ widget, and the
        watch belongs to the widget, not to the wrapper.
        """
        if widget.property(_WATCHED_PROPERTY):
            return
        widget.setProperty(_WATCHED_PROPERTY, True)
        # Without this, Qt retargets the drag to an ancestor first and this
        # filter would never run (see module docstring).
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)

    def install_tree(self, root: QWidget) -> None:
        """Watch *root* and every widget currently inside it."""
        for widget in (root, *root.findChildren(QWidget)):
            self.install_on(widget)

    # ------------------------------------------------------------ delivery --
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if not isinstance(obj, QWidget):
            return False
        kind = event.type()
        if kind == QEvent.Type.ChildAdded:
            # Chromium's input widgets live one level below the delegate and
            # come and go with the page: whatever appears below a watched
            # widget gets watched too.
            child = cast("QChildEvent", event).child()
            if isinstance(child, QWidget):
                self.install_tree(child)
            return False  # ChildAdded itself is not ours to consume
        if kind not in _DRAG_EVENT_TYPES:
            return False
        sink = self._sink
        if kind == QEvent.Type.DragEnter:
            sink.drag_enter(cast("QDragEnterEvent", event))
        elif kind == QEvent.Type.DragMove:
            sink.drag_move(cast("QDragMoveEvent", event))
        elif kind == QEvent.Type.DragLeave:
            sink.drag_leave(cast("QDragLeaveEvent", event))
        else:  # QEvent.Type.Drop
            sink.drop(cast("QDropEvent", event))
        return True
