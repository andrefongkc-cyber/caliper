"""Anchored geometry splits clusters: what's dimensioned from a fixed origin solves on its own.

A Fix that pins every parameter of an entity (a point, or a whole curve) means it never moves,
so two features dimensioned from it aren't coupled through it. Splitting there is only an
optimization: for any sketch, the split clusters must accept and reject the same commands, with
the same messages, and report the same degrees of freedom, as the whole clusters did, with
solved positions equal to within the solver's precision (see `close`). The whole clusters are
`sketch.reference()`: the solver as it was, which also writes every value as solved.
"""

import json
import re

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
    DeleteEntities,
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
from caliper.engine.constraints import equivalence, sketch
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


def _forget() -> None:
    """Start with nothing cached, as a fresh process would."""
    sketch._GROUPS.clear()
    sketch._SOLVED.clear()
    sketch._REFERRERS.clear()
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
    with sketch.reference():
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
    with sketch.reference():
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
    with sketch.reference():
        unsplit = replay(commands)
    assert same(split, unsplit)


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
    with sketch.reference():
        unsplit = replay(commands)
    assert split[-1][0][0] == "applied"  # type: ignore[index]
    assert same(split, unsplit)
    (difference,) = (
        d for d in equivalence.differences(unsplit[-1][2], split[-1][2]) if d.entity == "e5"
    )
    assert not difference.same  # a step apart: more than round-off,
    assert not difference.significant  # but the same solution


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
        actions = ["from-origin", "constraint", "dimension", "nearly", "edit", "move", "delete"]
        match draw(st.sampled_from(actions)):
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
            case "nearly":
                # A dimension of what already is, or nearly: close to repeating the relations
                # that fix it, where deciding whether it does is hardest.
                chosen = draw(st.lists(st.sampled_from(refs), min_size=1, max_size=2, unique=True))
                placement = draw(points)
                probe = Bus(bus.document, kernel=None)
                made = probe.execute(
                    CreateDimension(refs=tuple(chosen), placement=placement, value=None)
                )
                if not isinstance(made, Applied):
                    continue
                measured = probe.queries.dimension_value(made.created_ids[0])
                if not isinstance(measured, float) or not measured > 0:
                    continue
                off = draw(st.sampled_from([0.0, 1e-12, -1e-12, 1e-9, -1e-9, 1e-6, 1e-3]))
                attempt(
                    CreateDimension(
                        refs=tuple(chosen), placement=placement, value=measured * (1 + off)
                    )
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
            case "delete":
                attempt(DeleteEntities(ids=(draw(st.sampled_from(sorted(bus.document.entities))),)))
            case _:
                id = draw(st.sampled_from(geometry_ids))
                attempt(MoveEntities(ids=(id,), dx=draw(coordinate), dy=draw(coordinate)))
    return commands


def step(bus: Bus, command: Command) -> tuple[object, object, Document]:
    """Run `command`: its outcome, the status after it, and the document."""
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
        bus.document,
    )


def replay(commands: list[Command]) -> list[tuple[object, object, Document]]:
    """Each command's `step`, one after another."""
    bus = Bus(kernel=None)
    return [step(bus, command) for command in commands]


def same(
    split: list[tuple[object, object, Document]], whole: list[tuple[object, object, Document]]
) -> bool:
    """The same outcome and status at every step, and documents with no significant
    difference (`equivalence`: within the solver's precision)."""
    return all(
        a[:2] == b[:2] and not any(d.significant for d in equivalence.differences(b[2], a[2]))
        for a, b in zip(split, whole, strict=True)
    )


def holding(document: Document, reference: Document) -> list[tuple[EntityId, float, float]]:
    """Relations of `document` that don't hold within tolerance, and hold worse than in the
    reference's document: there should be none. (A solve that shrinks the geometry a
    thousandfold, squeezing a rectangle to a nanometre, say, converges at the scale it started
    from, so the old solver can end a little outside the tolerance at the new scale; the
    optimized solver must do no worse.)"""
    theirs = sketch.residuals(reference)
    return [
        (key, residual, allowed)
        for key, (residual, allowed) in sketch.residuals(document).items()
        if not residual <= allowed and not residual <= theirs.get(key, (0.0, 0.0))[0] + allowed
    ]


def moved_elsewhere(before: Document, after: Document, command: Command) -> list[EntityId]:
    """Entities a command changed outside every cluster it reached: there should be none. A
    command reaches the clusters of what it names, and moving or deleting anchored geometry
    reaches everything dimensioned from it, so then nothing is checked."""
    named = set(re.findall(r"\be\d+\b", json.dumps(codec.encode(command))))
    if named & sketch.anchored(before):
        return []
    index = sketch.referrers(before)  # deleting also deletes what refers to it
    named |= {referrer for n in list(named) for referrer in index.get(EntityId(n), ())}
    where = sketch.grouped(before)
    reached = {id(where[EntityId(n)]) for n in named if n in where}
    return [
        key
        for key, entity in before.entities.items()
        if key not in named
        and (key not in where or id(where[key]) not in reached)
        and after.entities.get(key) is not entity
    ]


@PROPERTIES
@given(commands=anchored_sessions())
def test_split_clusters_accept_reject_and_count_exactly_as_whole_ones(
    commands: list[Command],
) -> None:
    """Each command, from the same document, optimized and by the reference (the old solver):
    the same outcome, message, and status; no significant difference in any value; every
    relation holding afterwards; and nothing changed outside the clusters it reached. (Two
    whole replays of a session can part ways: they differ by round-off, and where constraints
    meet tangentially whether a new one is redundant turns on less than that, so the reference
    itself would decide differently for the other replay's document. What the optimized solver
    must not change is the answer for a given document.)"""
    _forget()
    bus = Bus(kernel=None)
    for index, command in enumerate(commands):
        before = bus.document
        with sketch.reference():
            reference = Bus(before, kernel=None)
            expected = step(reference, command)
        actual = step(bus, command)
        assert actual[0] == expected[0], (index, command)  # accepted, or rejected the same way
        assert actual[1] == expected[1], (index, command)  # state, degrees of freedom, health
        found = equivalence.differences(expected[2], actual[2])
        assert not [d for d in found if d.significant], (index, equivalence.report(found))
        if actual[0][0] == "applied":  # type: ignore[index]
            assert holding(bus.document, expected[2]) == [], (index, command)
            assert moved_elsewhere(before, bus.document, command) == [], (index, command)
