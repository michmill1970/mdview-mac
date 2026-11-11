"""Tab / document model (pure state machine).

The GUI keeps *no* tab state of its own: ``QTabWidget`` is a view over a
:class:`TabModel`.  That is what makes multi-tab behaviour testable without a
display, and it is where the two fiddly rules live:

* opening an already-open file focuses the existing tab instead of duplicating;
* closing the active tab activates its right neighbour, else its left one,
  else nothing (the window then shows the empty state).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mdview.core.document import Document, inspect_openable

__all__ = ["Tab", "TabModel"]

TabId = int
ChangeListener = Callable[[], None]


@dataclass(slots=True)
class Tab:
    """One open document plus the per-tab view state worth restoring."""

    tab_id: TabId
    document: Document
    #: Vertical scroll offset in CSS pixels, saved when the tab is hidden so that
    #: switching tabs feels like a browser rather than a reload.
    scroll_y: int = 0
    #: Set when the file changed on disk and the view is stale.
    stale: bool = False
    #: When not ``None`` the tab shows an error page instead of the document:
    #: the string is the human-readable reason (Phase 6: failed opens must be
    #: visible, not silent).  Re-opening the path successfully clears it.
    error: str | None = None

    @property
    def title(self) -> str:
        if self.error is None:
            return self.document.title
        return f"⚠ {self.document.title}"


@dataclass(slots=True)
class TabModel:
    """Ordered tabs with one optional active tab.

    ``on_change`` is called after every mutation so exactly one place (the
    window) has to re-sync the tab bar.
    """

    on_change: ChangeListener | None = None
    _tabs: list[Tab] = field(default_factory=list, repr=False)
    _active_id: TabId | None = field(default=None, repr=False)
    _next_id: TabId = field(default=1, repr=False)

    # -------------------------------------------------------------- read --
    @property
    def tabs(self) -> tuple[Tab, ...]:
        return tuple(self._tabs)

    @property
    def is_empty(self) -> bool:
        return not self._tabs

    @property
    def count(self) -> int:
        return len(self._tabs)

    @property
    def active(self) -> Tab | None:
        tab = self.active_tab_id
        return None if tab is None else self._find(tab)

    @property
    def active_tab_id(self) -> TabId | None:
        return self._active_id

    def tab(self, tab_id: TabId) -> Tab | None:
        return self._find(tab_id)

    def index_of(self, tab_id: TabId) -> int | None:
        tab = self._find(tab_id)
        return None if tab is None else self._tabs.index(tab)

    def find_by_path(self, path: Path | str) -> Tab | None:
        """Existing tab for this path, if any (used for de-duplication)."""
        for tab in self._tabs:
            if tab.document.matches(path):
                return tab
        return None

    def documents(self) -> tuple[Document, ...]:
        return tuple(tab.document for tab in self._tabs)

    def open_paths(self) -> tuple[str, ...]:
        """Absolute paths in tab order -- this is what ``settings.open_files`` stores."""
        return tuple(tab.document.key for tab in self._tabs)

    # ------------------------------------------------------------- write --
    def open_document(self, document: Document, *, activate: bool = True) -> Tab:
        """Add a document, or focus the tab that already shows it.

        Re-opening a path whose tab is showing an error page upgrades that
        tab in place (same id, same position): the reason is cleared and the
        caller re-renders the now-readable document.
        """
        existing = self.find_by_path(document.path)
        if existing is not None:
            recovered = existing.error is not None
            existing.error = None
            if activate:
                self.activate(existing.tab_id)
            if recovered:
                self._notify()  # the "⚠ " title prefix has to go, active or not
            return existing
        tab = Tab(tab_id=self._next_id, document=document)
        self._next_id += 1
        self._tabs.append(tab)
        if activate:
            self._active_id = tab.tab_id
        self._notify()
        return tab

    def open_path(self, path: Path | str, *, activate: bool = True) -> Tab:
        """Convenience wrapper: :meth:`Document.open` + :meth:`open_document`."""
        return self.open_document(Document.open(path), activate=activate)

    def open_error(self, path: Path | str, reason: str, *, activate: bool = True) -> Tab:
        """Open (or upgrade) a tab showing *why* ``path`` could not be opened.

        The tab holds a plain :class:`Document` handle for the path -- it is
        never read while ``tab.error`` is set; the view renders
        :func:`~mdview.core.document.error_page_markdown` instead.
        """
        document = Document(Path(path))
        existing = self.find_by_path(document.path)
        if existing is not None:
            existing.error = reason
            if activate:
                self.activate(existing.tab_id)
            self._notify()  # the tooltip carries the reason and must refresh
            return existing
        tab = Tab(tab_id=self._next_id, document=document, error=reason)
        self._next_id += 1
        self._tabs.append(tab)
        if activate:
            self._active_id = tab.tab_id
        self._notify()
        return tab

    def activate(self, tab_id: TabId) -> bool:
        """Make a tab active; returns whether the tab existed."""
        if self._find(tab_id) is None:
            return False
        if self._active_id == tab_id:
            return True
        self._active_id = tab_id
        self._notify()
        return True

    def activate_index(self, index: int) -> Tab | None:
        """Activate the tab at ``index`` (used by ``Cmd+1``..``Cmd+9``)."""
        if not 0 <= index < len(self._tabs):
            return None
        tab = self._tabs[index]
        self.activate(tab.tab_id)
        return tab

    def activate_next(self) -> Tab | None:
        return self._step(1)

    def activate_previous(self) -> Tab | None:
        return self._step(-1)

    def close(self, tab_id: TabId) -> bool:
        """Close a tab, choosing the next active tab as macOS apps do."""
        tab = self._find(tab_id)
        if tab is None:
            return False
        index = self._tabs.index(tab)
        was_active = self._active_id == tab_id
        self._tabs.pop(index)
        if was_active:
            if not self._tabs:
                self._active_id = None
            else:
                neighbour = min(index, len(self._tabs) - 1)
                self._active_id = self._tabs[neighbour].tab_id
        self._notify()
        return True

    def close_all(self) -> None:
        if not self._tabs:
            return
        self._tabs.clear()
        self._active_id = None
        self._notify()

    def move(self, tab_id: TabId, new_index: int) -> bool:
        """Reorder (drives ``TabWidget.tabMoved``); keeps ids stable."""
        tab = self._find(tab_id)
        if tab is None:
            return False
        self._tabs.remove(tab)
        self._tabs.insert(max(0, min(new_index, len(self._tabs))), tab)
        self._notify()
        return True

    def reorder_to(self, order: list[TabId]) -> bool:
        """Apply a full ordering produced by the tab bar (drag-and-drop)."""
        by_id = {tab.tab_id: tab for tab in self._tabs}
        if sorted(by_id) != sorted(order):
            return False
        self._tabs = [by_id[tab_id] for tab_id in order]
        self._notify()
        return True

    def set_scroll(self, tab_id: TabId, scroll_y: int) -> None:
        """Remember a tab's scroll offset without notifying (it is not visible state)."""
        tab = self._find(tab_id)
        if tab is not None:
            tab.scroll_y = max(0, scroll_y)

    def mark_stale(self, path: Path | str, stale: bool = True) -> Tab | None:
        """Flag every tab showing ``path`` as stale / fresh."""
        affected: Tab | None = None
        for tab in self._tabs:
            if tab.document.matches(path):
                tab.stale = stale
                affected = tab
        if affected is not None:
            self._notify()
        return affected

    # ------------------------------------------------------------ private --
    def _step(self, delta: int) -> Tab | None:
        if not self._tabs:
            return None
        current = self._active_index()
        index = 0 if current is None else (current + delta) % len(self._tabs)
        tab = self._tabs[index]
        self.activate(tab.tab_id)
        return tab

    def _active_index(self) -> int | None:
        tab = self.active
        return None if tab is None else self._tabs.index(tab)

    def _find(self, tab_id: TabId) -> Tab | None:
        for tab in self._tabs:
            if tab.tab_id == tab_id:
                return tab
        return None

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def restore(self, paths: Iterable[Path | str], *, active_path: str | None = None) -> list[Tab]:
        """Re-open the previous session.

        Missing files are skipped silently (a stale session is not a failed
        open request), but a file that exists and cannot be opened gets an
        error tab, like any explicit open (Phase 6: never fail silently).
        """
        opened: list[Tab] = []
        for path in paths:
            doc = Document(path=Path(path))
            if not doc.exists:
                continue
            reason = inspect_openable(path)
            if reason is not None:
                opened.append(self.open_error(path, reason, activate=False))
                continue
            opened.append(self.open_document(doc, activate=False))
        if not opened:
            return opened
        target = next((t for t in opened if active_path and t.document.key == active_path), None)
        self._active_id = (target or opened[-1]).tab_id
        self._notify()
        return opened
