"""Properties panel: edit the inputs of the selected entity.

Fields come from the entity's contract dataclass, so a new entity field shows up without
changes here. An edit commits on Return or when the field loses focus, and becomes one
`ModifyEntity`, which is one undo step. Continuous scrubbing would need `merge_key`; the
panel has no scrubbing yet. A `Rejected` result marks the field its `Error.field` names.
"""

import dataclasses
import math
from enum import StrEnum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from caliper.app.session import DocumentSession
from caliper.contracts.commands import Applied, ModifyEntity, ParamValue, Rejected
from caliper.contracts.document import Entity, EntityId, Point2, Ref


def format_number(value: float) -> str:
    """Shortest text that reads back as the same float; whole numbers drop the '.0'."""
    text = repr(value)
    return text[:-2] if text.endswith(".0") else text


def parse_number(text: str) -> float | None:
    try:
        value = float(text.strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _title(name: str) -> str:
    return name.replace("_", " ").capitalize()


class PropertiesPanel(QWidget):
    def __init__(self, session: DocumentSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.fields: dict[str, QLineEdit | QComboBox] = {}
        """Editable fields by path: "width", "corner.x", "orientation"."""
        self._entity_id: EntityId | None = None
        # The panel's width must not depend on what's selected: a dock that grows when a
        # selection appears shrinks the canvas and shifts the view under the user.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setAutoFillBackground(True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(6)
        self._body = QWidget()
        self._layout.addWidget(self._body)
        self._layout.addStretch(1)
        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        self._layout.addWidget(self.error)
        session.selection_changed.connect(self.rebuild)
        session.document_changed.connect(self.refresh)
        self.rebuild()

    @property
    def entity_id(self) -> EntityId | None:
        return self._entity_id

    # --- Building -------------------------------------------------------------------------

    def rebuild(self) -> None:
        # A focused field emits editingFinished as it's torn down; it must not commit to
        # whatever entity is selected next.
        for widget in self.fields.values():
            widget.blockSignals(True)
        self._body.hide()
        self._body.deleteLater()
        self._body = QWidget()
        self._layout.insertWidget(0, self._body)
        self.fields = {}
        self._clear_error()
        selection = self.session.selection
        entity = None
        if len(selection) == 1:
            (self._entity_id,) = selection
            entity = self.session.document.entities.get(self._entity_id)
        else:
            self._entity_id = None
        if entity is None:
            layout = QVBoxLayout(self._body)
            layout.setContentsMargins(0, 0, 0, 0)
            text = f"{len(selection)} entities selected" if selection else "Nothing selected"
            label = QLabel(text)
            label.setProperty("role", "section")
            layout.addWidget(label)
            return
        form = QFormLayout(self._body)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(5)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heading = QLabel(f"{_title(entity.kind)}  ·  {self._entity_id}")
        heading.setProperty("role", "section")
        form.addRow(heading)
        for field in dataclasses.fields(entity):
            self._add_field(form, field.name, getattr(entity, field.name))

    def _add_field(self, form: QFormLayout, name: str, value: object) -> None:
        match value:
            case bool():
                form.addRow(_title(name), QLabel(str(value)))
            case float() | int():
                form.addRow(_title(name), self._number(name, float(value)))
            case Point2():
                row = QHBoxLayout()
                row.setSpacing(4)
                for axis in ("x", "y"):
                    row.addWidget(QLabel(axis.upper()))
                    row.addWidget(self._number(f"{name}.{axis}", getattr(value, axis)), 1)
                form.addRow(_title(name), row)
            case StrEnum():
                combo = QComboBox()
                combo.addItems([member.value for member in type(value)])
                combo.setCurrentText(value.value)
                enum = type(value)
                combo.currentTextChanged.connect(
                    lambda text, n=name, e=enum: self._commit(n, e(text))
                )
                self.fields[name] = combo
                form.addRow(_title(name), combo)
            case Ref(entity=target, feature=feature):
                form.addRow(_title(name), QLabel(f"{target} {feature.value.replace('_', ' ')}"))
            case _:
                form.addRow(_title(name), QLabel(str(value)))

    def _number(self, path: str, value: float) -> QLineEdit:
        edit = QLineEdit(format_number(value))
        edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        edit.setMinimumWidth(40)
        edit.setObjectName(path)
        edit.editingFinished.connect(lambda p=path, e=edit: self._commit_text(p, e))
        self.fields[path] = edit
        return edit

    # --- Refreshing -----------------------------------------------------------------------

    def refresh(self) -> None:
        """Show the entity's current values after undo, redo, or another tool changed it."""
        if self._entity_id is None:
            return
        entity = self.session.document.entities.get(self._entity_id)
        if entity is None:
            self.rebuild()
            return
        for path, widget in self.fields.items():
            current = _read(entity, path)
            if isinstance(widget, QLineEdit) and not widget.hasFocus():
                widget.setText(format_number(float(current)))  # type: ignore[arg-type]
            elif isinstance(widget, QComboBox) and isinstance(current, StrEnum):
                widget.blockSignals(True)
                widget.setCurrentText(current.value)
                widget.blockSignals(False)

    # --- Committing -----------------------------------------------------------------------

    def _commit_text(self, path: str, edit: QLineEdit) -> None:
        entity = self._entity()
        if entity is None:
            return
        value = parse_number(edit.text())
        if value is None:
            self._show_error(path, "Enter a number")
            return
        if value == _read(entity, path):
            self._clear_error()
            edit.setText(format_number(value))
            return
        name, _, axis = path.partition(".")
        change: ParamValue = value
        if axis:
            point = getattr(entity, name)
            change = dataclasses.replace(point, **{axis: value})
        self._commit(name, change, path)

    def _commit(self, name: str, value: ParamValue, path: str | None = None) -> None:
        if self._entity_id is None:
            return
        result = self.session.execute(ModifyEntity(id=self._entity_id, changes={name: value}))
        match result:
            case Applied():
                self._clear_error()
                self.refresh()
            case Rejected(errors=errors):
                error = errors[0]
                self._show_error(error.field or path or name, error.message)
            case _:
                pass

    def _entity(self) -> Entity | None:
        if self._entity_id is None:
            return None
        return self.session.document.entities.get(self._entity_id)

    def _show_error(self, field: str, message: str) -> None:
        self._clear_error()
        for path, widget in self.fields.items():
            if path == field or path.startswith(field + "."):
                widget.setProperty("invalid", True)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        self.error.setText(message)
        self.error.show()

    def _clear_error(self) -> None:
        for widget in self.fields.values():
            if widget.property("invalid"):
                widget.setProperty("invalid", False)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        self.error.hide()


def _read(entity: Entity, path: str) -> object:
    value: object = entity
    for part in path.split("."):
        value = getattr(value, part)
    return value
