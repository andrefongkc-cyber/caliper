"""Checks: requirements for the sketch, re-measured after every change.

Each check is a contract `Expectation` evaluated with `queries.check`, the same call a bench
case or an AI agent makes. Add one from the current selection (a width, a height, an area,
a dimension's value) or from the last Measure. Checks are session state for now: whether
they're saved with the part or beside it is an open decision.
"""

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, Qt
from PySide6.QtGui import QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from caliper.app import theme
from caliper.app.panels.describe import n
from caliper.app.properties import format_number, parse_number, ref_text
from caliper.app.session import DocumentSession
from caliper.app.tokens import SPACE
from caliper.contracts.document import (
    AngleDimension,
    Circle,
    Constraint,
    DistanceDimension,
    EntityId,
    RadialDimension,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import ErrorCode
from caliper.contracts.queries import CheckResult, Expectation, Metric

DEFAULT_TOLERANCE = 0.01
STATUS_ROLE = Qt.ItemDataRole.UserRole
ACTUAL_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass(frozen=True, slots=True)
class Option:
    label: str
    metric: Metric
    ids: tuple[EntityId, ...] = ()
    refs: tuple[Ref, ...] = ()


def describe(e: Expectation) -> str:
    target = ", ".join(e.ids)
    match e.metric:
        case Metric.BBOX_WIDTH:
            subject = f"Width of {target}" if e.ids else "Sketch width"
        case Metric.BBOX_HEIGHT:
            subject = f"Height of {target}" if e.ids else "Sketch height"
        case Metric.AREA:
            subject = f"Area of {target}"
        case Metric.DIMENSION_VALUE:
            subject = f"Dimension {target}"
        case Metric.DISTANCE | Metric.DISTANCE_X | Metric.DISTANCE_Y:
            kind = {
                Metric.DISTANCE: "Distance",
                Metric.DISTANCE_X: "Horizontal distance",
                Metric.DISTANCE_Y: "Vertical distance",
            }[e.metric]
            subject = f"{kind} {ref_text(e.refs[0])} → {ref_text(e.refs[1])}"
    return f"{subject} = {n(e.expected)} ± {format_number(e.tolerance)}"


def options(session: DocumentSession) -> list[Option]:
    """What can be checked, given the selection and the last measurement."""
    entities = session.document.entities
    selected = sorted(session.selection)
    found: list[Option] = []
    if session.last_measurement is not None:
        a, b = session.last_measurement
        if a.entity in entities and b.entity in entities:
            refs = (a, b)
            found += [
                Option("Last measurement: distance", Metric.DISTANCE, refs=refs),
                Option("Last measurement: horizontal", Metric.DISTANCE_X, refs=refs),
                Option("Last measurement: vertical", Metric.DISTANCE_Y, refs=refs),
            ]
    if len(selected) == 1:
        (id,) = selected
        entity = entities.get(id)
        if isinstance(entity, DistanceDimension | RadialDimension | AngleDimension):
            found.append(Option(f"Value of {id}", Metric.DIMENSION_VALUE, ids=(id,)))
        elif entity is not None and not isinstance(entity, Constraint):
            found += [
                Option(f"Width of {id}", Metric.BBOX_WIDTH, ids=(id,)),
                Option(f"Height of {id}", Metric.BBOX_HEIGHT, ids=(id,)),
            ]
            area = Option(f"Area of {id}", Metric.AREA, ids=(id,))
            if isinstance(entity, Rectangle | Circle) and _measurable(session, area):
                found.append(area)
    elif len(selected) > 1:
        ids = tuple(selected)
        found += [
            Option("Width of the selection", Metric.BBOX_WIDTH, ids=ids),
            Option("Height of the selection", Metric.BBOX_HEIGHT, ids=ids),
        ]
    else:
        found += [
            Option("Sketch width", Metric.BBOX_WIDTH),
            Option("Sketch height", Metric.BBOX_HEIGHT),
        ]
    return found


class _Row(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        status = index.data(STATUS_ROLE)
        rect = option.rect.adjusted(SPACE.m, 0, -SPACE.m, 0)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, theme.FIELD)
        painter.save()
        glyph, colour = {"pass": ("✓", theme.PASSED), "fail": ("✗", theme.ERROR)}.get(
            status, ("!", theme.PREVIEW)
        )
        painter.setPen(colour)
        painter.setFont(theme.font(bold=True))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, glyph)
        text_rect = rect.adjusted(SPACE.xl + SPACE.xs, 0, 0, 0)
        actual = index.data(ACTUAL_ROLE) or ""
        painter.setFont(theme.font(size=11, mono=True))
        painter.setPen(colour)
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, actual
        )
        used = painter.fontMetrics().horizontalAdvance(actual) + SPACE.m
        painter.setFont(option.font)
        painter.setPen(theme.TEXT)
        elided = painter.fontMetrics().elidedText(
            index.data(), Qt.TextElideMode.ElideRight, text_rect.width() - used
        )
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided
        )
        painter.restore()


