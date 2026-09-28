"""Anchored geometry splits clusters: what's dimensioned from a fixed origin solves on its own.

A Fix that pins every parameter of an entity (a point, or a whole curve) means it never moves,
so two features dimensioned from it aren't coupled through it. Splitting there is only an
optimization: for any sketch, the split clusters must accept and reject the same commands, with
the same messages, and report the same degrees of freedom, as the whole clusters did, with
solved positions equal to within the solver's precision (see `close`). The whole clusters are
what `sketch.anchored` returning nothing gives.
"""

import math
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateCircle,
    CreateConstraint,
    CreateDimension,
    CreateDistanceDimension,
    CreateLine,
    CreatePoint,
    CreateRadialDimension,
    ModifyEntity,
    MoveEntities,
    Rejected,
)
from caliper.contracts.document import (
    ConstraintType,
    DistanceOrientation,
    Document,
    EntityId,
    Feature,
    Point2,
    RadialMeasure,
    Ref,
)
from caliper.engine import queries
from caliper.engine.commands.bus import Bus
from caliper.engine.constraints import sketch
from caliper.engine.io import codec
from tests.engine.constraints.test_constraint_properties import (
    PROPERTIES,
    coordinate,
    features,
    geometry,
    points,
    size,
)

E1 = EntityId("e1")


def origin(bus: Bus) -> None:
    bus.execute(CreatePoint(position=Point2(x=0, y=0), construction=True))
    bus.execute(
        CreateConstraint(type=ConstraintType.FIX, refs=(Ref(entity=E1, feature=Feature.POINT),))
    )


def placed(bus: Bus, id: EntityId, x: float, y: float) -> None:
    """Dimension `id`'s centre from the origin, both ways."""
    for orientation, value in (
        (DistanceOrientation.HORIZONTAL, x),
        (DistanceOrientation.VERTICAL, y),
    ):
        result = bus.execute(
            CreateDistanceDimension(
                a=Ref(entity=E1, feature=Feature.POINT),
                b=Ref(entity=id, feature=Feature.CENTER),
                orientation=orientation,
                offset=5.0,
                value=value,
            )
        )
        assert isinstance(result, Applied), result


@contextmanager
def whole() -> Iterator[None]:
    """Solve as before splitting: nothing counts as anchored."""
    real = sketch.anchored
    sketch.anchored = lambda document: frozenset()  # type: ignore[assignment]
    _forget()
    try:
        yield
    finally:
        sketch.anchored = real  # type: ignore[assignment]
        _forget()


def _forget() -> None:
    sketch._GROUPS.clear()
    sketch._LAST.clear()
    queries._STATUS.clear()
    queries._CHECKS.clear()


# --- What splits, and what doesn't ------------------------------------------------------


def test_features_dimensioned_from_a_fixed_origin_are_separate_clusters() -> None:
    bus = Bus(kernel=None)
    origin(bus)
    (a,) = bus.execute(CreateCircle(center=Point2(x=10, y=10), radius=2)).created_ids  # type: ignore[union-attr]
    (b,) = bus.execute(CreateCircle(center=Point2(x=50, y=10), radius=2)).created_ids  # type: ignore[union-attr]
    placed(bus, a, 10, 10)
    placed(bus, b, 50, 10)
    found = sketch.clusters(bus.document)
    assert [c.geometry for c in found] == [(E1,), (a,), (b,)]
    assert [c.fixed for c in found] == [(), (E1,), (E1,)]
    assert found[0].relations == ("e2",)  # the origin's cluster holds its Fix
    with whole():
        assert [c.geometry for c in sketch.clusters(bus.document)] == [(E1, a, b)]


def test_a_solve_leaves_features_that_only_share_the_origin_alone(monkeypatch) -> None:
    bus = Bus(kernel=None)
    origin(bus)
    (a,) = bus.execute(CreateCircle(center=Point2(x=10, y=10), radius=2)).created_ids  # type: ignore[union-attr]
    (b,) = bus.execute(CreateCircle(center=Point2(x=50, y=10), radius=2)).created_ids  # type: ignore[union-attr]
    placed(bus, a, 10, 10)
    placed(bus, b, 50, 10)
    sizes: list[int] = []
    build = sketch.System.build

    def recorded(
        document: Document, cluster: sketch.Cluster, before: Document | None = None
    ) -> sketch.System:
        system = build(document, cluster, before)
        sizes.append(len(system.unknowns))
        return system

    monkeypatch.setattr(sketch.System, "build", staticmethod(recorded))
    bus.execute(
        CreateRadialDimension(target=b, measure=RadialMeasure.DIAMETER, label_angle=45, value=6)
    )
    assert sizes
    assert max(sizes) == 3  # circle b alone: centre and radius, the origin a constant


