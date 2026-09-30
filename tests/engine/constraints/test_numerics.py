"""The solver's numerical model (V2.1): tolerances, the reference solver, keeping values a solve
didn't need to change, no drift, and a split cluster deciding only what it can decide exactly.

- `tolerance`: one policy, in the order the rules must keep.
- `sketch.reference()` is the solver as it was, the oracle the optimized one is compared with:
  it writes exactly the files `main` wrote before Performance V2 (pinned by their hashes).
- `_kept`: a value a solve changed by no more than `UNCHANGED` goes back to its stored value
  when every relation still holds without the change; never a change some relation needs.
- No drift: solving again, round trips of an edit, adding and removing a constraint, and
  accepting and undoing leave unchanged geometry exactly as it was.
- The fallback boundary: whatever a split cluster can't decide exactly as the whole sketch
  would (a failed solve, a relation near repeating the others, geometry near collapsing) the
  whole sketch decides, and a failure anywhere leaves the document as it was.
"""

import contextlib
import hashlib
import json
import math
import threading
from pathlib import Path

import pytest
from hypothesis import given

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateArc,
    CreateCircle,
    CreateConstraint,
    CreateDimension,
    CreatePoint,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    Rejected,
)
from caliper.contracts.document import (
    Arc,
    Circle,
    ConstraintType,
    Document,
    EntityId,
    Feature,
    Point2,
    RadialDimension,
    Ref,
)
from caliper.engine.commands import handlers
from caliper.engine.commands.bus import Bus
from caliper.engine.commands.handlers import Executed, already
from caliper.engine.constraints import equivalence, sketch, tolerance
from caliper.engine.io import snapshot
from caliper.engine.io.codec import COMMAND_KINDS, decode_command
from tests.engine.constraints.test_constraint_properties import PROPERTIES, sessions
from tests.engine.constraints.test_split_clusters import holding

SESSIONS = Path(__file__).resolve().parents[3] / "bench" / "sessions"
E1, E2, E3, E4 = EntityId("e1"), EntityId("e2"), EntityId("e3"), EntityId("e4")


def replay(name: str) -> Bus:
    """A recorded Claude Desktop session's commands (and undos) through a bus, as its draft
    ran them. Queries change nothing, so they're skipped."""
    bus = Bus(kernel=None)
    for call in json.loads((SESSIONS / f"{name}.json").read_text())["calls"]:
        tool, arguments = call["tool"], call["arguments"]
        if tool == "undo":
            bus.undo()
        elif tool in COMMAND_KINDS:
            bus.execute(decode_command({**arguments, "kind": tool}, tool))
    return bus


def circles(first: float, second: float) -> tuple[Document, sketch.System]:
    """Two circles with an equal constraint, and the system solving it."""
    bus = Bus(kernel=None)
    bus.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    bus.execute(CreateCircle(center=Point2(x=20, y=0), radius=5))
    curve = Feature.CURVE
    bus.execute(
        CreateConstraint(
            type=ConstraintType.EQUAL,
            refs=(
                Ref(entity=EntityId("e1"), feature=curve),
                Ref(entity=EntityId("e2"), feature=curve),
            ),
        )
    )
    entities = dict(bus.document.entities)
    entities[EntityId("e1")] = Circle(center=Point2(x=0, y=0), radius=first)
    entities[EntityId("e2")] = Circle(center=Point2(x=20, y=0), radius=second)
    document = Document(entities=entities, next_id=bus.document.next_id)
    (cluster,) = sketch.clusters(document)
    return document, sketch.System.build(document, cluster)


# --- The policy ----------------------------------------------------------------------------


def test_the_tolerances_keep_their_order_and_meaning() -> None:
    assert tolerance.SOLVED < tolerance.UNCHANGED < tolerance.BROKEN < tolerance.PRECISION
    assert tolerance.INDEPENDENT * tolerance.DECIDED < 1e-6  # a margin, not a new rule
    assert tolerance.scale([]) == 1.0
    assert tolerance.scale([0.5, -240.0]) == 240.0
    assert tolerance.solved([240.0]) == 1e-10 * 240.0  # the arithmetic the old solver used
    assert tolerance.broken([240.0]) == 1e3 * (1e-10 * 240.0)
    assert math.isclose(tolerance.PRECISION, math.sqrt(tolerance.SOLVED))


