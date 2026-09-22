"""One open document: the bus, its file, and the UI state that goes with it.

The bus owns the document. The session owns what the document is not allowed to hold:
selection, hover, the file path, and whether there are unsaved changes.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from caliper.contracts.commands import (
    Applied,
    Change,
    Command,
    CommandBus,
    CommandResult,
    Rejected,
    Transaction,
)
from caliper.contracts.document import Document, EntityId, Ref
from caliper.contracts.queries import CheckResult, Expectation, Queries
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot


class Author(StrEnum):
    """Who made a change. The contract's `Change` doesn't say, so the shell records it."""

    YOU = "You"
    AGENT = "Agent"


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    label: str
    author: Author
    at: float
    """Seconds since the epoch."""
    commands: tuple[Command, ...] = ()
    """The resolved commands this entry ran, for the file's optional history section."""


class DocumentSession(QObject):
    document_changed = Signal()
    """The document was replaced or changed. Read `session.document`."""
    changed = Signal(object)
    """A `Change` from the bus, before `document_changed`, for views that update incrementally."""
    document_replaced = Signal()
    """New or Open swapped in another document: views should rebuild from scratch."""
    selection_changed = Signal()
    hover_changed = Signal()
    file_changed = Signal()
    """The path or the unsaved-changes state changed."""
    message = Signal(str)
    """Something worth a line in the status bar."""
    history_changed = Signal()
    checks_changed = Signal()
    references_changed = Signal()
    """The points and curves picked for a constraint changed (`references`)."""

    def __init__(self, bus: CommandBus | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus: CommandBus = bus if bus is not None else Bus()
        self._unsubscribe = self._bus.subscribe(self._on_change)
        self._saved: Document = self._bus.document
        self._path: Path | None = None
        self._selection: frozenset[EntityId] = frozenset()
        self._hover: EntityId | None = None
        self._references: tuple[Ref, ...] = ()
        self._history: list[HistoryEntry] = []
        self._history_position = 0
        self._transaction_depth = 0
        self._pending: list[Command] = []
        self.save_history = False
        self.opened_steps = 0
        """How many recorded steps the file just opened carried, for the status line."""
        """Write the optional history section when saving (ADR 0005: off by default)."""
        self._checks: list[Expectation] = []
        self.last_measurement: tuple[Ref, Ref] | None = None
        """The two points the Measure tool last measured, for turning into a check."""

    # --- Engine ---------------------------------------------------------------------------

    @property
    def bus(self) -> CommandBus:
        return self._bus

    @property
    def document(self) -> Document:
        return self._bus.document

    @property
    def queries(self) -> Queries:
        return self._bus.queries

    def execute(self, command: Command, *, author: Author = Author.YOU) -> CommandResult:
        """Send a command. A rejection is also reported as a message."""
        result = self._bus.execute(command)
        if isinstance(result, Rejected):
            self.message.emit("; ".join(e.message for e in result.errors))
            return result
        if isinstance(result, Applied) and (result.delta.before or result.delta.after):
            if self._transaction_depth:
                self._pending.append(result.command)
            else:
                self._record(result.label, author, (result.command,))
        return result

    @contextmanager
    def transaction(self, label: str, *, author: Author = Author.YOU) -> Iterator[Transaction]:
        """Group commands into one undo step, recorded once in the history."""
        before = self._bus.document
        self._transaction_depth += 1
        committed = False
        try:
            with self._bus.transaction(label) as tx:
                yield tx
            committed = True
        finally:
            self._transaction_depth -= 1
        if self._transaction_depth == 0:
            ran, self._pending = tuple(self._pending), []
            if committed and self._bus.document != before:
                self._record(label, author, ran)

    def undo(self) -> None:
        if (change := self._bus.undo()) is not None:
            self._history_position = max(0, self._history_position - 1)
            self.history_changed.emit()
            self.message.emit(f"Undid {change.label}")

    def redo(self) -> None:
        if (change := self._bus.redo()) is not None:
            self._history_position = min(len(self._history), self._history_position + 1)
            self.history_changed.emit()
            self.message.emit(f"Redid {change.label}")

    # --- History --------------------------------------------------------------------------

    @property
    def history(self) -> tuple[HistoryEntry, ...]:
        """Every recorded change, oldest first, including undone ones that can be redone."""
        return tuple(self._history)

    @property
    def history_position(self) -> int:
        """How many history entries are currently applied; later ones are undone."""
        return self._history_position

    def _record(self, label: str, author: Author, commands: tuple[Command, ...] = ()) -> None:
        del self._history[self._history_position :]  # a new change discards the redo stack
        self._history.append(
            HistoryEntry(label=label, author=author, at=time.time(), commands=commands)
        )
        self._history_position = len(self._history)
        self.history_changed.emit()

    # --- Checks ---------------------------------------------------------------------------

    @property
    def checks(self) -> tuple[Expectation, ...]:
        """Requirements for this session. Not saved yet: where they live is undecided."""
        return tuple(self._checks)

    def add_check(self, expectation: Expectation) -> None:
        self._checks.append(expectation)
        self.checks_changed.emit()

    def remove_check(self, index: int) -> None:
        del self._checks[index]
        self.checks_changed.emit()

    def check_results(self) -> list[CheckResult]:
        queries = self.queries
        return [queries.check(e) for e in self._checks]

    def _on_change(self, change: Change) -> None:
        live = self._bus.document.entities
        if any(id not in live for id in self._selection):
            self._selection = frozenset(id for id in self._selection if id in live)
            self.selection_changed.emit()
        if self._hover is not None and self._hover not in live:
            self._hover = None
            self.hover_changed.emit()
        if any(ref.entity not in live for ref in self._references):
            self._references = tuple(r for r in self._references if r.entity in live)
            self.references_changed.emit()
        self.changed.emit(change)
        self.document_changed.emit()
        self.file_changed.emit()

    # --- Files ----------------------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def is_dirty(self) -> bool:
        return self._bus.document != self._saved

    def replace(self, bus: CommandBus, path: Path | None) -> None:
        """Switch to another document. Undo history never crosses documents, so it's a new bus."""
        self._unsubscribe()
        self._bus = bus
        self._unsubscribe = bus.subscribe(self._on_change)
        self._saved = bus.document
        self._path = path
        self._selection = frozenset()
        self._hover = None
        self._references = ()
        self._history = []
        self._history_position = 0
        self._checks = []
        self.last_measurement = None
        self.history_changed.emit()
        self.checks_changed.emit()
        self.selection_changed.emit()
        self.hover_changed.emit()
        self.references_changed.emit()
        self.document_replaced.emit()
        self.document_changed.emit()
        self.file_changed.emit()

    def new(self) -> None:
        self.replace(Bus(), None)

    def open(self, path: Path) -> None:
        """Raises `LoadError` (from caliper.engine.io.canonical) for a bad file; nothing changes."""
        opened = snapshot.read_file(path)
        self.replace(Bus(opened.document), path)
        self.opened_steps = len(opened.history) if opened.history is not None else 0

    @property
    def recorded_commands(self) -> tuple[Command, ...]:
        """Every command still applied, in order: what the file's history section holds."""
        return tuple(
            command
            for entry in self._history[: self._history_position]
            for command in entry.commands
        )

    def save(self, path: Path | None = None) -> None:
        target = path if path is not None else self._path
        if target is None:
            raise ValueError("an untitled document needs a path")
        document = self._bus.document
        history = self.recorded_commands if self.save_history else None
        snapshot.save(document, target, history=history)
        self._saved = document
        self._path = target
        self.file_changed.emit()

    # --- UI state -------------------------------------------------------------------------

    @property
    def selection(self) -> frozenset[EntityId]:
        return self._selection

    def set_selection(self, ids: frozenset[EntityId]) -> None:
        if ids != self._selection:
            self._selection = ids
            self.selection_changed.emit()

    @property
    def hover(self) -> EntityId | None:
        return self._hover

    def set_hover(self, id: EntityId | None) -> None:
        if id != self._hover:
            self._hover = id
            self.hover_changed.emit()

    @property
    def references(self) -> tuple[Ref, ...]:
        """Points and curves picked with the Constrain tool, in the order they were picked.

        UI state like the selection: a constraint action applies to these when there are any.
        """
        return self._references

    def set_references(self, refs: tuple[Ref, ...]) -> None:
        if refs != self._references:
            self._references = refs
            self.references_changed.emit()
