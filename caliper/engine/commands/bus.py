"""In-memory CommandBus (ADR 0002).

Every command, undo/redo with an undo stack bounded by entry count and size, merge keys,
transactions (undoable or unrecorded, nested into the outermost), change notifications, and
queries.
"""

import json
from collections import deque
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Self

from caliper.contracts.commands import (
    Applied,
    Change,
    ChangeReason,
    Command,
    CommandResult,
    Delta,
    Listener,
    Rejected,
    Unsubscribe,
)
from caliper.contracts.document import Document
from caliper.contracts.kernel import Kernel
from caliper.contracts.queries import Queries
from caliper.engine.commands.handlers import handle
from caliper.engine.document.delta import apply, diff, is_empty
from caliper.engine.io.codec import encode
from caliper.engine.queries import DocumentQueries

if TYPE_CHECKING:
    from caliper.contracts.commands import CommandBus, Transaction


@dataclass(frozen=True, slots=True)
class _UndoEntry:
    label: str
    delta: Delta
    size: int
    """Approximate bytes: the delta's entities as compact JSON."""


class Bus:
    def __init__(
        self,
        document: Document | None = None,
        *,
        undo_limit: int = 1000,
        undo_bytes: int = 64 * 1024 * 1024,
        kernel: Kernel | None = None,
    ) -> None:
        """`undo_limit` caps undo entries and `undo_bytes` their total approximate size.

        The newest entry is always kept, however large.
        """
        self._document = document if document is not None else Document.empty()
        self._undo_limit = undo_limit
        self._undo_bytes = undo_bytes
        self._kernel = kernel
        self._undo: deque[_UndoEntry] = deque()
        self._undo_size = 0
        self._redo: list[_UndoEntry] = []
        self._listeners: list[Listener] = []
        self._open: list[_Transaction] = []
        self._merge_key: str | None = None
        self._merge_base = self._document
        self._merge_entry_on_top = False

    @property
    def document(self) -> Document:
        return self._document

    @property
    def queries(self) -> Queries:
        return DocumentQueries(self._document, self._kernel)

    def execute(self, command: Command, *, merge_key: str | None = None) -> CommandResult:
        """Validate and apply. A command that changes nothing is Applied but not recorded."""
        outcome = handle(self._document, command)
        if isinstance(outcome, list):
            return Rejected(command=command, errors=tuple(outcome))
        delta = diff(self._document, outcome.document)
        if not is_empty(delta):
            before, self._document = self._document, outcome.document
            if not self._open:
                self._record(before, outcome.label, merge_key)
            self._notify(ChangeReason.EXECUTE, delta, outcome.label)
        return Applied(
            command=outcome.command,
            delta=delta,
            label=outcome.label,
            created_ids=outcome.created_ids,
        )

    def transaction(self, label: str, *, undoable: bool = True) -> "Transaction":
        """Open with `with`. Nested transactions fold into the outermost, whose `undoable` wins.

        `rollback()` reverts the transaction's changes at once, and leaving the block then
        reverts anything executed after it too: a rolled-back transaction never commits.
        """
        return _Transaction(self, label, undoable=undoable)

    def undo(self) -> Change | None:
        self._refuse_inside_transaction("undo")
        self._end_merge()
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._undo_size -= entry.size
        inverse = entry.delta.inverted()
        self._document = apply(self._document, inverse)
        self._redo.append(entry)
        return self._notify(ChangeReason.UNDO, inverse, entry.label)

    def redo(self) -> Change | None:
        self._refuse_inside_transaction("redo")
        self._end_merge()
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._document = apply(self._document, entry.delta)
        self._push(entry)
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

    # --- Undo stack -----------------------------------------------------------------------

    def _record(self, before: Document, label: str, merge_key: str | None) -> None:
        self._redo.clear()
        if merge_key is not None and merge_key == self._merge_key:
            # One entry spans the whole run of same-key executes, from before the first.
            if self._merge_entry_on_top:
                self._undo_size -= self._undo.pop().size
            net = diff(self._merge_base, self._document)
            self._merge_entry_on_top = not is_empty(net)
            if self._merge_entry_on_top:
                self._push(_entry(label, net))
            return
        self._merge_key, self._merge_base = merge_key, before
        self._merge_entry_on_top = merge_key is not None
        self._push(_entry(label, diff(before, self._document)))

    def _push(self, entry: _UndoEntry) -> None:
        self._undo.append(entry)
        self._undo_size += entry.size
        while len(self._undo) > 1 and (
            len(self._undo) > self._undo_limit or self._undo_size > self._undo_bytes
        ):
            self._undo_size -= self._undo.popleft().size

    def _end_merge(self) -> None:
        self._merge_key = None
        self._merge_entry_on_top = False

    # --- Transactions ---------------------------------------------------------------------

    def _begin(self, transaction: "_Transaction") -> Document:
        self._end_merge()
        self._open.append(transaction)
        return self._document

    def _finish(self, transaction: "_Transaction", *, revert: bool) -> None:
        if not self._open or self._open[-1] is not transaction:
            raise RuntimeError("transactions must close in the reverse order they opened")
        self._open.pop()
        if revert:
            self._revert_to(transaction.start, transaction.label)
            return
        if self._open:
            return  # nested: the outermost transaction commits everything
        net = diff(transaction.start, self._document)
        if is_empty(net):
            return
        self._redo.clear()
        if transaction.undoable:
            self._push(_entry(transaction.label, net))
        else:
            self._undo.clear()
            self._undo_size = 0

    def _revert_to(self, document: Document, label: str) -> None:
        delta = diff(self._document, document)
        if not is_empty(delta):
            self._document = document
            self._notify(ChangeReason.ROLLBACK, delta, label)

    def _refuse_inside_transaction(self, action: str) -> None:
        if self._open:
            raise RuntimeError(f"can't {action} while a transaction is open")

    def _notify(self, reason: ChangeReason, delta: Delta, label: str) -> Change:
        change = Change(reason=reason, delta=delta, label=label)
        for listener in list(self._listeners):
            listener(change)
        return change


class _Transaction:
    def __init__(self, bus: Bus, label: str, *, undoable: bool) -> None:
        self._bus = bus
        self.label = label
        self.undoable = undoable
        self.start = bus.document
        self._state = "new"

    def rollback(self) -> None:
        if self._state == "new" or self._state == "closed":
            raise RuntimeError("rollback() is only allowed inside the transaction's with block")
        self._bus._revert_to(self.start, self.label)
        self._state = "rolled_back"

    def __enter__(self) -> Self:
        if self._state != "new":
            raise RuntimeError("a transaction can only be entered once")
        self.start = self._bus._begin(self)
        self._state = "open"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        revert = exc_type is not None or self._state == "rolled_back"
        self._state = "closed"
        self._bus._finish(self, revert=revert)


def _entry(label: str, delta: Delta) -> _UndoEntry:
    entities = encode({"after": delta.after, "before": delta.before})
    return _UndoEntry(
        label=label, delta=delta, size=len(json.dumps(entities, separators=(",", ":")))
    )


if TYPE_CHECKING:
    _conforms: CommandBus = Bus()
