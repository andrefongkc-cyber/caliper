"""Heads-up numeric entry: type exact values next to the pointer while drawing.

Opens when a digit, "-", or "." is typed while a tool that accepts values is busy. Tab and
Shift+Tab move between fields, every keystroke updates the preview, Return commits, and Esc
closes the entry (a second Esc cancels the operation, as usual). Fields left empty follow the
pointer.
"""

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QWidget

from caliper.app import theme
from caliper.app.properties import parse_number
from caliper.app.tokens import SPACE

STARTS_ENTRY = frozenset("0123456789.-")
OFFSET_PX = 18
FIELD_WIDTH = 64


class NumericEntry(QFrame):
    changed = Signal(tuple)
    """Values as typed so far: a tuple of float or None per field."""
    committed = Signal(tuple)
    closed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("numeric-entry")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            f"#numeric-entry {{ background: {theme.PANEL.name()};"
            f" border: 1px solid {theme.ACCENT.name()}; border-radius: 3px; }}"
            f" #numeric-entry QLabel {{ color: {theme.TEXT_DIM.name()}; }}"
        )
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(SPACE.s, SPACE.xs, SPACE.s, SPACE.xs)
        self._layout.setSpacing(SPACE.s)
        self.fields: list[QLineEdit] = []
        self.hide()

    # --- Opening and closing --------------------------------------------------------------

    def open(self, labels: Sequence[str], first_text: str, at: QPoint) -> None:
        self._clear()
        for label in labels:
            name = QLabel(label)
            name.setFont(theme.font(size=11))
            edit = QLineEdit()
            edit.setObjectName(label.lower())
            edit.setFixedWidth(FIELD_WIDTH)
            edit.setAlignment(Qt.AlignmentFlag.AlignRight)
            edit.textEdited.connect(self._emit_changed)
            edit.installEventFilter(self)
            self._layout.addWidget(name)
            self._layout.addWidget(edit)
            self.fields.append(edit)
        self.adjustSize()
        self.move_near(at)
        self.show()
        self.raise_()
        first = self.fields[0]
        first.setFocus()
        first.setText(first_text)
        self._emit_changed()

    def move_near(self, at: QPoint) -> None:
        """Sit below-right of the pointer, flipped to stay inside the canvas."""
        parent = self.parentWidget()
        x, y = at.x() + OFFSET_PX, at.y() + OFFSET_PX
        if parent is not None:
            if x + self.width() > parent.width():
                x = at.x() - OFFSET_PX - self.width()
            if y + self.height() > parent.height():
                y = at.y() - OFFSET_PX - self.height()
        self.move(max(0, x), max(0, y))

    def close_entry(self) -> None:
        if self.isVisible():
            self.hide()
            self._clear()
            self.closed.emit()

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.removeEventFilter(self)
                widget.deleteLater()
        self.fields = []

    # --- Values ---------------------------------------------------------------------------

    def values(self) -> tuple[float | None, ...]:
        return tuple(parse_number(f.text()) if f.text().strip() else None for f in self.fields)

    def _emit_changed(self) -> None:
        for field in self.fields:
            bad = bool(field.text().strip()) and parse_number(field.text()) is None
            field.setProperty("invalid", bad)
            field.style().unpolish(field)
            field.style().polish(field)
        self.changed.emit(self.values())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not (isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress):
            return False
        if watched not in self.fields:
            return False
        index = self.fields.index(watched)  # type: ignore[arg-type]
        key = event.key()
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            step = -1 if key == Qt.Key.Key_Backtab else 1
            target = self.fields[(index + step) % len(self.fields)]
            target.setFocus()
            target.selectAll()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.committed.emit(self.values())
            return True
        if key == Qt.Key.Key_Escape:
            self.close_entry()
            return True
        return False
