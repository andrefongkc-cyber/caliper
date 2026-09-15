"""In-memory CommandBus (ADR 0002).

Phase 0 slice: every create command, ModifyEntity, undo/redo, change notifications, and
the Phase 0.5 queries. MoveEntities, DeleteEntities, transactions, and merge keys raise
NotImplementedError until they land in V1 (docs/workplan/core.md).
"""

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from caliper.contracts.commands import (
    Applied,
    Change,
    ChangeReason,
    Command,
    CommandResult,
    Delta,
    Listener,
    Rejected,
    Transaction,
    Unsubscribe,
)
from caliper.contracts.document import Document
from caliper.contracts.queries import Queries
from caliper.engine.commands.handlers import handle
from caliper.engine.document.delta import apply, diff, is_empty
from caliper.engine.queries import DocumentQueries

if TYPE_CHECKING:
    from caliper.contracts.commands import CommandBus


@dataclass(frozen=True, slots=True)
class _UndoEntry:
    label: str
    delta: Delta


class Bus:
    def __init__(self, document: Document | None = None, *, undo_limit: int = 1000) -> None:
        self._document = document if document is not None else Document.empty()
        self._undo: deque[_UndoEntry] = deque(maxlen=undo_limit)
        self._redo: list[_UndoEntry] = []
        self._listeners: list[Listener] = []

    @property
    def document(self) -> Document:
        return self._document

    @property
    def queries(self) -> Queries:
        return DocumentQueries(self._document)

    def execute(self, command: Command, *, merge_key: str | None = None) -> CommandResult:
        """Validate and apply. A command that changes nothing is Applied but not recorded."""
        if merge_key is not None:
            raise NotImplementedError("merge keys land in V1 (docs/workplan/core.md)")
        outcome = handle(self._document, command)
        if isinstance(outcome, list):
            return Rejected(command=command, errors=tuple(outcome))
        delta = diff(self._document, outcome.document)
        if not is_empty(delta):
            self._document = outcome.document
            self._undo.append(_UndoEntry(label=outcome.label, delta=delta))
            self._redo.clear()
            self._notify(ChangeReason.EXECUTE, delta, outcome.label)
        return Applied(
            command=outcome.command,
            delta=delta,
            label=outcome.label,
            created_ids=outcome.created_ids,
        )

    def transaction(self, label: str, *, undoable: bool = True) -> Transaction:
        raise NotImplementedError("transactions land in V1 (docs/workplan/core.md)")

    def undo(self) -> Change | None:
        if not self._undo:
            return None
        entry = self._undo.pop()
        inverse = entry.delta.inverted()
        self._document = apply(self._document, inverse)
        self._redo.append(entry)
        return self._notify(ChangeReason.UNDO, inverse, entry.label)

    def redo(self) -> Change | None:
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._document = apply(self._document, entry.delta)
        self._undo.append(entry)
        return self._notify(ChangeReason.REDO, entry.delta, entry.label)

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def subscribe(self, listener: Listener) -> Unsubscribe:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _notify(self, reason: ChangeReason, delta: Delta, label: str) -> Change:
        change = Change(reason=reason, delta=delta, label=label)
        for listener in list(self._listeners):
            listener(change)
        return change


if TYPE_CHECKING:
    _conforms: CommandBus = Bus()
