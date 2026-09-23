"""Transactions, merge keys, and the size-bounded undo stack (ADR 0002)."""

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from caliper.contracts.commands import (
    Applied,
    Change,
    ChangeReason,
    CommandResult,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    MoveEntities,
    Rejected,
    Transaction,
)
from caliper.contracts.document import Document, EntityId, Point2, Rectangle
from caliper.engine.commands.bus import Bus
from caliper.engine.document.delta import apply
from caliper.engine.io import snapshot

E1, E2, E3 = EntityId("e1"), EntityId("e2"), EntityId("e3")
ORIGIN = Point2(x=0.0, y=0.0)


def rectangle(width: float = 100.0) -> CreateRectangle:
    return CreateRectangle(corner=ORIGIN, width=width, height=50.0)


def width(bus: Bus, id: EntityId = E1) -> float:
    entity = bus.document.entities[id]
    assert isinstance(entity, Rectangle)
    return entity.width


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def recording(bus: Bus) -> list[tuple[ChangeReason, str]]:
    changes: list[tuple[ChangeReason, str]] = []

    def listen(change: Change) -> None:
        changes.append((change.reason, change.label))

    bus.subscribe(listen)
    return changes


# --- Undoable transactions --------------------------------------------------------------


def test_a_transaction_commits_as_one_undo_entry() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    changes = recording(bus)
    with bus.transaction("Add Mounting Holes"):
        applied(bus.execute(rectangle()))
        applied(bus.execute(rectangle()))
        applied(bus.execute(ModifyEntity(id=E2, changes={"width": 5.0})))
        assert bus.undo_label == "Create Rectangle"  # nothing recorded yet
    assert bus.undo_label == "Add Mounting Holes"
    assert [reason for reason, _ in changes] == [ChangeReason.EXECUTE] * 3 + [ChangeReason.COMMIT]
    after = bus.document

    undone = bus.undo()
    assert undone is not None
    assert undone.label == "Add Mounting Holes"
    assert set(bus.document.entities) == {E1}
    assert bus.undo_label == "Create Rectangle"
    bus.redo()
    assert bus.document == after


def test_the_undo_entry_holds_the_net_change() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    with bus.transaction("Scratch"):
        applied(bus.execute(rectangle()))
        applied(bus.execute(DeleteEntities(ids=(E2,))))
        for w in (110.0, 120.0, 130.0):
            applied(bus.execute(ModifyEntity(id=E1, changes={"width": w})))
    undone = bus.undo()
    assert undone is not None
    # e2 was created and deleted inside: no trace of it, only e1's first and last width.
    assert set(undone.delta.before) == {E1}
    assert set(undone.delta.after) == {E1}
    assert width(bus) == 100.0


def test_a_transaction_that_changes_nothing_records_nothing_and_keeps_redo() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    applied(bus.execute(ModifyEntity(id=E1, changes={"width": 120.0})))
    bus.undo()
    with bus.transaction("Nothing"):
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 90.0})))
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 100.0})))
    assert bus.undo_label == "Create Rectangle"
    assert bus.redo_label == "Change Width"


def test_a_committed_change_clears_redo() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    bus.undo()
    with bus.transaction("Batch"):
        applied(bus.execute(rectangle(width=7.0)))
    assert bus.redo_label is None


def test_committing_announces_the_new_labels() -> None:
    """Contract gap 9: the commit changed the labels without a Change, so a menu reading
    "Redo Create Rectangle" stayed stale until the next edit."""
    bus = Bus()
    applied(bus.execute(rectangle()))
    applied(bus.execute(rectangle()))
    bus.undo()
    heard: list[tuple[Change, str | None, str | None]] = []

    def listen(change: Change) -> None:
        heard.append((change, bus.undo_label, bus.redo_label))

    bus.subscribe(listen)
    with bus.transaction("Add Mounting Holes"):
        applied(bus.execute(rectangle(width=7.0)))
    assert [change.reason for change, _, _ in heard] == [ChangeReason.EXECUTE, ChangeReason.COMMIT]
    commit, undo_label, redo_label = heard[-1]
    assert commit.label == "Add Mounting Holes"
    assert (undo_label, redo_label) == ("Add Mounting Holes", None)  # current on arrival
    # The execute already announced the new rectangle; the commit announces no entity twice.
    assert (dict(commit.delta.before), dict(commit.delta.after)) == ({}, {})
    assert commit.delta.next_id_before == commit.delta.next_id_after == bus.document.next_id


