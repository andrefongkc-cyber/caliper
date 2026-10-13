"""The Extrude panel (V2): a sketch swept along its plane's normal, made or edited.

Compact and in the window, over the top left of the view, not a dialog of its own: the
keyboard stays in the window, and the part stays in sight. It shows the sketch it reads,
which of its geometry (all of it, or what's selected), a depth, whether it adds to the part's
solid or cuts it away, and which way it goes. Opened on an extrude the part has, it edits it.

The solid the fields would make is seen as they change (`previewed`): the command is tried on
a bus of its own over the session's document, so nothing is changed and nothing can be undone
by it, and the 3D view draws what that leaves. A refusal (an open profile, say) shows under
the fields at once and leaves the panel open, so the depth typed isn't lost. Return or the
button sends one command: `CreateExtrude`, or for an edit one `ModifyEntity` of what changed.
Escape or Cancel closes it.
"""

from PySide6.QtCore import Qt, QTimer, Signal
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

from caliper.app.part_names import (
    DIRECTIONS,
    OPERATIONS,
    feature_text,
    place_name,
    profile_text,
    titles,
)
from caliper.app.properties import display_number, parse_number
from caliper.app.session import DocumentSession
from caliper.app.tokens import SPACE
from caliper.contracts.commands import Applied, Command, CreateExtrude, ModifyEntity
from caliper.contracts.document import EntityId, Extrude, Geometry, Sketch
from caliper.engine import faces
from caliper.engine.commands.bus import Bus

DEFAULT_DEPTH = 10.0
PREVIEW_MS = 120
"""How long the fields rest before the solid they'd make is worked out: typing "125" is one
solid, not three."""


