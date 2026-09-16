"""Command palette (Cmd+K): find any action or command by name and run it.

Two kinds of entries share one list:
- window actions (tools, File, Edit, View), shown with their shortcut;
- contract commands from `command_schema`, which open a typed parameter form.

Keyboard only: type to filter, Up/Down to move, Return to run, Esc to go back or close.
"""

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QRect, Qt
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from caliper.app import theme
from caliper.app.command_schema import CommandSpec, build, command_specs, matches
from caliper.app.properties import format_number, parse_number
from caliper.app.session import DocumentSession
from caliper.app.tokens import SPACE
from caliper.contracts.commands import Applied, Rejected

WIDTH = 460
LIST_ROWS = 9
TOP_MARGIN = 56


DETAIL_ROLE = Qt.ItemDataRole.UserRole + 1


class _Row(QStyledItemDelegate):
    """Title on the left, shortcut or parameters right-aligned and dimmed."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().paint(painter, option, index)
        detail = index.data(DETAIL_ROLE)
        if not detail:
            return
        painter.save()
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        painter.setPen(theme.TEXT_DIM if enabled else theme.BORDER)
        rect: QRect = option.rect.adjusted(0, 0, -SPACE.m, 0)
        painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, detail)
        painter.restore()


def rank(query: str, title: str) -> int:
    """0 if the title starts with the query, 1 if a word does, 2 for any other match."""
    q, t = query.lower().strip(), title.lower()
    if not q or t.startswith(q):
        return 0
    return 1 if any(word.startswith(q.split()[0]) for word in t.split()) else 2


@dataclass(frozen=True, slots=True)
class Entry:
    title: str
    detail: str
    action: QAction | None = None
    spec: CommandSpec | None = None


class CommandPalette(QFrame):
    def __init__(self, session: DocumentSession, parent: QWidget) -> None:
        super().__init__(parent)
        self.session = session
        self.actions: list[QAction] = []
        self.entries: list[Entry] = []
        self.spec: CommandSpec | None = None
        self.form_fields: dict[str, QLineEdit] = {}
        self.return_focus: QWidget | None = None
        """Where keyboard focus goes when the palette closes, usually the canvas."""

        self.setObjectName("command-palette")
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            f"#command-palette {{ background: {theme.PANEL.name()};"
            f" border: 1px solid {theme.BORDER.name()}; border-radius: 4px; }}"
            f" #command-palette QListWidget {{ background: transparent; border: none;"
            f" outline: none; }}"
            f" #command-palette QListWidget::item {{ padding: {SPACE.xs}px {SPACE.m}px; }}"
            f" #command-palette QListWidget::item:selected {{"
            f" background: {theme.FIELD.name()}; color: {theme.TEXT.name()};"
            f" border-left: 2px solid {theme.ACCENT.name()}; }}"
            f" #palette-search {{ font-size: 15px; padding: {SPACE.s}px {SPACE.m}px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.m, SPACE.m, SPACE.m, SPACE.m)
        layout.setSpacing(SPACE.s)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        # Page 1: search and results.
        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(SPACE.s)
        self.search = QLineEdit()
        self.search.setObjectName("palette-search")
        self.search.setPlaceholderText("Search tools, actions, and commands")
        self.search.textChanged.connect(self._refilter)
        self.search.installEventFilter(self)
        self.results = QListWidget()
        self.results.setUniformItemSizes(True)
        self.results.setItemDelegate(_Row(self.results))
        self.results.itemActivated.connect(lambda _item: self._run_current())
        search_layout.addWidget(self.search)
        search_layout.addWidget(self.results)
        self.stack.addWidget(search_page)

        # Page 2: parameters for one command.
        self.form_page = QWidget()
        self.form_layout = QVBoxLayout(self.form_page)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.form_page)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.hide()

    # --- Opening --------------------------------------------------------------------------

    def set_actions(self, actions: list[QAction]) -> None:
        self.actions = actions

    def open(self) -> None:
        self.entries = [
            Entry(
                a.text().replace("&", ""),
                a.shortcut().toString(QKeySequence.SequenceFormat.NativeText),
                action=a,
            )
            for a in self.actions
        ] + [Entry(spec.title, _parameters(spec), spec=spec) for spec in command_specs()]
        self.stack.setCurrentIndex(0)
        self.error.hide()
        self.search.clear()
        self._refilter("")
        parent = self.parentWidget()
        self.setFixedWidth(WIDTH)
        self._fit_list()
        if parent is not None:
            self.move((parent.width() - self.width()) // 2, TOP_MARGIN)
        self.show()
        self.raise_()
        self.search.setFocus()

    def close_palette(self) -> None:
        self.hide()
        target = self.return_focus or self.parentWidget()
        if target is not None:
            target.setFocus()

    # --- Filtering ------------------------------------------------------------------------

    def visible_titles(self) -> list[str]:
        return [self.results.item(i).text() for i in range(self.results.count())]

    def _fit_list(self) -> None:
        """As tall as the results, up to LIST_ROWS; hidden when nothing matches."""
        count = self.results.count()
        row = self.results.sizeHintForRow(0) if count else 0
        self.results.setFixedHeight(row * min(count, LIST_ROWS) + (4 if count else 0))
        self.results.setVisible(count > 0)
        self.adjustSize()

    def _refilter(self, query: str) -> None:
        self.results.clear()
        found = [e for e in self.entries if matches(query, e.title)]
        found.sort(key=lambda e: rank(query, e.title))  # stable: keeps menu order within a rank
        for entry in found:
            item = QListWidgetItem(entry.title)
            item.setData(DETAIL_ROLE, entry.detail)
            item.setData(Qt.ItemDataRole.UserRole, self.entries.index(entry))
            if entry.action is not None and not entry.action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.results.addItem(item)
        for i in range(self.results.count()):
            if self.results.item(i).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.results.setCurrentRow(i)
                break
        if self.isVisible():
            self._fit_list()

    def _move(self, step: int) -> None:
        count = self.results.count()
        row = self.results.currentRow()
        for _ in range(count):
            row = (row + step) % count
            if self.results.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.results.setCurrentRow(row)
                return

    # --- Running --------------------------------------------------------------------------

    def _run_current(self) -> None:
        item = self.results.currentItem()
        if item is None or not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        entry = self.entries[item.data(Qt.ItemDataRole.UserRole)]
        if entry.action is not None:
            self.close_palette()
            entry.action.trigger()
        elif entry.spec is not None:
            self._open_form(entry.spec)

    def _open_form(self, spec: CommandSpec) -> None:
        if spec.uses_selection and not self.session.selection:
            self._show_error(f"{spec.title} needs a selection: select something first")
            return
        self.spec = spec
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        title = QLabel(spec.title)
        title.setFont(theme.font(bold=True))
        self.form_layout.addWidget(title)
        if spec.uses_selection:
            count = len(self.session.selection)
            self.form_layout.addWidget(
                QLabel(f"{count} selected {'entity' if count == 1 else 'entities'}")
            )
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setContentsMargins(0, SPACE.xs, 0, 0)
        self.form_fields = {}
        for field in spec.fields:
            edit = QLineEdit("" if field.default is None else format_number(field.default))
            edit.setObjectName(field.path)
            edit.setAlignment(Qt.AlignmentFlag.AlignRight)
            edit.installEventFilter(self)
            self.form_fields[field.path] = edit
            form.addRow(field.label, edit)
        self.form_layout.addWidget(form_widget)
        hint = QLabel("Return runs it · Tab moves between fields · Esc goes back")
        hint.setProperty("role", "section")
        self.form_layout.addWidget(hint)
        self.error.hide()
        self.stack.setCurrentIndex(1)
        self.adjustSize()
        edits = list(self.form_fields.values())
        empty = [e for e in edits if not e.text()]
        target = empty[0] if empty else (edits[0] if edits else None)
        if target is not None:
            target.setFocus()
            target.selectAll()
        else:
            self._submit()

    def _submit(self) -> None:
        if self.spec is None:
            return
        values: dict[str, float] = {}
        for path, edit in self.form_fields.items():
            value = parse_number(edit.text())
            if value is None:
                self._show_error("Enter a number", path)
                return
            values[path] = value
        command = build(self.spec, values, self.session.selection)
        result = self.session.execute(command)
        match result:
            case Applied():
                self.close_palette()
            case Rejected(errors=errors):
                error = errors[0]
                self._show_error(error.message, error.field)
            case _:
                self._show_error(getattr(result, "message", "Couldn't run that"))

    def _show_error(self, message: str, field: str | None = None) -> None:
        for path, edit in self.form_fields.items():
            bad = field is not None and (path == field or path.startswith(field + "."))
            edit.setProperty("invalid", bad)
            edit.style().unpolish(edit)
            edit.style().polish(edit)
            if bad:
                edit.setFocus()
                edit.selectAll()
        self.error.setText(message)
        self.error.show()
        self.adjustSize()

    # --- Keys -----------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not (isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress):
            return False
        key = event.key()
        if watched is self.search:
            if key == Qt.Key.Key_Down:
                self._move(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run_current()
                return True
            if key == Qt.Key.Key_Escape:
                self.close_palette()
                return True
            return False
        if watched in self.form_fields.values():
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._submit()
                return True
            if key == Qt.Key.Key_Escape:
                self.stack.setCurrentIndex(0)
                self.error.hide()
                self.adjustSize()
                self.search.setFocus()
                self.search.selectAll()
                return True
        return False


def _parameters(spec: CommandSpec) -> str:
    """What the form will ask for, e.g. "corner, width, height" or "selection"."""
    names = dict.fromkeys(f.path.partition(".")[0].replace("_", " ") for f in spec.fields)
    parts = (["selection"] if spec.uses_selection else []) + list(names)
    return ", ".join(parts)
