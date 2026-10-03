"""The Extrude panel (V2): the sketch being edited, swept along its plane's normal.

Compact and in the window, over the top left of the view, not a dialog of its own: the
keyboard stays in the window, and the sketch stays in sight. It shows the sketch it reads,
which of its geometry (all of it, or what's selected), a depth, and whether it adds to the
part's solid or cuts it away. Return or Extrude sends one `CreateExtrude`; Escape or Cancel
closes it. A refusal (an open profile, say) shows under the fields and leaves the panel open,
so the depth typed isn't lost.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from caliper.app.panels.features import place_name, titles
from caliper.app.properties import display_number, parse_number
from caliper.app.session import DocumentSession
from caliper.app.tokens import SPACE
from caliper.contracts.commands import Applied, CreateExtrude
from caliper.contracts.document import EntityId, ExtrudeOperation, Geometry, Sketch

DEFAULT_DEPTH = 10.0


class ExtrudeForm(QFrame):
    extruded = Signal(object)
    """The new extrude's id."""
    closed = Signal()

    def __init__(self, session: DocumentSession, parent: QWidget) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("extrude-form")
        sketch = session.active_sketch
        document = session.document
        plane = next(
            (f.plane for f in document.features if isinstance(f, Sketch) and f.id == sketch), None
        )
        self.profile: tuple[EntityId, ...] = tuple(
            sorted(
                id for id in session.selection if isinstance(document.entities.get(id), Geometry)
            )
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.l, SPACE.m, SPACE.l, SPACE.m)
        layout.setSpacing(SPACE.s)
        title = QLabel("Extrude")
        title.setObjectName("extrude-title")
        layout.addWidget(title)
        form = QFormLayout()
        form.setHorizontalSpacing(SPACE.l)
        form.setVerticalSpacing(SPACE.s)
        named = titles(document.features).get(sketch, "") if sketch is not None else ""
        where = (
            f"{named} ({sketch}), {place_name(document, plane)}" if plane is not None else "None"
        )
        form.addRow("Sketch", QLabel(where))
        form.addRow(
            "Profile",
            QLabel(f"{len(self.profile)} selected" if self.profile else "All of its geometry"),
        )
        self.depth = QLineEdit(display_number(DEFAULT_DEPTH))
        self.depth.setObjectName("extrude-depth")
        self.depth.returnPressed.connect(self.submit)
        form.addRow("Depth (mm)", self.depth)
        self.operation = QComboBox()
        self.operation.setObjectName("extrude-operation")
        self.operation.addItem("Add to the solid", ExtrudeOperation.ADD)
        self.operation.addItem("Cut from the solid", ExtrudeOperation.REMOVE)
        form.addRow("Operation", self.operation)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setObjectName("extrude-error")
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.close_form)
        self.confirm = QPushButton("Extrude")
        self.confirm.setObjectName("extrude-confirm")
        self.confirm.setDefault(True)
        self.confirm.clicked.connect(self.submit)
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.confirm)
        layout.addLayout(buttons)
        self.setFixedWidth(300)

    def open(self) -> None:
        self.adjustSize()
        self.move(SPACE.l, SPACE.xl * 2)
        self.show()
        self.raise_()
        self.depth.setFocus()
        self.depth.selectAll()

    def submit(self) -> None:
        depth = parse_number(self.depth.text())
        if depth is None:
            self._fail("Type a depth in mm")
            return
        command = CreateExtrude(
            depth=depth,
            operation=self.operation.currentData(),
            ids=self.profile,
        )
        result = self.session.execute(command)
        if isinstance(result, Applied):
            self.close_form()
            self.extruded.emit(result.created_ids[0])
        else:
            self._fail("; ".join(e.message for e in result.errors))

    def close_form(self) -> None:
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close_form()
            return
        super().keyPressEvent(event)

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()
        self.adjustSize()