# --- The reference solver ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "digest"),
    [
        ("rectangle", "945ae9553849ef80baf9d708cdb29a0e0b172e6e0ce4d00cc2bd29ece5b468f0"),
        ("ball-bearing", "ac246d3e5403cdd36749041cc4e1dbcde3df216451416a9a47686ba9521d0247"),
    ],
)
def test_the_reference_writes_the_files_main_wrote(name: str, digest: str) -> None:
    """The hashes are of `main`'s own files (a394639), so the oracle is the old solver.
    Those were written at schema 2; only the version line has changed since (C-1)."""
    with sketch.reference():
        text = snapshot.dumps(replay(name).document)
    text = text.replace(f'"schema_version": {snapshot.SCHEMA_VERSION},', '"schema_version": 2,')
    assert hashlib.sha256(text.encode()).hexdigest() == digest


def test_the_reference_caches_nothing_and_stays_in_its_thread() -> None:
    document = replay("rectangle").document
    sketch._SOLVED.clear()
    sketch._GROUPS.clear()
    seen: list[list[sketch.Cluster]] = []
    with sketch.reference():
        whole = sketch.clusters(document)
        sketch.status(document)
        worker = threading.Thread(target=lambda: seen.append(sketch.clusters(document)))
        worker.start()
        worker.join()
    assert sketch._SOLVED.get(document) is None
    assert sketch._GROUPS.get(document) is None
    assert seen == [sketch.clusters(document)]  # the other thread solved as usual
    assert len(whole) < len(seen[0])  # the fixed origin splits the rectangle's cluster


def test_every_recorded_command_gives_what_the_reference_gives() -> None:
    """The optimized solver against the oracle, from the same document each time: the same
    outcome and status, and no difference beyond the solver's precision."""
    for name in ("rectangle", "ball-bearing"):
        bus = Bus(kernel=None)
        for call in json.loads((SESSIONS / f"{name}.json").read_text())["calls"]:
            tool, arguments = call["tool"], call["arguments"]
            if tool not in COMMAND_KINDS:
                continue
            command = decode_command({**arguments, "kind": tool}, tool)
            with sketch.reference():
                reference = Bus(bus.document, kernel=None)
                expected = reference.execute(command)
                expected_status = reference.queries.solve_status()
            actual = bus.execute(command)
            assert type(actual) is type(expected)
            assert bus.queries.solve_status() == expected_status
            found = equivalence.differences(reference.document, bus.document)
            assert not [d for d in found if d.significant], equivalence.report(found)


@PROPERTIES
@given(session=sessions())
def test_any_sketch_solves_as_the_reference_does(session: tuple[Bus, list[Command]]) -> None:
    """Random sketches without a fixed origin (clusters don't split): each command, from the
    same document, gives the reference's outcome and status and the same solution, keeping
    only values the reference would have changed by no more than `UNCHANGED`."""
    _, history = session
    bus = Bus(kernel=None)
    for index, command in enumerate(history):
        with sketch.reference():
            reference = Bus(bus.document, kernel=None)
            expected = reference.execute(command)
            expected_status = reference.queries.solve_status()
        actual = bus.execute(command)
        assert type(actual) is type(expected), (index, command)
        assert bus.queries.solve_status() == expected_status, (index, command)
        found = equivalence.differences(reference.document, bus.document)
        assert not [d for d in found if d.significant], (index, equivalence.report(found))
        assert holding(bus.document, reference.document) == [], (index, command)


# --- Keeping what a solve didn't need to change -------------------------------------------------


