"""Sketch browser: every entity in the document, grouped, kept in sync with the selection.

Click selects (Cmd or Shift adds), double-click frames the entity on the canvas.
"""

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from caliper.app import icons, theme
from caliper.app.panels.describe import ICON, kind_title, summary
from caliper.app.session import DocumentSession
from caliper.contracts.document import DistanceDimension, EntityId, RadialDimension

GROUPS = ("Geometry", "Dimensions")
ID_ROLE = Qt.ItemDataRole.UserRole


class SketchBrowser(QTreeWidget):
    frame_requested = Signal(object)
    """An EntityId the user wants to see on the canvas."""

    def __init__(self, session: DocumentSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("sketch-browser")
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setIndentation(12)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.items: dict[EntityId, QTreeWidgetItem] = {}
        self.groups: dict[str, QTreeWidgetItem] = {}
        for name in GROUPS:
            group = QTreeWidgetItem([name.upper(), ""])
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            group.setFont(0, theme.font(size=11, bold=True))
            group.setForeground(0, theme.TEXT_DIM)
            self.addTopLevelItem(group)
            group.setExpanded(True)
            self.groups[name] = group
        self.itemSelectionChanged.connect(self._push_selection)
        self.itemDoubleClicked.connect(self._frame)
        session.document_changed.connect(self.rebuild)
        session.selection_changed.connect(self._pull_selection)
        session.hover_changed.connect(self.viewport().update)
        self.rebuild()

    def rebuild(self) -> None:
        blocker = QSignalBlocker(self)
        document, queries = self.session.document, self.session.queries
        for group in self.groups.values():
            group.takeChildren()
        self.items = {}
        for id in sorted(document.entities, key=_natural):
            entity = document.entities[id]
            group = (
                "Dimensions"
                if isinstance(entity, DistanceDimension | RadialDimension)
                else "Geometry"
            )
            item = QTreeWidgetItem([f"{kind_title(entity)}  {id}", summary(entity, id, queries)])
            item.setData(0, ID_ROLE, id)
            item.setIcon(0, icons.icon(ICON[entity.kind]))
            item.setForeground(1, theme.TEXT_DIM)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.groups[group].addChild(item)
            self.items[id] = item
        for group in self.groups.values():
            group.setText(1, str(group.childCount()) if group.childCount() else "")
            group.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            group.setHidden(group.childCount() == 0)
        del blocker
        self._pull_selection()

    def _pull_selection(self) -> None:
        blocker = QSignalBlocker(self)
        selected = self.session.selection
        for id, item in self.items.items():
            item.setSelected(id in selected)
        if len(selected) == 1:
            (only,) = selected
            if only in self.items:
                self.scrollToItem(self.items[only])
        del blocker
        self.viewport().update()

    def _push_selection(self) -> None:
        ids = frozenset(
            item.data(0, ID_ROLE) for item in self.selectedItems() if item.data(0, ID_ROLE)
        )
        self.session.set_selection(ids)

    def _frame(self, item: QTreeWidgetItem) -> None:
        id = item.data(0, ID_ROLE)
        if id:
            self.frame_requested.emit(id)


def _natural(id: str) -> tuple[str, int, str]:
    """e2 before e10."""
    head = id.rstrip("0123456789")
    digits = id[len(head) :]
    return (head, int(digits) if digits else -1, id)
