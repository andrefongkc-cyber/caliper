"""Sketch browser: every entity in the document, grouped, kept in sync with the selection.

Click selects (Cmd or Shift adds), double-click frames the entity on the canvas.
"""

import bisect

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from caliper.app import icons, theme
from caliper.app.panels.describe import ICON, kind_title, summary
from caliper.app.session import DocumentSession
from caliper.contracts.commands import Change
from caliper.contracts.document import (
    AngleDimension,
    Constraint,
    DistanceDimension,
    Entity,
    EntityId,
    RadialDimension,
)
from caliper.contracts.queries import Queries

GROUPS = ("Geometry", "Dimensions", "Constraints")
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
        # Sized by hand from the widths of the values shown: ResizeToContents re-measures every
        # row whenever one row's text changes, which dominated an edit's cost at 2,000 entities.
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.items: dict[EntityId, QTreeWidgetItem] = {}
        self._value_widths: dict[str, int] = {}
        """Text width of each row's value (and each group's count), keyed by id or group name."""
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
        session.changed.connect(self._apply)
        session.document_replaced.connect(self.rebuild)
        session.selection_changed.connect(self._pull_selection)
        self.rebuild()

    def rebuild(self) -> None:
        blocker = QSignalBlocker(self)
        for group in self.groups.values():
            group.takeChildren()
        self.items = {}
        self._value_widths = {}
        document = self.session.document
        for id in sorted(document.entities, key=_natural):
            self._insert(id)
        self._update_groups()
        del blocker
        self._pull_selection()

    def _apply(self, change: Change) -> None:
        """Update only what the change touched, and the dimensions measuring it."""
        blocker = QSignalBlocker(self)
        delta, document = change.delta, self.session.document
        for id in delta.removed | {i for i in delta.modified if i not in document.entities}:
            item = self.items.pop(id, None)
            self._value_widths.pop(id, None)
            if item is not None and item.parent() is not None:
                item.parent().removeChild(item)
        for id in sorted(delta.added & document.entities.keys(), key=_natural):
            self._insert(id)
        queries = self.session.queries
        changed = delta.modified | delta.removed
        for id in delta.modified & document.entities.keys():
            self._fill(self.items[id], id, queries)
        if changed:
            for id, entity in document.entities.items():
                if id not in delta.modified and id in self.items and _measures(entity, changed):
                    self._fill(self.items[id], id, queries)
        self._update_groups()
        del blocker
        self._pull_selection()

    def _insert(self, id: EntityId) -> None:
        entity = self.session.document.entities[id]
        group = self.groups[_group(entity)]
        item = QTreeWidgetItem()
        item.setData(0, ID_ROLE, id)
        item.setForeground(1, theme.TEXT_DIM)
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._fill(item, id, self.session.queries)
        keys = [_natural(group.child(i).data(0, ID_ROLE)) for i in range(group.childCount())]
        group.insertChild(bisect.bisect(keys, _natural(id)), item)
        self.items[id] = item

    def _fill(self, item: QTreeWidgetItem, id: EntityId, queries: Queries) -> None:
        entity = self.session.document.entities[id]
        value = summary(entity, id, queries)
        item.setText(0, f"{kind_title(entity)}  {id}")
        item.setText(1, value)
        item.setIcon(0, icons.icon(ICON[entity.kind]))
        construction = getattr(entity, "construction", False)
        font = item.font(0)
        if font.italic() != construction:
            # Construction geometry reads in italics, and its tooltip says why.
            font.setItalic(construction)
            item.setFont(0, font)
            item.setToolTip(
                0, "Construction geometry: constrained, never a profile" if construction else ""
            )
        self._value_widths[id] = self.fontMetrics().horizontalAdvance(value)

    def _update_groups(self) -> None:
        for name, group in self.groups.items():
            count = str(group.childCount()) if group.childCount() else ""
            group.setText(1, count)
            group.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            group.setHidden(group.childCount() == 0)
            self._value_widths[name] = self.fontMetrics().horizontalAdvance(count)
        self._fit_value_column()

    def _fit_value_column(self) -> None:
        margin = 2 * (
            self.style().pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, self) + 1
        )
        width = max(self._value_widths.values(), default=0) + margin
        if self.header().sectionSize(1) != width:
            self.header().resizeSection(1, width)

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


def _group(entity: Entity) -> str:
    match entity:
        case DistanceDimension() | RadialDimension() | AngleDimension():
            return "Dimensions"
        case Constraint():
            return "Constraints"
    return "Geometry"


def _measures(entity: Entity, ids: frozenset[EntityId]) -> bool:
    """True if `entity` is a dimension whose value depends on one of `ids`."""
    match entity:
        case DistanceDimension(a=a, b=b) | AngleDimension(a=a, b=b):
            return a.entity in ids or b.entity in ids
        case RadialDimension(target=target):
            return target in ids
    return False
