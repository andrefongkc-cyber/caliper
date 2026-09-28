"""The Performance V2 shortcuts give exactly the answers of the long way round.

- `already`: running commands a bus already ran, from the same document, commits their
  outcomes without solving them again, and anything else still runs.
- `RowBasis` skips products it can prove are zero: the results are bit-for-bit those of the
  dense factorization it replaced (kept here as the reference).
- `referrers`, kept from the last document, always equals a scan of every entity.
- `check` answers from its cache only while everything it reads is unchanged.
- The caches kept for recent documents are safe to share between threads.
"""

import math
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from operator import mul

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateCircle,
    CreateConstraint,
    CreateRadialDimension,
    ModifyEntity,
)
from caliper.contracts.document import (
    ConstraintType,
    Document,
    EntityId,
    Feature,
    Point2,
    RadialMeasure,
    Ref,
)
from caliper.contracts.queries import Expectation, Metric
from caliper.engine import queries
from caliper.engine.commands import handlers
from caliper.engine.commands.bus import Bus
from caliper.engine.commands.handlers import Executed, already
from caliper.engine.constraints import sketch
from caliper.engine.constraints.linalg import RowBasis
from caliper.engine.document.recent import Recent
from tests.engine.constraints.test_constraint_properties import PROPERTIES, sessions

# --- Committing what already ran ----------------------------------------------------------


def _ran(commands: list[Command]) -> tuple[Bus, Executed]:
    bus = Bus(kernel=None)
    base = bus.document
    steps = []
    for command in commands:
        result = bus.execute(command)
        assert isinstance(result, Applied)
        steps.append(result)
    return bus, Executed(base=base, steps=tuple(steps), result=bus.document)


E1 = EntityId("e1")

COMMANDS: list[Command] = [
    CreateCircle(center=Point2(x=0, y=0), radius=5),
    CreateCircle(center=Point2(x=20, y=0), radius=3),
    CreateConstraint(
        type=ConstraintType.EQUAL,
        refs=(
            Ref(entity=EntityId("e1"), feature=Feature.CURVE),
            Ref(entity=EntityId("e2"), feature=Feature.CURVE),
        ),
    ),
    CreateRadialDimension(
        target=EntityId("e1"), measure=RadialMeasure.DIAMETER, label_angle=0, value=8
    ),
]


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[Command]:
    """Commands that really ran: validated, applied, and solved."""
    ran: list[Command] = []
    real = handlers._apply
    monkeypatch.setattr(handlers, "_apply", lambda d, c: ran.append(c) or real(d, c))
    return ran


def test_commands_that_already_ran_are_committed_without_being_solved_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, executed = _ran(COMMANDS)
    ran = _counting(monkeypatch)
    target = Bus(executed.base, kernel=None)
    with already(executed), target.transaction("Accept"):
        for step in executed.steps:
            result = target.execute(step.command)
            assert isinstance(result, Applied)
            assert result.delta == step.delta
            assert result.created_ids == step.created_ids
    assert ran == []  # nothing validated or solved a second time
    assert target.document is source.document  # the very document the workspace built
    assert target.undo_label == "Accept"
    assert handlers._REPLAYS == []  # nothing left behind
    target.undo()
    assert target.document == executed.base


def test_an_equal_document_runs_as_usual(monkeypatch: pytest.MonkeyPatch) -> None:
    _, executed = _ran(COMMANDS)
    ran = _counting(monkeypatch)
    copy = Bus(Document(entities=dict(executed.base.entities), next_id=1), kernel=None)
    with already(executed):
        for step in executed.steps:
            copy.execute(step.command)
    assert len(ran) == len(COMMANDS)  # equal isn't the same: every command really ran
    assert copy.document == executed.result