class ChecksPanel(QWidget):
    def __init__(self, session: DocumentSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("checks")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.m, SPACE.s, SPACE.m, SPACE.m)
        layout.setSpacing(SPACE.s)

        header = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setProperty("role", "section")
        self.add_button = QPushButton("Add check")
        self.add_button.clicked.connect(self.open_form)
        header.addWidget(self.summary, 1)
        header.addWidget(self.add_button)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setItemDelegate(_Row(self.list))
        self.list.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.list.installEventFilter(self)
        layout.addWidget(self.list, 1)

        self.empty = QLabel(
            "No checks yet. Select something or measure, then Add check to turn a size into "
            "a requirement that's re-measured after every change."
        )
        self.empty.setWordWrap(True)
        self.empty.setProperty("role", "section")
        layout.addWidget(self.empty)

        self.form = QFrame()
        self.form.setObjectName("check-form")
        form_layout = QFormLayout(self.form)
        form_layout.setContentsMargins(0, SPACE.xs, 0, 0)
        self.metric_box = QComboBox()
        self.metric_box.currentIndexChanged.connect(self._fill_expected)
        self.expected = QLineEdit()
        self.expected.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.tolerance = QLineEdit(format_number(DEFAULT_TOLERANCE))
        self.tolerance.setAlignment(Qt.AlignmentFlag.AlignRight)
        buttons = QHBoxLayout()
        self.confirm = QPushButton("Add")
        self.confirm.setDefault(True)
        self.confirm.clicked.connect(self.submit)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close_form)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.confirm)
        form_layout.addRow("Check", self.metric_box)
        form_layout.addRow("Expected", self.expected)
        form_layout.addRow("Tolerance ±", self.tolerance)
        form_layout.addRow(buttons)
        self.error = QLabel()
        self.error.setProperty("role", "error")
        form_layout.addRow(self.error)
        self.expected.returnPressed.connect(self.submit)
        self.tolerance.returnPressed.connect(self.submit)
        layout.addWidget(self.form)
        self.form.hide()
        self._options: list[Option] = []

        session.document_changed.connect(self.refresh)
        session.checks_changed.connect(self.refresh)
        self.refresh()

    # --- Results --------------------------------------------------------------------------

    def refresh(self) -> None:
        results = self.session.check_results()
        self.list.clear()
        for result in results:
            item = QListWidgetItem(describe(result.expectation))
            item.setData(STATUS_ROLE, _status(result))
            item.setData(ACTUAL_ROLE, _actual(result))
            item.setToolTip(result.error.message if result.error else "")
            self.list.addItem(item)
        passing = sum(r.passed for r in results)
        self.summary.setText(f"{passing} of {len(results)} pass" if results else "Checks")
        self.list.setVisible(bool(results))
        self.empty.setVisible(not results and self.form.isHidden())

    # --- Adding ---------------------------------------------------------------------------

    def open_form(self) -> None:
        self._options = options(self.session)
        self.metric_box.blockSignals(True)
        self.metric_box.clear()
        self.metric_box.addItems([o.label for o in self._options])
        self.metric_box.blockSignals(False)
        self.error.clear()
        self.tolerance.setText(format_number(DEFAULT_TOLERANCE))
        self._fill_expected()
        self.form.show()
        self.empty.hide()
        self.expected.setFocus()
        self.expected.selectAll()

    def close_form(self) -> None:
        self.form.hide()
        self.refresh()

    def _fill_expected(self) -> None:
        option = self._current()
        if option is None:
            return
        probe = self.session.queries.check(_expectation(option, 0.0, 0.0))
        self.expected.setText("" if probe.actual is None else format_number(round(probe.actual, 6)))

    def submit(self) -> None:
        option = self._current()
        expected, tolerance = (
            parse_number(self.expected.text()),
            parse_number(self.tolerance.text()),
        )
        if option is None or expected is None or tolerance is None or tolerance < 0:
            self.error.setText("Enter a number for the expected value and a tolerance of 0 or more")
            return
        self.session.add_check(_expectation(option, expected, tolerance))
        self.close_form()

    def _current(self) -> Option | None:
        index = self.metric_box.currentIndex()
        return self._options[index] if 0 <= index < len(self._options) else None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self.list
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
            and self.list.currentRow() >= 0
        ):
            self.session.remove_check(self.list.currentRow())
            return True
        return False


def _measurable(session: DocumentSession, option: Option) -> bool:
    """False when the engine can't evaluate it here, e.g. area without a geometry kernel."""
    result = session.queries.check(_expectation(option, 0.0, 0.0))
    return result.error is None or result.error.code is not ErrorCode.KERNEL_UNAVAILABLE


def _expectation(option: Option, expected: float, tolerance: float) -> Expectation:
    return Expectation(
        metric=option.metric,
        expected=expected,
        tolerance=tolerance,
        refs=option.refs,
        ids=option.ids,
    )


def _status(result: CheckResult) -> str:
    if result.error is not None:
        return "error"
    return "pass" if result.passed else "fail"


def _actual(result: CheckResult) -> str:
    if result.error is not None:
        return "can't measure"
    assert result.actual is not None
    return n(result.actual)
