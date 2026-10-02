"""The part: its default geometry and its features in order (V2), above the sketch browser.

"Default geometry" lists the origin and the Top, Front, and Right planes (ADR 0015): pick a
plane to sketch on it, or double-click it to start one there. A sketch row shows its plane
and whether it's open for editing; double-click it to edit it. An extrude row shows its depth
and what it does, and a failing one says why in the error colour. Clicking a feature selects
it, so Properties edits it and Delete deletes it. The panel's heading carries the volume.

It reads `Document.features` and the queries (`feature_error`, `solid_properties`), the same
contract the AI uses; the panel holds no state of its own.
"""

from collections.abc import Callable

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from caliper.app import theme
from caliper.app.properties import format_number
from caliper.app.session import DocumentSession
from caliper.app.tokens import SPACE
from caliper.app.viewport.scene3d import PLANE_NAMES, PLANES
from caliper.contracts.document import (
    EntityId,
    Extrude,
    ExtrudeOperation,
    PartFeature,
    Plane,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode

ID_ROLE = Qt.ItemDataRole.UserRole


def volume_text(session: DocumentSession) -> str:
    """The part's volume, for a heading: "60,000 mm³", or why there's none."""
    if not any(isinstance(f, Extrude) for f in session.document.features):
        return "No solid yet"
    found = session.queries.solid_properties()
    if isinstance(found, Error):
        return "Needs the occt extra" if found.code is ErrorCode.KERNEL_UNAVAILABLE else "Failing"
    return f"{grouped(found.volume)} mm³"


def grouped(value: float) -> str:
    """60000.0 as "60,000", 5685.8407 as "5,685.841": a volume, read at a glance."""
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def titles(features: tuple[PartFeature, ...]) -> dict[EntityId, str]:
    """ "Sketch 1", "Extrude 2": each feature's kind and its number among that kind."""
    counts: dict[str, int] = {}
    found = {}
    for feature in features:
        counts[feature.kind] = counts.get(feature.kind, 0) + 1
        found[feature.id] = f"{feature.kind.title()} {counts[feature.kind]}"
    return found


class FeatureTree(QWidget):
    edit_requested = Signal(object)
    """A sketch's id the user wants to edit."""
    sketch_requested = Signal(object)
    """A `Plane` the user wants to start a sketch on."""

    def __init__(self, session: DocumentSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("part-panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        heading = QWidget()
        heading.setObjectName("part-heading")
        row = QHBoxLayout(heading)
        row.setContentsMargins(SPACE.m, SPACE.xs, SPACE.m, SPACE.xs)
        title = QLabel("Part")
        title.setProperty("role", "section")
        self.volume = QLabel()
        self.volume.setObjectName("part-volume")
        self.volume.setToolTip("The volume of the part's solid, after its last feature")
        row.addWidget(title)
        row.addStretch(1)
        row.addWidget(self.volume)
        layout.addWidget(heading)

        self.tree = QTreeWidget()
        self.tree.setObjectName("feature-tree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(SPACE.l)
        self.tree.setUniformRowHeights(True)
        self.tree.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

        self.items: dict[EntityId, QTreeWidgetItem] = {}
        self.planes: dict[Plane, QTreeWidgetItem] = {}
        self._stale = True
        self.editing: Callable[[], EntityId | None] = lambda: session.active_sketch
        """The sketch open for editing, marked in its row: the window says which."""
        self.tree.itemSelectionChanged.connect(self._push_selection)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        session.document_changed.connect(self.rebuild)
        session.document_replaced.connect(self.rebuild)
        session.active_sketch_changed.connect(self.rebuild)
        session.selection_changed.connect(self._pull_selection)
        self.rebuild()

    def rebuild(self) -> None:
        if not self.isVisible():
            self._stale = True  # hidden in the 2D tab: rebuilt when it shows
            return
        self._stale = False
        blocker = QSignalBlocker(self.tree)
        self.tree.clear()
        self.items = {}
        self.planes = {}
        defaults = QTreeWidgetItem(["Default geometry", ""])
        defaults.setFlags(Qt.ItemFlag.ItemIsEnabled)
        defaults.setForeground(0, theme.TEXT_DIM)
        origin = QTreeWidgetItem(["Origin", ""])
        origin.setFlags(Qt.ItemFlag.ItemIsEnabled)
        defaults.addChild(origin)
        for plane in PLANES:
            item = QTreeWidgetItem([PLANE_NAMES[plane], plane.value.upper()])
            item.setData(0, ID_ROLE, plane)
            item.setForeground(1, theme.TEXT_DIM)
            item.setToolTip(0, f"The {PLANE_NAMES[plane]} plane: double-click to sketch on it")
            defaults.addChild(item)
            self.planes[plane] = item
        self.tree.addTopLevelItem(defaults)
        defaults.setExpanded(True)
        document = self.session.document
        named = titles(document.features)
        for feature in document.features:
            item = QTreeWidgetItem([named[feature.id], ""])
            item.setData(0, ID_ROLE, feature.id)
            self._fill(item, feature)
            self.tree.addTopLevelItem(item)
            self.items[feature.id] = item
        self.volume.setText(volume_text(self.session))
        del blocker
        self._pull_selection()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._stale:
            self.rebuild()

    def _fill(self, item: QTreeWidgetItem, feature: PartFeature) -> None:
        item.setToolTip(0, feature.id)
        match feature:
            case Sketch(plane=plane):
                editing = feature.id == self.editing()
                item.setText(1, f"{PLANE_NAMES[plane]}{'  ·  editing' if editing else ''}")
                font = item.font(0)
                font.setBold(editing)
                item.setFont(0, font)
                item.setForeground(1, theme.ACCENT if editing else theme.TEXT_DIM)
                item.setToolTip(0, f"{feature.id} on {PLANE_NAMES[plane]}: double-click to edit")
            case Extrude(depth=depth, operation=operation):
                verb = "adds" if operation is ExtrudeOperation.ADD else "cuts"
                item.setText(1, f"{verb} {format_number(depth)} mm")
                item.setForeground(1, theme.TEXT_DIM)
                error = self.session.queries.feature_error(feature.id)
                if error is not None:
                    item.setForeground(0, theme.ERROR)
                    item.setForeground(1, theme.ERROR)
                    item.setToolTip(0, f"{feature.id} fails: {error.message}")
                else:
                    item.setToolTip(
                        0, f"{feature.id}: {operation.value}s {feature.sketch}'s profile"
                    )

    def _pull_selection(self) -> None:
        blocker = QSignalBlocker(self.tree)
        selected = self.session.selection
        for id, item in self.items.items():
            item.setSelected(id in selected)
        for plane, item in self.planes.items():
            item.setSelected(plane is self.session.picked_plane)
        del blocker

    def _push_selection(self) -> None:
        # Plane rows are told apart by the row itself: Qt keeps a Plane's data as a plain str.
        planes = [plane for plane, item in self.planes.items() if item.isSelected()]
        if planes:
            self.session.set_picked_plane(planes[0])
            return
        ids = frozenset(id for id, item in self.items.items() if item.isSelected())
        if ids:
            self.session.set_selection(ids)

    def _double_clicked(self, item: QTreeWidgetItem) -> None:
        plane = next((p for p, row in self.planes.items() if row is item), None)
        id = item.data(0, ID_ROLE)
        if plane is not None:
            self.sketch_requested.emit(plane)
        elif any(isinstance(f, Sketch) and f.id == id for f in self.session.document.features):
            self.edit_requested.emit(id)