def test_nested_transactions_announce_one_commit_from_the_outermost() -> None:
    bus = Bus()
    changes = recording(bus)
    with bus.transaction("Outer"):
        with bus.transaction("Inner"):
            applied(bus.execute(rectangle()))
        applied(bus.execute(rectangle()))
    assert changes == [
        (ChangeReason.EXECUTE, "Create Rectangle"),
        (ChangeReason.EXECUTE, "Create Rectangle"),
        (ChangeReason.COMMIT, "Outer"),
    ]


def test_no_commit_is_announced_when_the_labels_stay_the_same() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    changes = recording(bus)
    with bus.transaction("Nothing"):  # the edits cancel out: nothing is recorded
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 90.0})))
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 100.0})))
    with bus.transaction("Attempt") as transaction:
        applied(bus.execute(rectangle()))
        transaction.rollback()

    def fail() -> None:
        with bus.transaction("Optimizer"):
            applied(bus.execute(rectangle()))
            raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        fail()
    assert ChangeReason.COMMIT not in [reason for reason, _ in changes]
    assert bus.undo_label == "Create Rectangle"


def test_a_rejected_command_does_not_roll_back() -> None:
    bus = Bus()
    with bus.transaction("Retry"):
        applied(bus.execute(rectangle()))
        assert isinstance(bus.execute(rectangle(width=-1.0)), Rejected)
        applied(bus.execute(rectangle(width=2.0)))
    assert set(bus.document.entities) == {E1, E2}
    assert bus.undo_label == "Retry"


def test_merge_keys_are_ignored_inside_a_transaction() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    with bus.transaction("Drag"):
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 110.0}), merge_key="w"))
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 120.0}), merge_key="w"))
    applied(bus.execute(ModifyEntity(id=E1, changes={"width": 130.0}), merge_key="w"))
    assert bus.undo_label == "Change Width"
    bus.undo()
    assert width(bus) == 120.0  # the transaction's entry is separate from the later drag
    assert bus.undo_label == "Drag"


# --- Rollback ---------------------------------------------------------------------------


def test_rollback_reverts_at_once_and_never_commits() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    before = bus.document
    changes = recording(bus)
    with bus.transaction("Attempt") as transaction:
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 5.0})))
        applied(bus.execute(rectangle()))
        transaction.rollback()
        assert bus.document == before
        applied(bus.execute(rectangle(width=9.0)))  # also discarded when the block ends
    assert bus.document == before
    assert bus.undo_label == "Create Rectangle"
    assert changes[-3:] == [
        (ChangeReason.ROLLBACK, "Attempt"),
        (ChangeReason.EXECUTE, "Create Rectangle"),
        (ChangeReason.ROLLBACK, "Attempt"),
    ]


def test_an_escaping_exception_rolls_back_and_propagates() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    before = bus.document
    changes = recording(bus)

    def optimize() -> None:
        with bus.transaction("Optimizer"):
            applied(bus.execute(ModifyEntity(id=E1, changes={"width": 5.0})))
            raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        optimize()
    assert bus.document == before
    assert changes[-1] == (ChangeReason.ROLLBACK, "Optimizer")
    assert bus.undo_label == "Create Rectangle"


def test_rolling_back_with_nothing_changed_sends_no_change() -> None:
    bus = Bus()
    changes = recording(bus)
    with bus.transaction("Empty") as transaction:
        transaction.rollback()
    assert changes == []


# --- Nesting ----------------------------------------------------------------------------


def test_nested_transactions_commit_into_the_outermost() -> None:
    bus = Bus()
    with bus.transaction("Outer"):
        applied(bus.execute(rectangle()))
        with bus.transaction("Inner"):
            applied(bus.execute(rectangle()))
        assert bus.undo_label is None
    assert bus.undo_label == "Outer"
    bus.undo()
    assert bus.document == Document.empty()
    assert bus.undo_label is None


def test_an_inner_rollback_reverts_only_the_inner_block() -> None:
    bus = Bus()
    with bus.transaction("Outer"):
        applied(bus.execute(rectangle()))
        with bus.transaction("Inner") as inner:
            applied(bus.execute(rectangle()))
            inner.rollback()
        try:
            with bus.transaction("Inner again"):
                applied(bus.execute(rectangle()))
                raise ValueError("bad step")
        except ValueError:
            pass
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 3.0})))
    assert set(bus.document.entities) == {E1}
    assert width(bus) == 3.0
    assert bus.undo_label == "Outer"