def test_a_tiny_change_no_relation_needs_goes_back_to_the_stored_value() -> None:
    _, system = circles(5.0, 5.0)
    solved = list(system.values)
    solved[system.slots[EntityId("e1")][2]] += 1e-12  # polishing moved a radius that was fine
    assert sketch._kept(system, solved) == system.values


def test_a_change_a_relation_needs_always_stays() -> None:
    _, system = circles(5.0, 5.0 + 1e-8)  # off by more than the solve tolerance (2e-9 here)
    solved = list(system.values)
    solved[system.slots[EntityId("e2")][2]] = 5.0  # within UNCHANGED, but needed
    assert sketch._kept(system, solved) == solved


def test_a_change_beyond_unchanged_is_never_put_back() -> None:
    _, system = circles(5.0, 5.0)
    solved = list(system.values)
    solved[system.slots[EntityId("e1")][0]] += 1e-6  # a centre moved: nothing reads it
    assert sketch._kept(system, solved) == solved


def test_the_largest_changes_a_failing_relation_reads_are_given_up_first() -> None:
    _, system = circles(5.0, 5.0 + 1.5e-8)
    radius_1, radius_2 = system.slots[EntityId("e1")][2], system.slots[EntityId("e2")][2]
    x = system.slots[EntityId("e1")][0]
    solved = list(system.values)
    solved[radius_1] = solved[radius_2] = 5.0 + 7.5e-9  # both moved to meet
    solved[x] += 1e-12  # and a centre picked up round-off
    kept = sketch._kept(system, solved)
    assert kept[x] == system.values[x]  # the round-off goes
    assert kept[radius_1] == kept[radius_2] == 5.0 + 7.5e-9  # the meeting stays


def test_a_solve_that_shrinks_the_sketch_keeps_nothing_its_new_size_forbids() -> None:
    """Found by the property tests. A centre dimensioned 1.000000001 from the origin is within
    tolerance of it while the circle's radius is 11 (scale 11). Driving the radius to 0.5
    tightens the tolerance tenfold, so the solve moves the centre, and that change must stay."""
    bus = Bus(kernel=None)
    origin, center = Ref(entity=E1, feature=Feature.POINT), Ref(entity=E4, feature=Feature.CENTER)
    for command in (
        CreatePoint(position=Point2(x=0, y=0), construction=True),
        CreateConstraint(type=ConstraintType.FIX, refs=(origin,)),
        CreatePoint(position=Point2(x=0, y=0)),
        CreateCircle(center=Point2(x=0, y=1), radius=11),
        CreateDimension(refs=(origin, center), placement=Point2(x=0, y=0), value=1.000000001),
        CreateDimension(
            refs=(Ref(entity=E4, feature=Feature.CURVE),), placement=Point2(x=0, y=0), value=1.0
        ),
    ):
        assert isinstance(bus.execute(command), Applied)
    for key, (residual, allowed) in sketch.residuals(bus.document).items():
        assert residual <= allowed, key


def test_keeping_values_never_turns_a_rejection_into_an_acceptance() -> None:
    """Found by the property tests. A horizontal constraint between an arc's start and middle
    squeezes it to a sweep of 1e-9 degrees; making a circle coincide with it then collapses
    it, and the solve fails. Keeping the old sweep would make the collapse vanish: decisions
    are taken on the solved values, and keeping changes only what's written."""
    commands: list[Command] = [
        CreateCircle(center=Point2(x=0, y=0), radius=1),
        CreateArc(center=Point2(x=0, y=0), radius=35, start_angle=0, sweep_angle=10),
        CreateConstraint(
            type=ConstraintType.HORIZONTAL,
            refs=(Ref(entity=E2, feature=Feature.START), Ref(entity=E2, feature=Feature.MID)),
        ),
        CreateConstraint(type=ConstraintType.FIX, refs=(Ref(entity=E1, feature=Feature.CENTER),)),
        CreateConstraint(
            type=ConstraintType.COINCIDENT,
            refs=(Ref(entity=E1, feature=Feature.CURVE), Ref(entity=E2, feature=Feature.CURVE)),
        ),
    ]
    outcomes = []
    for reference in (False, True):
        bus = Bus(kernel=None)
        with sketch.reference() if reference else contextlib.nullcontext():
            outcomes.append([type(bus.execute(command)) for command in commands])
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][-1] is Rejected