def test_after_a_command_that_wasnt_recorded_the_rest_runs_as_usual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, executed = _ran(COMMANDS)
    ran = _counting(monkeypatch)
    target = Bus(executed.base, kernel=None)
    first, second, *rest = executed.steps
    with already(executed):
        target.execute(first.command)  # committed
        target.execute(replace(second.command))  # equal, but not the command that ran
        for step in rest:
            target.execute(step.command)
    assert len(ran) == len(COMMANDS) - 1
    assert target.document == executed.result


# --- The sparse factorization is the dense one ----------------------------------------------


@dataclass
class DenseBasis:
    """`RowBasis.add` and `step` as they were, taking every product."""

    width: int
    q: list[list[float]] = field(default_factory=list)
    lower: list[list[float]] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)
    dependent: dict[int, list[float]] = field(default_factory=dict)

    def add(self, index: int, row: Sequence[float]) -> bool:
        size = _norm(row)
        v = list(row)
        c = [0.0] * len(self.q)
        if size > 1e-14:
            for _ in range(2):
                for j, qj in enumerate(self.q):
                    p = sum(map(mul, v, qj))
                    if p != 0.0:
                        c[j] += p
                        v = [vi - p * qji for vi, qji in zip(v, qj, strict=True)]
        rest = _norm(v)
        if size > 1e-14 and rest > 1e-9 * size:
            self.q.append([x / rest for x in v])
            self.lower.append([*c, rest])
            self.kept.append(index)
            return True
        self.dependent[index] = RowBasis._in_kept_rows(self, c) if size > 1e-14 else [0.0] * len(c)  # type: ignore[arg-type]
        return False

    def step(self, residuals: Sequence[float]) -> list[float]:
        k = len(self.kept)
        z = [0.0] * k
        for i in range(k):
            total = -residuals[self.kept[i]]
            row = self.lower[i]
            for j in range(i):
                total -= row[j] * z[j]
            z[i] = total / row[i]
        delta = [0.0] * self.width
        for zi, qi in zip(z, self.q, strict=True):
            if zi != 0.0:
                for j, qij in enumerate(qi):
                    delta[j] += zi * qij
        return delta


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(map(mul, v, v)))


@st.composite
def sparse_rows(draw: st.DrawFn) -> tuple[int, list[list[float]]]:
    width = draw(st.integers(min_value=1, max_value=24))
    value = st.one_of(st.just(0.0), st.floats(min_value=-10, max_value=10, allow_subnormal=False))
    rows = draw(st.lists(st.lists(value, min_size=width, max_size=width), min_size=1, max_size=30))
    # Repeat some rows and combinations of rows, so dependent rows are common, as in sketches.
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        a, b = draw(st.sampled_from(rows)), draw(st.sampled_from(rows))
        rows.append([x + 2.0 * y for x, y in zip(a, b, strict=True)])
    return width, rows


@given(case=sparse_rows())
def test_the_sparse_factorization_is_bit_for_bit_the_dense_one(
    case: tuple[int, list[list[float]]],
) -> None:
    width, rows = case
    fast, slow = RowBasis(width), DenseBasis(width)
    for i, row in enumerate(rows):
        assert fast.add(i, row) == slow.add(i, row)
    assert fast.kept == slow.kept
    assert fast.lower == slow.lower
    assert fast.dependent == slow.dependent
    for a, b in zip(fast.q, slow.q, strict=True):
        assert [x == y for x, y in zip(a, b, strict=True)] == [True] * width
    residuals = [float(i % 7) - 3.0 for i in range(len(rows))]
    assert fast.step(residuals) == slow.step(residuals)


# --- The index of what refers to what -------------------------------------------------------


def _scanned(bus: Bus) -> dict[EntityId, frozenset[EntityId]]:
    found: dict[EntityId, set[EntityId]] = {}
    for id, entity in bus.document.entities.items():
        for ref in sketch.references(entity):
            found.setdefault(ref.entity, set()).add(id)
    return {k: frozenset(v) for k, v in found.items()}