def test_the_outermost_transaction_decides_whether_to_record() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    with bus.transaction("Outer"), bus.transaction("Inner", undoable=False):
        applied(bus.execute(rectangle()))
    assert bus.undo_label == "Outer"


# --- Unrecorded -------------------------------------------------------------------------


def test_an_unrecorded_transaction_clears_undo_and_redo() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    applied(bus.execute(rectangle()))
    bus.undo()
    with bus.transaction("Optimize", undoable=False):
        for w in range(1, 50):
            applied(bus.execute(ModifyEntity(id=E1, changes={"width": float(w)})))
    assert width(bus) == 49.0
    assert (bus.undo_label, bus.redo_label) == (None, None)
    assert bus.undo() is None


def test_an_unrecorded_commit_announces_the_cleared_history() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    heard: list[tuple[ChangeReason, str, str | None]] = []

    def listen(change: Change) -> None:
        heard.append((change.reason, change.label, bus.undo_label))

    bus.subscribe(listen)
    with bus.transaction("Optimize", undoable=False):
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 5.0})))
    assert heard == [
        (ChangeReason.EXECUTE, "Change Width", "Create Rectangle"),
        (ChangeReason.COMMIT, "Optimize", None),
    ]


def test_an_unrecorded_transaction_that_changes_nothing_keeps_history() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    with bus.transaction("Probe", undoable=False):
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 1.0})))
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": 100.0})))
    assert bus.undo_label == "Create Rectangle"


# --- Misuse -----------------------------------------------------------------------------


def test_undo_and_redo_are_refused_inside_a_transaction() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    with bus.transaction("Batch"):
        with pytest.raises(RuntimeError, match="undo"):
            bus.undo()
        with pytest.raises(RuntimeError, match="redo"):
            bus.redo()


def test_transaction_objects_are_single_use() -> None:
    bus = Bus()
    transaction: Transaction = bus.transaction("Once")
    with pytest.raises(RuntimeError):
        transaction.rollback()
    with transaction:
        pass
    with pytest.raises(RuntimeError), transaction:
        pass
    with pytest.raises(RuntimeError):
        transaction.rollback()


def test_transactions_must_close_innermost_first() -> None:
    bus = Bus()
    outer, inner = bus.transaction("Outer"), bus.transaction("Inner")
    outer.__enter__()
    inner.__enter__()
    with pytest.raises(RuntimeError, match="reverse order"):
        outer.__exit__(None, None, None)


# --- Merge keys -------------------------------------------------------------------------


def drag(bus: Bus, *widths: float, key: str | None = "e1.width") -> None:
    for w in widths:
        applied(bus.execute(ModifyEntity(id=E1, changes={"width": w}), merge_key=key))


def test_consecutive_executes_with_one_merge_key_are_one_undo_step() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    drag(bus, 101.0, 105.0, 120.0)
    assert bus.undo_label == "Change Width"
    bus.undo()
    assert width(bus) == 100.0
    assert bus.undo_label == "Create Rectangle"
    bus.redo()
    assert width(bus) == 120.0


def test_a_different_key_no_key_or_an_undo_starts_a_new_step() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    drag(bus, 110.0, 120.0)
    drag(bus, 130.0, key="other")
    drag(bus, 140.0, key=None)
    drag(bus, 150.0, 160.0)
    bus.undo()
    assert width(bus) == 140.0
    bus.undo()
    assert width(bus) == 130.0
    bus.undo()
    assert width(bus) == 120.0
    drag(bus, 125.0)  # after an undo the same key starts over
    bus.undo()
    assert width(bus) == 120.0
    bus.undo()
    assert width(bus) == 100.0


def test_dragging_back_to_the_start_leaves_no_step() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    drag(bus, 120.0, 100.0)
    assert bus.undo_label == "Create Rectangle"
    drag(bus, 130.0)
    assert bus.undo_label == "Change Width"
    bus.undo()
    assert width(bus) == 100.0


def test_a_merged_run_still_clears_redo() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    applied(bus.execute(rectangle()))
    bus.undo()
    drag(bus, 120.0, 100.0)
    assert bus.redo_label is None


# --- Undo stack size --------------------------------------------------------------------