def test_kept_values_hold_at_the_scale_they_are_stored_at() -> None:
    """Found by the property tests. The arc's start angle solves to just past 360, stored as a
    few billionths of a degree: the stored sketch's scale is its sweep (166), not 360, and its
    tolerance half as wide. Values kept must hold at that."""
    origin = Ref(entity=E1, feature=Feature.POINT)
    commands: list[Command] = [
        CreatePoint(position=Point2(x=0, y=0), construction=True),
        CreateConstraint(type=ConstraintType.FIX, refs=(origin,)),
        CreateRectangle(corner=Point2(x=0, y=0), width=100, height=1),
        CreateArc(center=Point2(x=0, y=46), radius=51, start_angle=0, sweep_angle=166),
        CreateConstraint(
            type=ConstraintType.COINCIDENT,
            refs=(Ref(entity=E3, feature=Feature.RIGHT), Ref(entity=E4, feature=Feature.START)),
        ),
        CreateDimension(
            refs=(Ref(entity=E4, feature=Feature.MID), origin),
            placement=Point2(x=0, y=0),
            value=111.28609164434772,
        ),
        CreateDimension(
            refs=(Ref(entity=E4, feature=Feature.CENTER), origin),
            placement=Point2(x=0, y=0),
            value=67.20505851292555,
        ),
    ]
    bus = Bus(kernel=None)
    for command in commands:
        with sketch.reference():
            reference = Bus(bus.document, kernel=None)
            reference.execute(command)
        assert isinstance(bus.execute(command), Applied)
        assert holding(bus.document, reference.document) == [], command


def test_values_that_cant_be_evaluated_are_left_as_solved(monkeypatch: pytest.MonkeyPatch) -> None:
    _, system = circles(5.0, 5.0)
    solved = list(system.values)
    solved[system.slots[EntityId("e1")][2]] += 1e-12

    def fails(*args: object) -> object:
        raise ZeroDivisionError

    monkeypatch.setattr(sketch, "_evaluate", fails)
    assert sketch._kept(system, solved) == solved


def test_values_claude_typed_that_already_held_stay_as_typed() -> None:
    """In the ball bearing, the balls' radius: `main` stored 1.5000000000000004. In the
    stress plate, the D cutout's arc, which `main` rewrote whenever it solved the star."""
    bearing = replay("ball-bearing").document
    radii = [e.radius for e in bearing.entities.values() if isinstance(e, Circle)]
    assert 1.5 in radii
    assert not [r for r in radii if r != 1.5 and abs(r - 1.5) < 1e-9]
    plate = replay("stress-plate-build").document
    arcs = [e for e in plate.entities.values() if isinstance(e, Arc)]
    assert any(a.start_angle == 22.619865 and a.sweep_angle == 134.76027 for a in arcs)


