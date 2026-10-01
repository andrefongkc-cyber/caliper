"""One open document: the bus, its file, and the UI state that goes with it.

The bus owns the document. The session owns what the document is not allowed to hold:
selection, hover, the file path, and whether there are unsaved changes. Checks are in the
document (C-1): the session only reads them out, and adds and removes them by command.
"""

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from PySide6.QtCore import QObject, Signal

from caliper.contracts.commands import (
    Applied,
    Change,
    ChangeReason,
    Command,
    CommandBus,
    CommandResult,
    CreateArc,
    CreateCheck,
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreatePoint,
    CreateRectangle,
    DeleteEntities,
    Rejected,
    Transaction,
)
from caliper.contracts.document import Document, EntityId, Expectation, Ref, Sketch
from caliper.contracts.queries import CheckResult, Queries
from caliper.engine.commands.bus import Bus
from caliper.engine.document.delta import diff, is_empty
from caliper.engine.io import snapshot


class Author(StrEnum):
    """Who made a change in the app. The session names it to the bus as `Change.source`; a
    change someone else made on the session's bus is recorded under the source it gave."""

    YOU = "You"
    AGENT = "Agent"


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    label: str
    author: str
    """An `Author`, or the source another caller of the bus gave ("Unknown" without one)."""
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
    """A change added, edited, or removed a check, or another document came in."""
    references_changed = Signal()
    """The points and curves picked for a constraint changed (`references`)."""
    flagged_changed = Signal()
    active_sketch_changed = Signal()
    """The sketch being edited changed (V2's sketch mode): the canvas shows another sketch."""
    """What the last rejected change named changed (`flagged`)."""

    def __init__(self, bus: CommandBus | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus: CommandBus = bus if bus is not None else Bus()
        self._unsubscribe = self._bus.subscribe(self._on_change)
        self._saved: Document = self._bus.document
        self._path: Path | None = None
        self._selection: frozenset[EntityId] = frozenset()
        self._hover: EntityId | None = None
        self._references: tuple[Ref, ...] = ()
        self._flagged: frozenset[EntityId] = frozenset()
        self._history: list[HistoryEntry] = []
        self._history_position = 0
        self._transaction_depth = 0
        self._own = 0
        """Executes the session itself is making: their changes are recorded by the session."""
        self._pending: list[Command] = []
        self._held = False
        """A change arrived inside the open transaction; views hear of it when it closes."""
        self.save_history = False
        self.opened_steps = 0
        """How many recorded steps the file just opened carried, for the status line."""
        """Write the optional history section when saving (ADR 0005: off by default)."""
        self.last_measurement: tuple[Ref, Ref] | None = None
        """The two points the Measure tool last measured, for turning into a check."""
        self._active: EntityId | None = _last_sketch(self._bus.document)
        self._view: tuple[Document, EntityId | None, Document, Queries] | None = None

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
        """Send a command. A rejection is also reported as a message. Geometry and extrudes
        that don't name a sketch go in the one being edited, once the part has several (ADR
        0011); with one, the command goes as it came and the engine finds the only sketch."""
        if (
            isinstance(command, _IN_A_SKETCH)
            and command.sketch is None
            and self._active is not None
            and len(_sketches(self._bus.document)) > 1
        ):
            command = replace(command, sketch=self._active)
        self._own += 1
        try:
            result = self._bus.execute(command, source=author.value)
        finally:
            self._own -= 1
        if isinstance(result, Rejected):
            self.message.emit("; ".join(e.message for e in result.errors))
            named = frozenset(id for e in result.errors for id in e.ids)
            self._set_flagged(named & self._bus.document.entities.keys())
            return result
        if isinstance(result, Applied) and not is_empty(result.delta):  # features count (V2)
            if self._transaction_depth:
                self._pending.append(result.command)
            else:
                self._record(result.label, author, (result.command,))
        return result

    @contextmanager
    def transaction(self, label: str, *, author: Author = Author.YOU) -> Iterator[Transaction]:
        """Group commands into one undo step, recorded once in the history.

        Views hear of the whole transaction once, as one change, when it closes, not of each
        command in it: accepting a 250-change proposal redraws, re-lists, and re-measures the
        sketch once instead of 250 times."""
        before = self._bus.document
        self._transaction_depth += 1
        committed = False
        try:
            with self._bus.transaction(label, source=author.value) as tx:
                yield tx
            committed = True
        finally:
            self._transaction_depth -= 1
            if self._transaction_depth == 0 and self._held:
                self._held = False
                delta = diff(before, self._bus.document)
                if not is_empty(delta):
                    self._announce(Change(reason=ChangeReason.COMMIT, delta=delta, label=label))
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

    def _record(self, label: str, author: str, commands: tuple[Command, ...] = ()) -> None:
        del self._history[self._history_position :]  # a new change discards the redo stack
        self._history.append(
            HistoryEntry(label=label, author=author, at=time.time(), commands=commands)
        )
        self._history_position = len(self._history)
        self.history_changed.emit()

    # --- Checks ---------------------------------------------------------------------------

    @property
    def check_ids(self) -> tuple[EntityId, ...]:
        """The document's checks, in the order they were made (e2 before e10)."""
        return tuple(
            sorted(
                (id for id, e in self.document.entities.items() if isinstance(e, Expectation)),
                key=_natural,
            )
        )

    @property
    def checks(self) -> tuple[Expectation, ...]:
        """The requirements stored in the document, in `check_ids` order."""
        entities = self.document.entities
        return tuple(entities[id] for id in self.check_ids)  # type: ignore[misc]

    def add_check(self, expectation: Expectation, *, author: Author = Author.YOU) -> CommandResult:
        """Store a check: a command like any change, so it's undone and saved like one."""
        return self.execute(check_command(expectation), author=author)

    def remove_check(self, index: int) -> CommandResult:
        """Delete the check at `index` in `checks`."""
        return self.execute(DeleteEntities(ids=(self.check_ids[index],)))

    def check_results(self) -> list[CheckResult]:
        queries = self.queries
        return [queries.check(e) for e in self.checks]

    def _on_change(self, change: Change) -> None:
        if self._transaction_depth:
            self._held = True  # announced once, when the transaction closes
            return
        if not self._own and change.reason is ChangeReason.EXECUTE:
            # Made on this bus by someone other than the session: a script, say (C-4).
            self._record(change.label, change.source or "Unknown")
        self._announce(change)

    def _announce(self, change: Change) -> None:
        document = self._bus.document
        if change.delta.features_after is not None and self._active not in _sketches(document):
            self._active, self._view = _last_sketch(document), None  # its sketch was deleted
            self.active_sketch_changed.emit()
        live = document.entities.keys() | {f.id for f in document.features}
        if any(id not in live for id in self._selection):
            self._selection = frozenset(id for id in self._selection if id in live)
            self.selection_changed.emit()
        if self._hover is not None and self._hover not in live:
            self._hover = None
            self.hover_changed.emit()
        self._set_flagged(frozenset())  # a change went through: the rejection is history
        if any(ref.entity not in live for ref in self._references):
            self._references = tuple(r for r in self._references if r.entity in live)
            self.references_changed.emit()
        self.changed.emit(change)
        self.document_changed.emit()
        self.file_changed.emit()
        if any(
            isinstance(e, Expectation)
            for e in (*change.delta.before.values(), *change.delta.after.values())
        ):
            self.checks_changed.emit()

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
        self._flagged = frozenset()
        self._history = []
        self._history_position = 0
        self.last_measurement = None
        self._active, self._view = _last_sketch(bus.document), None
        self.history_changed.emit()
        self.checks_changed.emit()
        self.selection_changed.emit()
        self.hover_changed.emit()
        self.references_changed.emit()
        self.flagged_changed.emit()
        self.active_sketch_changed.emit()
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

    # --- Sketch mode (V2) ------------------------------------------------------------------

    @property
    def active_sketch(self) -> EntityId | None:
        """The sketch being edited: where drawing goes, and what the canvas shows. UI state,
        never in the document. None only when the part has no sketch."""
        return self._active

    def set_active_sketch(self, sketch: EntityId) -> None:
        if sketch not in _sketches(self._bus.document):
            raise ValueError(f"{sketch!r} isn't one of the part's sketches")
        if sketch == self._active:
            return
        self._active, self._view = sketch, None
        # What was selected is in the sketch left behind.
        self.set_selection(frozenset())
        self.set_hover(None)
        self.active_sketch_changed.emit()

    @property
    def sketch_view(self) -> Document:
        """The document as the canvas edits it: the part's entities in the active sketch. A
        part with one sketch, every V1 file among them, is the document itself. Commands still
        go to the whole document; this only limits what's drawn and picked, so nothing in
        another sketch is edited by accident."""
        document = self._bus.document
        if len(_sketches(document)) <= 1:
            return document
        return self._viewed(document)[0]

    @property
    def sketch_queries(self) -> Queries:
        """Queries over `sketch_view`: what picking, snapping, and the sketch's own solve state
        see."""
        document = self._bus.document
        if len(_sketches(document)) <= 1:
            return self._bus.queries
        return self._viewed(document)[1]

    def _viewed(self, document: Document) -> tuple[Document, Queries]:
        cached = self._view
        if cached is not None and cached[0] is document and cached[1] == self._active:
            return cached[2], cached[3]
        sketch_of = self._bus.queries.sketch_of
        entities = {id: e for id, e in document.entities.items() if sketch_of(id) == self._active}
        view = replace(document, entities=MappingProxyType(entities))
        queries = Bus(view).queries
        self._view = (document, self._active, view, queries)
        return view, queries

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

    @property
    def flagged(self) -> frozenset[EntityId]:
        """What the last rejected command named (`Error.ids`): the constraints in the way.

        Cleared by the next change that goes through, including undo and redo.
        """
        return self._flagged

    def _set_flagged(self, ids: frozenset[EntityId]) -> None:
        if ids != self._flagged:
            self._flagged = ids
            self.flagged_changed.emit()


def check_command(expectation: Expectation) -> CreateCheck:
    """The command that stores `expectation` in the document."""
    return CreateCheck(
        metric=expectation.metric,
        expected=expectation.expected,
        tolerance=expectation.tolerance,
        refs=expectation.refs,
        ids=expectation.ids,
    )


def _natural(id: EntityId) -> list[int | str]:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", id)]


_IN_A_SKETCH = (CreatePoint, CreateLine, CreateCircle, CreateArc, CreateRectangle, CreateExtrude)
"""Commands that draw in a sketch, or read one, and take `sketch` (None: the only one)."""


def _sketches(document: Document) -> list[EntityId]:
    return [f.id for f in document.features if isinstance(f, Sketch)]


def _last_sketch(document: Document) -> EntityId | None:
    """Where a part opens for editing: its last sketch, the one most recently made."""
    found = _sketches(document)
    return found[-1] if found else None