def test_the_undo_stack_drops_the_oldest_entries_past_its_size_budget() -> None:
    bus = Bus(undo_bytes=600)
    for w in range(1, 20):
        applied(bus.execute(rectangle(width=float(w))))
    undone = 0
    while bus.undo():
        undone += 1
    assert 0 < undone < 19
    assert set(bus.document.entities) == {EntityId(f"e{n}") for n in range(1, 20 - undone)}


def test_the_newest_entry_is_kept_even_when_it_alone_is_over_budget() -> None:
    bus = Bus(undo_bytes=1)
    applied(bus.execute(rectangle()))
    applied(bus.execute(MoveEntities(ids=(E1,), dx=1.0, dy=1.0)))
    assert bus.undo_label == "Move Rectangle"
    bus.undo()
    assert bus.undo() is None


# --- Everything at once -----------------------------------------------------------------

widths = st.floats(min_value=0.5, max_value=500.0)


class BusHistory(RuleBasedStateMachine):
    """Random commands, undo/redo, merge keys, and nested transactions never corrupt history.

    After any sequence, with every transaction closed, undoing everything and redoing
    as many steps returns to the same document, and every snapshot reloads. Throughout, a
    subscriber that knows only the Changes it heard has the right document and labels.
    """

    @initialize()
    def start(self) -> None:
        self.bus = Bus(undo_limit=25)
        self.open: list[tuple[Transaction, Document]] = []
        self.mirror = self.bus.document
        self.heard: tuple[str | None, str | None] = (None, None)
        self.bus.subscribe(self.hear)

    def hear(self, change: Change) -> None:
        # How a view tracks the bus: apply each delta in turn, and read the labels now.
        self.mirror = apply(self.mirror, change.delta)
        self.heard = (self.bus.undo_label, self.bus.redo_label)

    def ids(self) -> list[EntityId]:
        return sorted(self.bus.document.entities)

    @rule(w=widths)
    def create(self, w: float) -> None:
        applied(self.bus.execute(rectangle(width=w)))

    @precondition(lambda self: bool(self.bus.document.entities))
    @rule(data=st.data(), w=widths, key=st.sampled_from([None, "drag", "other"]))
    def resize(self, data: st.DataObject, w: float, key: str | None) -> None:
        id = data.draw(st.sampled_from(self.ids()))
        applied(self.bus.execute(ModifyEntity(id=id, changes={"width": w}), merge_key=key))

    @precondition(lambda self: bool(self.bus.document.entities))
    @rule(data=st.data())
    def move_or_delete(self, data: st.DataObject) -> None:
        ids = tuple(data.draw(st.lists(st.sampled_from(self.ids()), min_size=1, unique=True)))
        if data.draw(st.booleans()):
            applied(self.bus.execute(MoveEntities(ids=ids, dx=1.0, dy=-2.0)))
        else:
            applied(self.bus.execute(DeleteEntities(ids=ids)))

    @precondition(lambda self: not self.open)
    @rule()
    def undo(self) -> None:
        self.bus.undo()

    @precondition(lambda self: not self.open)
    @rule()
    def redo(self) -> None:
        self.bus.redo()

    @precondition(lambda self: len(self.open) < 3)
    @rule(undoable=st.booleans())
    def begin(self, undoable: bool) -> None:
        transaction = self.bus.transaction("Batch", undoable=undoable)
        transaction.__enter__()
        self.open.append((transaction, self.bus.document))

    @precondition(lambda self: bool(self.open))
    @rule()
    def commit(self) -> None:
        transaction, _ = self.open.pop()
        transaction.__exit__(None, None, None)

    @precondition(lambda self: bool(self.open))
    @rule()
    def roll_back(self) -> None:
        transaction, start = self.open.pop()
        transaction.rollback()
        transaction.__exit__(None, None, None)
        assert self.bus.document == start

    @invariant()
    def snapshot_reloads(self) -> None:
        if hasattr(self, "bus"):
            assert snapshot.loads(snapshot.dumps(self.bus.document)) == self.bus.document

    @invariant()
    def subscribers_are_never_stale(self) -> None:
        if hasattr(self, "bus"):
            assert self.mirror == self.bus.document
            assert self.heard == (self.bus.undo_label, self.bus.redo_label)

    def teardown(self) -> None:
        if not hasattr(self, "bus"):
            return
        while self.open:
            self.commit()
        final = self.bus.document
        undone = 0
        while self.bus.undo():
            undone += 1
        for _ in range(undone):
            assert self.bus.redo() is not None
        assert self.bus.document == final


TestBusHistory = BusHistory.TestCase