@PROPERTIES
@given(session=sessions())
def test_referrers_kept_from_the_last_document_match_a_full_scan(session) -> None:
    _, history = session
    bus = Bus(kernel=None)
    for command in history:
        bus.execute(command)
        assert sketch.referrers(bus.document) == _scanned(bus)
    while bus.undo() is not None:
        assert sketch.referrers(bus.document) == _scanned(bus)
    while bus.redo() is not None:
        assert sketch.referrers(bus.document) == _scanned(bus)


# --- Cached checks ----------------------------------------------------------------------------


def test_a_check_is_measured_again_only_when_what_it_reads_changes(monkeypatch) -> None:
    bus = Bus(kernel=None)
    bus.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    bus.execute(CreateCircle(center=Point2(x=20, y=0), radius=3))
    width = Expectation(
        metric=Metric.BBOX_WIDTH, expected=10.0, tolerance=1e-6, ids=(EntityId("e1"),)
    )
    measured: list[Expectation] = []
    real = queries.DocumentQueries._evaluate
    monkeypatch.setattr(
        queries.DocumentQueries,
        "_evaluate",
        lambda self, e: measured.append(e) or real(self, e),
    )
    assert bus.queries.check(width).passed
    assert bus.queries.check(width).passed
    bus.execute(ModifyEntity(id=EntityId("e2"), changes={"radius": 4.0}))  # not what it reads
    assert bus.queries.check(width).passed
    assert len(measured) == 1
    bus.execute(ModifyEntity(id=EntityId("e1"), changes={"radius": 6.0}))  # what it reads
    result = bus.queries.check(width)
    assert not result.passed
    assert result.actual == 12.0
    assert len(measured) == 2


def test_a_check_of_everything_is_never_answered_from_the_cache() -> None:
    bus = Bus(kernel=None)
    bus.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    everything = Expectation(metric=Metric.BBOX_WIDTH, expected=10.0, tolerance=1e-6)
    assert bus.queries.check(everything).passed
    bus.execute(CreateCircle(center=Point2(x=20, y=0), radius=3))
    assert bus.queries.check(everything).actual == 28.0


# --- Shared between threads ------------------------------------------------------------------


def test_recent_keeps_the_last_few_documents_by_identity() -> None:
    recent: Recent[str] = Recent(2)
    a, b, c = (Document(entities={}, next_id=n) for n in (1, 2, 3))
    assert recent.latest() is None
    recent.put(a, "a")
    recent.put(b, "b")
    assert recent.get(a) == "a"  # used: now the most recent
    assert recent.get(Document(entities={}, next_id=1)) is None  # equal isn't the same
    recent.put(c, "c")  # b, used least recently, goes
    assert recent.get(b) is None
    assert recent.latest() == (c, "c")


def test_the_solver_caches_can_be_shared_between_threads() -> None:
    """The in-app assistant's tool calls run on a worker thread while the window asks about
    its own document: both use the same caches, and neither may break the other."""
    failures: list[BaseException] = []
    width = Expectation(metric=Metric.BBOX_WIDTH, expected=10.0, tolerance=1e-6, ids=(E1,))

    def work(offset: float) -> None:
        try:
            bus = Bus(kernel=None)
            for n in range(40):
                bus.execute(CreateCircle(center=Point2(x=offset + n, y=0), radius=5))
                bus.execute(
                    CreateConstraint(
                        type=ConstraintType.EQUAL,
                        refs=(
                            Ref(entity=E1, feature=Feature.CURVE),
                            Ref(
                                entity=EntityId(f"e{bus.document.next_id - 1}"),
                                feature=Feature.CURVE,
                            ),
                        ),
                    )
                )
                bus.queries.solve_status()
                bus.queries.check(width)
                sketch.referrers(bus.document)
        except BaseException as e:  # reported below, not lost in the thread
            failures.append(e)

    threads = [threading.Thread(target=work, args=(100.0 * i,)) for i in range(4)]
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible, to meet any race
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(interval)
    assert failures == []