def test_a_stage_that_cannot_be_solved_stops_when_its_steps_no_longer_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In the ball bearing, making two balls equal first tries moving only the second, which
    the other constraints forbid. Kept values leave residuals just within tolerance elsewhere,
    and Newton used to lower them by a part in a billion a step for 14 steps (241 evaluations)
    before giving up. Steps that small can't solve anything, so the stage stops at once."""
    calls = json.loads((SESSIONS / "ball-bearing.json").read_text())["calls"]
    commands = [
        decode_command({**c["arguments"], "kind": c["tool"]}, c["tool"])
        for c in calls
        if c["tool"] in COMMAND_KINDS
    ]
    bus = Bus(kernel=None)
    for command in commands[:-6]:
        bus.execute(command)
    evaluations = [0]
    real = sketch._evaluate

    def counted(*args: object) -> object:
        evaluations[0] += 1
        return real(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(sketch, "_evaluate", counted)
    worst = 0
    for command in commands[-6:]:  # the balls made equal, one pair at a time
        evaluations[0] = 0
        assert isinstance(bus.execute(command), Applied)
        worst = max(worst, evaluations[0])
    assert worst < 60  # the old solver needed up to 38 here; the crawl, 263


# --- No drift -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plate() -> Document:
    return replay("stress-plate-build").document


def test_solving_the_same_document_again_changes_nothing(plate: Document) -> None:
    relations = frozenset(id for id, e in plate.entities.items() if sketch.is_relation(e))
    document = plate
    for _ in range(5):
        solved = sketch.settle(document, document, sketch.Request(touched=relations))
        assert solved is document
    with sketch.reference():
        assert sketch.settle(plate, plate, sketch.Request(touched=relations)) is plate


def test_an_edit_and_back_many_times_returns_exactly(plate: Document) -> None:
    fillet = min(
        (id for id, e in plate.entities.items() if isinstance(e, RadialDimension) and e.value),
        key=lambda id: int(id[1:]),
    )
    value = plate.entities[fillet].value
    assert value is not None
    bus = Bus(plate, kernel=None)
    for n in range(20):
        result = bus.execute(ModifyEntity(id=fillet, changes={"value": value * (1.5, 1.0)[n % 2]}))
        assert isinstance(result, Applied)
    found = equivalence.differences(plate, bus.document)
    assert found == [], equivalence.report(found)  # not even 5e-63 where a 0 was


def test_editing_one_feature_leaves_every_other_cluster_untouched(plate: Document) -> None:
    tiny = min(
        (id for id, e in plate.entities.items() if isinstance(e, Circle)),
        key=lambda id: plate.entities[id].radius,  # type: ignore[union-attr]
    )
    (diameter,) = (
        id
        for id, e in plate.entities.items()
        if isinstance(e, RadialDimension) and e.target == tiny and e.value is not None
    )
    where = sketch.grouped(plate)
    own = where[tiny]
    bus = Bus(plate, kernel=None)
    for n in range(10):
        result = bus.execute(ModifyEntity(id=diameter, changes={"value": 0.2 + 0.01 * n}))
        assert isinstance(result, Applied)
    for id, entity in plate.entities.items():
        if where.get(id) is not own and id not in (tiny, diameter):
            assert bus.document.entities[id] is entity, id


def test_adding_and_removing_a_constraint_that_holds_changes_nothing() -> None:
    bus = Bus(kernel=None)
    bus.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    bus.execute(CreateCircle(center=Point2(x=20, y=0), radius=5))
    start = dict(bus.document.entities)
    curves = (
        Ref(entity=EntityId("e1"), feature=Feature.CURVE),
        Ref(entity=EntityId("e2"), feature=Feature.CURVE),
    )
    for _ in range(10):
        added = bus.execute(CreateConstraint(type=ConstraintType.EQUAL, refs=curves))
        assert isinstance(added, Applied)
        assert isinstance(bus.execute(DeleteEntities(ids=added.created_ids)), Applied)
    assert all(bus.document.entities[id] is entity for id, entity in start.items())


def test_accepting_and_undoing_again_and_again_gives_the_same_documents() -> None:
    commands: list[Command] = []
    source = Bus(kernel=None)
    for call in json.loads((SESSIONS / "ball-bearing.json").read_text())["calls"]:
        if call["tool"] in COMMAND_KINDS:
            commands.append(
                decode_command({**call["arguments"], "kind": call["tool"]}, call["tool"])
            )
    base = source.document
    steps = []
    for command in commands:
        result = source.execute(command)
        if isinstance(result, Applied) and (result.delta.before or result.delta.after):
            steps.append(result)
    executed = Executed(base=base, steps=tuple(steps), result=source.document)
    for _ in range(3):  # the same proposal, accepted onto the same sketch each time
        target = Bus(base, kernel=None)
        with already(executed), target.transaction("Accept"):
            for step in steps:
                target.execute(step.command)
        assert target.document is source.document
        target.undo()
        assert target.document == base
        target.redo()
        assert target.document == source.document


# --- The fallback boundary, and failing safely ---------------------------------------------


PLATE_STEPS = 60
"""Enough of the stress plate for anchored clusters and new relations: the origin, the outline
and its fillets, and the slots."""


def plate_run() -> tuple[list[tuple[object, ...]], Document]:
    """The first commands of the stress plate: each one's outcome and status, and the result."""
    bus = Bus(kernel=None)
    steps: list[tuple[object, ...]] = []
    calls = json.loads((SESSIONS / "stress-plate-build.json").read_text())["calls"]
    commands = [
        decode_command({**c["arguments"], "kind": c["tool"]}, c["tool"])
        for c in calls
        if c["tool"] in COMMAND_KINDS
    ]
    for command in commands[:PLATE_STEPS]:
        result = bus.execute(command)
        errors = result.errors if isinstance(result, Rejected) else ()
        steps.append((type(result), errors, bus.queries.solve_status()))
    return steps, bus.document


def _deferrals(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """For each split solve, whether it left the decision to the whole sketch."""
    deferred: list[bool] = []
    real = sketch._settle

    def recorded(*args: object, local: bool, keep: bool) -> object:
        outcome = real(*args, local=local, keep=keep)  # type: ignore[arg-type]
        if local:
            deferred.append(outcome is None)
        return outcome

    monkeypatch.setattr(sketch, "_settle", recorded)
    return deferred


def test_decisions_near_a_threshold_are_the_whole_sketchs(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a margin so wide that every new relation is close to repeating the others and
    every curve close to collapsing, the split solves defer, and nothing else changes."""
    expected, document = plate_run()
    monkeypatch.setattr(tolerance, "DECIDED", 1e12)
    deferred = _deferrals(monkeypatch)
    steps, deciding = plate_run()
    assert steps == expected
    assert sum(deferred) > PLATE_STEPS // 2
    found = equivalence.differences(document, deciding)
    assert not [d for d in found if d.significant], equivalence.report(found)


def test_a_split_solve_that_fails_is_decided_by_the_whole_sketch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, document = plate_run()
    real = sketch._attempt

    def fails_when_split(system: sketch.System, request: sketch.Request, **kw: bool) -> object:
        return None if not kw.get("nudge", True) else real(system, request, **kw)

    monkeypatch.setattr(sketch, "_attempt", fails_when_split)
    deferred = _deferrals(monkeypatch)
    steps, deciding = plate_run()
    assert steps == expected
    assert deferred
    assert all(deferred)
    found = equivalence.differences(document, deciding)
    assert not [d for d in found if d.significant], equivalence.report(found)


def test_a_failure_during_accept_leaves_the_document_as_it_was() -> None:
    source = replay("rectangle")
    base = Bus(kernel=None).document
    steps = []
    bus = Bus(kernel=None)
    for call in json.loads((SESSIONS / "rectangle.json").read_text())["calls"]:
        if call["tool"] in COMMAND_KINDS:
            result = bus.execute(
                decode_command({**call["arguments"], "kind": call["tool"]}, call["tool"])
            )
            assert isinstance(result, Applied)
            steps.append(result)
    executed = Executed(base=base, steps=tuple(steps), result=bus.document)
    target = Bus(base, kernel=None)

    def accept_and_fail() -> None:
        with already(executed), target.transaction("Accept"):
            target.execute(steps[0].command)
            raise RuntimeError("the machine went away")

    with pytest.raises(RuntimeError):
        accept_and_fail()
    assert target.document is base
    assert target.undo_label is None
    assert handlers._REPLAYS == []
    assert source.document == bus.document


def test_a_rejected_command_leaves_the_document_as_it_was() -> None:
    bus = replay("rectangle")
    before = bus.document
    result = bus.execute(ModifyEntity(id=EntityId("e1"), changes={"width": 90.0}))  # held at 100
    assert isinstance(result, Rejected)
    assert bus.document is before