@pytest.mark.parametrize(
    ("feature", "anchored"),
    [(Feature.CURVE, True), (Feature.START, False), (Feature.MID, False)],
)
def test_only_geometry_fixed_in_every_parameter_is_anchored(
    feature: Feature, anchored: bool
) -> None:
    bus = Bus(kernel=None)
    (line,) = bus.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=10, y=0))).created_ids  # type: ignore[union-attr]
    bus.execute(
        CreateConstraint(type=ConstraintType.FIX, refs=(Ref(entity=line, feature=feature),))
    )
    assert (line in sketch.anchored(bus.document)) is anchored


def test_both_ends_fixed_anchor_a_line() -> None:
    bus = Bus(kernel=None)
    (line,) = bus.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=10, y=0))).created_ids  # type: ignore[union-attr]
    for end in (Feature.START, Feature.END):
        bus.execute(
            CreateConstraint(type=ConstraintType.FIX, refs=(Ref(entity=line, feature=end),))
        )
    assert line in sketch.anchored(bus.document)


def test_moving_fixed_geometry_is_refused_exactly_as_before() -> None:
    bus = Bus(kernel=None)
    origin(bus)
    (a,) = bus.execute(CreateCircle(center=Point2(x=10, y=10), radius=2)).created_ids  # type: ignore[union-attr]
    placed(bus, a, 10, 10)
    split = bus.execute(MoveEntities(ids=(E1,), dx=5, dy=0))
    with whole():
        before = Bus(kernel=None)
        origin(before)
        before.execute(CreateCircle(center=Point2(x=10, y=10), radius=2))
        placed(before, a, 10, 10)
        unsplit = before.execute(MoveEntities(ids=(E1,), dx=5, dy=0))
    assert isinstance(split, Rejected)
    assert isinstance(unsplit, Rejected)
    assert split.errors == unsplit.errors


def test_a_degenerate_start_moves_the_way_it_always_did() -> None:
    """A point on the origin itself, then dimensioned 1 from it: no direction is better than
    another, so the solver nudges it. Where the nudge points depends on the system's unknowns,
    so the whole sketch does the nudging, as it always has."""
    commands: list[Command] = [
        CreatePoint(position=Point2(x=0, y=0), construction=True),
        CreateConstraint(type=ConstraintType.FIX, refs=(Ref(entity=E1, feature=Feature.POINT),)),
        CreatePoint(position=Point2(x=0, y=0)),
        CreateDistanceDimension(
            a=Ref(entity=E1, feature=Feature.POINT),
            b=Ref(entity=EntityId("e3"), feature=Feature.POINT),
            orientation=DistanceOrientation.ALIGNED,
            offset=5.0,
            value=1.0,
        ),
    ]
    _forget()
    split = replay(commands)
    with whole():
        unsplit = replay(commands)
    assert close(split, unsplit)


def test_a_tangential_solve_agrees_to_the_solvers_precision() -> None:
    """The aligned and horizontal distances to e3's end meet tangentially where it ends up
    (y = 0), so it stops just short of that, and split and whole stop a step apart."""
    commands: list[Command] = [
        CreatePoint(position=Point2(x=0, y=0), construction=True),
        CreateConstraint(type=ConstraintType.FIX, refs=(Ref(entity=E1, feature=Feature.POINT),)),
        CreateCircle(center=Point2(x=0, y=0), radius=2),  # makes the whole sketch larger
        CreateDimension(
            refs=(
                Ref(entity=E1, feature=Feature.POINT),
                Ref(entity=EntityId("e3"), feature=Feature.CENTER),
            ),
            placement=Point2(x=0, y=0),
            value=1.0,
        ),
        CreateLine(start=Point2(x=0, y=0), end=Point2(x=1, y=0.5)),
    ]
    end = Ref(entity=EntityId("e5"), feature=Feature.END)
    for orientation in (DistanceOrientation.ALIGNED, DistanceOrientation.HORIZONTAL):
        commands.append(
            CreateDistanceDimension(
                a=Ref(entity=E1, feature=Feature.POINT),
                b=end,
                orientation=orientation,
                offset=5.0,
                value=1.0,
            )
        )
    _forget()
    split = replay(commands)
    with whole():
        unsplit = replay(commands)
    assert [step[:2] for step in split] == [step[:2] for step in unsplit]
    assert split[-1][0][0] == "applied"
    assert close(split, unsplit)
    y = split[-1][2]["e5"]["end"]["y"]  # type: ignore[index]
    assert abs(y) < 1e-4  # on the root, to the solver's precision


# --- Split and whole agree on everything ------------------------------------------------