class ExtrudeForm(QFrame):
    extruded = Signal(object)
    """The id of the extrude made, or edited."""
    closed = Signal()
    previewed = Signal(object)
    """The part as the fields would leave it (a `Document`), for the 3D view to draw its solid;
    None when they'd be refused, or the panel closed."""

    def __init__(
        self, session: DocumentSession, parent: QWidget, editing: Extrude | None = None
    ) -> None:
        """`editing` is an extrude of the part to change; without one, a new extrude of the
        sketch being edited is made."""
        super().__init__(parent)
        self.session = session
        self.editing = editing.id if editing is not None else None
        self.setObjectName("extrude-form")
        document = session.document
        sketch = editing.sketch if editing is not None else session.active_sketch
        plane = next(
            (f.plane for f in document.features if isinstance(f, Sketch) and f.id == sketch), None
        )
        self.profile: tuple[EntityId, ...] = (
            editing.ids
            if editing is not None
            else tuple(
                sorted(
                    id
                    for id in session.selection
                    if isinstance(document.entities.get(id), Geometry)
                )
            )
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.l, SPACE.m, SPACE.l, SPACE.m)
        layout.setSpacing(SPACE.s)
        named = titles(document.features)
        title = QLabel("Extrude" if editing is None else f"Edit {named[editing.id]}")
        title.setObjectName("extrude-title")
        layout.addWidget(title)
        form = QFormLayout()
        form.setHorizontalSpacing(SPACE.l)
        form.setVerticalSpacing(SPACE.s)
        where = (
            f"{feature_text(document, sketch)}, {place_name(document, plane)}"
            if sketch is not None and plane is not None
            else "None"
        )
        form.addRow("Sketch", QLabel(where))
        form.addRow("Profile", QLabel(profile_text(self.profile)))
        depth = editing.depth if editing is not None else DEFAULT_DEPTH
        self.depth = QLineEdit(display_number(depth))
        self.depth.setObjectName("extrude-depth")
        self.depth.returnPressed.connect(self.submit)
        form.addRow("Depth (mm)", self.depth)
        self.operation = QComboBox()
        self.operation.setObjectName("extrude-operation")
        for operation, text in OPERATIONS.items():
            self.operation.addItem(text, operation)
        form.addRow("Operation", self.operation)
        self.direction = QComboBox()
        self.direction.setObjectName("extrude-direction")
        for reversed_, text in DIRECTIONS.items():
            self.direction.addItem(text, reversed_)
        self.direction.setToolTip(
            "Which way it goes from the sketch: on a face, the normal points out of the part, "
            "so a cut goes against it, into the part"
        )
        form.addRow("Direction", self.direction)
        self._sketch = sketch
        if editing is None:
            self._default_direction()
        else:  # as it is: the engine's choice is for a new one
            self.operation.setCurrentIndex(self.operation.findData(editing.operation))
            self.direction.setCurrentIndex(1 if editing.reversed else 0)
        self.operation.currentIndexChanged.connect(self._default_direction)
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
        self.confirm = QPushButton("Extrude" if editing is None else "Done")
        self.confirm.setObjectName("extrude-confirm")
        self.confirm.setDefault(True)
        self.confirm.clicked.connect(self.submit)
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.confirm)
        layout.addLayout(buttons)
        self.setFixedWidth(300)

        self._rest = QTimer(self)
        self._rest.setSingleShot(True)
        self._rest.setInterval(PREVIEW_MS)
        self._rest.timeout.connect(self.refresh_preview)
        self.depth.textEdited.connect(self._rest.start)
        self.operation.currentIndexChanged.connect(self._rest.start)
        self.direction.currentIndexChanged.connect(self._rest.start)
        # The part changed under the panel (undo, Claude): what it would make has too.
        session.document_changed.connect(self._rest.start)

    def open(self) -> None:
        self.adjustSize()
        self.move(SPACE.l, SPACE.xl * 2)
        self.show()
        self.raise_()
        self.depth.setFocus()
        self.depth.selectAll()
        self.refresh_preview()

    def command(self) -> Command | str | None:
        """What the fields ask for: the command, why there's none to send (said under the
        fields), or None when an edit changes nothing."""
        depth = parse_number(self.depth.text())
        if depth is None:
            return "Type a depth in mm"
        operation = self.operation.currentData()
        reversed_ = bool(self.direction.currentData())
        if self.editing is None:
            return CreateExtrude(
                depth=depth,
                sketch=self._sketch,
                operation=operation,
                ids=self.profile,
                reversed=reversed_,
            )
        feature = self._edited()
        if feature is None:
            return "The extrude isn't in the part any more"
        wanted = {"depth": depth, "operation": operation, "reversed": reversed_}
        changes = {name: value for name, value in wanted.items() if getattr(feature, name) != value}
        return ModifyEntity(id=self.editing, changes=changes) if changes else None

    def refresh_preview(self) -> None:
        """Try the fields' command on a bus of its own and show what it leaves: the solid in
        the 3D view, or under the fields why it can't be made."""
        if not self.isVisible():
            return
        command = self.command()
        if command is None:
            self._ok()
            self.previewed.emit(None)  # the part as it is
            return
        if isinstance(command, str):
            self._fail(command)
            self.previewed.emit(None)
            return
        trial = Bus(self.session.document)
        result = trial.execute(command)
        if not isinstance(result, Applied):
            self._fail("; ".join(e.message for e in result.errors))
            self.previewed.emit(None)
            return
        made = self.editing if self.editing is not None else result.created_ids[0]
        broken = trial.queries.feature_error(made)
        if broken is not None:  # made, but with no solid to show for it
            self._fail(broken.message)
            self.previewed.emit(None)
            return
        self._ok()
        self.previewed.emit(trial.document)

    def submit(self) -> None:
        command = self.command()
        if command is None:  # an edit that changes nothing
            self.close_form()
            return
        if isinstance(command, str):
            self._fail(command)
            return
        result = self.session.execute(command)
        if isinstance(result, Applied):
            made = self.editing if self.editing is not None else result.created_ids[0]
            self.close_form()
            self.extruded.emit(made)
        else:
            self._fail("; ".join(e.message for e in result.errors))

    def _edited(self) -> Extrude | None:
        return next(
            (
                f
                for f in self.session.document.features
                if isinstance(f, Extrude) and f.id == self.editing
            ),
            None,
        )

    def _default_direction(self) -> None:
        """The way the engine would choose (ADR 0016): a cut from a face goes into the part,
        anything else along the normal. Choosing another is kept until the operation changes."""
        reversed_ = (
            faces.default_reversed(
                self.session.document, self._sketch, self.operation.currentData()
            )
            if self._sketch is not None
            else False
        )
        self.direction.setCurrentIndex(1 if reversed_ else 0)

    def close_form(self) -> None:
        self._rest.stop()
        self.hide()
        self.previewed.emit(None)
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

    def _ok(self) -> None:
        if not self.error.isHidden():
            self.error.hide()
            self.adjustSize()
