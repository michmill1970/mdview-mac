"""QTabWidget view of the pure :class:`~mdview.core.tabs.TabModel`.

The tab bar holds *no* state: ``TabModel`` is the source of truth and this
widget both reads from it (``sync()``) and writes to it (user clicks a close
button, drags a tab).  Each tab's widget is a :class:`ContentView`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabWidget, QWidget

from mdview.core.tabs import Tab, TabId, TabModel
from mdview.viewer.content_view import ContentView

TabFactory = Callable[[], ContentView]

#: macOS tab-chrome decision (Phase 4 field report, see DESIGN.md "macOS chrome
#: notes").  QMacStyle renders document-mode tabs nearly flat and reveals each
#: tab's close button *only while that tab is hovered* ("Make close button
#: visible only on the hovered tab", ``PE_IndicatorTabClose`` in qtbase's
#: qmacstyle_mac.mm).  Classic tab view keeps the close button on every tab and
#: on macOS 26 ("Liquid Glass") draws tabs as native button bezels with a
#: clearly selected tab -- discoverable and native, without stylesheet hacks.
#: Off macOS (Linux CI, offscreen tests) document mode stays on so the Fusion
#: look the visual tests were tuned against does not change.
_USE_DOCUMENT_MODE_TABS = sys.platform != "darwin"


class TabView(QTabWidget):
    """Tab strip + content panes, mirrored against a TabModel."""

    def __init__(
        self, model: TabModel, tab_factory: TabFactory, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._model = model
        self._tab_factory = tab_factory
        self._views: dict[TabId, ContentView] = {}
        self._syncing = False
        self.setObjectName("tabView")
        self.setDocumentMode(_USE_DOCUMENT_MODE_TABS)
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setUsesScrollButtons(True)
        self.tabCloseRequested.connect(self._close_requested)
        self.currentChanged.connect(self._current_changed)
        self.tabBar().tabMoved.connect(self._tab_moved)

    # ------------------------------------------------------------ mapping --
    def view_for(self, tab_id: TabId) -> ContentView | None:
        return self._views.get(tab_id)

    def view_ids(self) -> tuple[TabId, ...]:
        """Tab ids that currently own a content view (for page-store cleanup)."""
        return tuple(self._views)

    def current_view(self) -> ContentView | None:
        tab = self._model.active
        return None if tab is None else self._views.get(tab.tab_id)

    # -------------------------------------------------------------- sync --
    def sync(self) -> None:
        """Rebuild the visible tab bar from the model (cheap: tabs are few)."""
        if self._syncing:
            return
        self._syncing = True
        try:
            desired = [tab.tab_id for tab in self._model.tabs]
            current = [
                widget.property("tab_id") for i in range(self.count()) if (widget := self.widget(i))
            ]
            for tab_id in [t for t in current if t not in desired]:
                self._remove(tab_id)
            for tab in self._model.tabs:
                if tab.tab_id not in self._views:
                    self._add(tab)
                self.setTabText(self._index_of(tab.tab_id), tab.title)
                status = "changed on disk" if tab.stale else tab.document.title
                self.setTabToolTip(self._index_of(tab.tab_id), f"{tab.document.path}\n{status}")
            self._reorder(desired)
            active = self._model.active
            target = -1 if active is None else self._index_of(active.tab_id)
            if self.currentIndex() != target:
                self.setCurrentIndex(target)
            self.setVisible(self._model.count > 0)
        finally:
            self._syncing = False

    def _add(self, tab: Tab) -> None:
        view = self._tab_factory()
        view.setProperty("tab_id", tab.tab_id)
        index = self.addTab(view, tab.title)
        self.setTabToolTip(index, str(tab.document.path))
        self._views[tab.tab_id] = view

    def _remove(self, tab_id: TabId) -> None:
        view = self._views.pop(tab_id, None)
        if view is not None:
            index = self.indexOf(view)
            if index >= 0:
                self.removeTab(index)
            view.deleteLater()

    def _index_of(self, tab_id: TabId) -> int:
        view = self._views.get(tab_id)
        return -1 if view is None else self.indexOf(view)

    def _reorder(self, desired: list[TabId]) -> None:
        for target_index, tab_id in enumerate(desired):
            current_index = self._index_of(tab_id)
            if current_index >= 0 and current_index != target_index:
                self.tabBar().moveTab(current_index, target_index)

    # ---------------------------------------------------------- signals --
    def _close_requested(self, index: int) -> None:
        widget = self.widget(index)
        if widget is None:
            return
        tab_id = widget.property("tab_id")
        if isinstance(tab_id, int):
            self._model.close(tab_id)

    def _current_changed(self, index: int) -> None:
        if self._syncing or index < 0:
            return
        widget = self.widget(index)
        tab_id = None if widget is None else widget.property("tab_id")
        if isinstance(tab_id, int):
            self._model.activate(tab_id)

    def _tab_moved(self, from_index: int, to_index: int) -> None:
        if self._syncing:
            return
        order: list[TabId] = []
        for i in range(self.count()):
            widget = self.widget(i)
            tab_id = None if widget is None else widget.property("tab_id")
            if isinstance(tab_id, int):
                order.append(tab_id)
        self._model.reorder_to(order)