@st.composite
def anchored_sessions(draw: st.DrawFn) -> list[Command]:
    """Commands on a sketch with a fixed origin, features dimensioned from it (as Claude draws
    them), and then random constraints, dimensions, edits, and moves, as the property tests
    make them. Every command is kept, accepted or not, to be replayed both ways."""
    bus = Bus(kernel=None)
    commands: list[Command] = []

    def attempt(command: Command) -> None:
        commands.append(command)
        bus.execute(command)

    attempt(CreatePoint(position=Point2(x=0, y=0), construction=True))
    attempt(
        CreateConstraint(type=ConstraintType.FIX, refs=(Ref(entity=E1, feature=Feature.POINT),))
    )
    for command in draw(st.lists(geometry(), min_size=2, max_size=4)):
        attempt(command)
    for _ in range(draw(st.integers(min_value=2, max_value=10))):
        refs = features(bus)
        geometry_ids = sorted({r.entity for r in refs if r.entity != E1})
        if not geometry_ids:
            break
        match draw(st.sampled_from(["from-origin", "constraint", "dimension", "edit", "move"])):
            case "from-origin":
                target = draw(st.sampled_from([r for r in refs if r.entity != E1]))
                if target.feature is Feature.CURVE:
                    continue
                attempt(
                    CreateDistanceDimension(
                        a=Ref(entity=E1, feature=Feature.POINT),
                        b=target,
                        orientation=draw(st.sampled_from(list(DistanceOrientation))),
                        offset=5.0,
                        value=draw(st.one_of(st.none(), size)),
                    )
                )
            case "constraint":
                count = draw(st.sampled_from([1, 2, 2]))
                if len(refs) < count:
                    continue
                chosen = draw(
                    st.lists(st.sampled_from(refs), min_size=count, max_size=count, unique=True)
                )
                fits = [
                    o.type
                    for o in bus.queries.applicable_constraints(chosen)
                    if o.error is None and isinstance(o.type, ConstraintType)
                ]
                if fits:
                    attempt(CreateConstraint(type=draw(st.sampled_from(fits)), refs=tuple(chosen)))
            case "dimension":
                chosen = draw(st.lists(st.sampled_from(refs), min_size=1, max_size=2, unique=True))
                attempt(
                    CreateDimension(refs=tuple(chosen), placement=draw(points), value=draw(size))
                )
            case "edit":
                id = draw(st.sampled_from(geometry_ids))
                entity = bus.document.entities[id]
                field = draw(
                    st.sampled_from([f for f in type(entity).__slots__ if f != "construction"])
                )
                current = getattr(entity, field)
                if isinstance(current, Point2):
                    attempt(ModifyEntity(id=id, changes={field: draw(points)}))
                elif isinstance(current, float):
                    attempt(ModifyEntity(id=id, changes={field: draw(size)}))
            case _:
                id = draw(st.sampled_from(geometry_ids))
                attempt(MoveEntities(ids=(id,), dx=draw(coordinate), dy=draw(coordinate)))
    return commands


def step(bus: Bus, command: Command) -> tuple[object, ...]:
    """Run `command`: its outcome, the status after it, and the geometry, comparably."""
    result = bus.execute(command)
    status = bus.queries.solve_status()
    outcome: object
    if isinstance(result, Rejected):
        outcome = ("rejected", tuple((e.code, e.message, e.ids) for e in result.errors))
    else:
        assert isinstance(result, Applied)
        outcome = ("applied", result.created_ids)
    return (
        outcome,
        (status.state, status.dof, dict(status.entity_dof), status.conflicting, status.redundant),
        codec.encode(bus.document.entities),
    )


def replay(commands: list[Command]) -> list[tuple[object, ...]]:
    """Each command's `step`, one after another."""
    bus = Bus(kernel=None)
    return [step(bus, command) for command in commands]


def close(a: object, b: object) -> bool:
    """Equal, with floats equal to within the solver's precision.

    That is round-off almost everywhere. But where two constraints meet tangentially (a double
    root: an aligned and a horizontal distance of 1 to the same point) Newton closes in slowly
    and stops once within its tolerance, which fixes the point only to about the tolerance's
    square root. The tolerance scales with the largest value in the system solved, which for a
    split cluster is its own geometry and for the whole sketch everything, so the two can stop
    a step apart: a millionth of a millimetre, far below anything visible."""
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-4)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b, strict=True))
    return a == b


@PROPERTIES
@given(commands=anchored_sessions())
def test_split_clusters_accept_reject_and_count_exactly_as_whole_ones(
    commands: list[Command],
) -> None:
    """Each command, from the same document, split and whole. (Two whole replays of a session
    can part ways: they differ by round-off, and where constraints meet tangentially whether a
    new one is redundant turns on less than that, so the whole sketch itself would decide
    differently for the other replay's document. What splitting must not change is the answer
    for a given document.)"""
    _forget()
    bus = Bus(kernel=None)
    for index, command in enumerate(commands):
        with whole():
            expected = step(Bus(bus.document, kernel=None), command)
        actual = step(bus, command)
        assert actual[0] == expected[0], (index, command)  # accepted, or rejected the same way
        assert actual[1] == expected[1], (index, command)  # state, degrees of freedom, health
        assert close(actual[2], expected[2]), (index, command)  # the same geometry
