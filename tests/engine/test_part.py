"""The part (ADR 0011): ordered features, sketches on planes, and 2D work inside one sketch.

Every rule here is new in V2's F1; a part with one sketch, which is every V1 document,
behaves as before (the rest of the suite).
"""

from types import MappingProxyType
from typing import get_args

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateAngleDimension,
    CreateArc,
    CreateCheck,
    CreateCircle,
    CreateConstraint,
    CreateDimension,
    CreateDistanceDimension,
    CreateExtrude,
    CreateLine,
    CreatePoint,
    CreateRadialDimension,
    CreateRectangle,
    CreateSketch,
    DeleteEntities,
    FilletCorner,
    ModifyEntity,
    MoveEntities,
    Rejected,
)
from caliper.contracts.document import (
    FIRST_SKETCH,
    Arc,
    Circle,
    ConstraintType,
    DistanceOrientation,
    Document,
    EntityId,
    Feature,
    Line,
    Metric,
    Plane,
    Point2,
    RadialMeasure,
    Rectangle,
    Ref,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import Expectation
from caliper.engine import part
from caliper.engine.commands.bus import Bus
from caliper.engine.commands.validation import GEOMETRY
from caliper.engine.document.delta import is_empty
from caliper.engine.io import snapshot

ORIGIN = Point2(x=0.0, y=0.0)
E0 = FIRST_SKETCH


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def refused(result: CommandResult) -> Error:
    assert isinstance(result, Rejected), result
    (error,) = result.errors
    return error


def created(bus: Bus, command: Command) -> EntityId:
    (id,) = applied(bus.execute(command)).created_ids
    return id


def two_sketches() -> tuple[Bus, EntityId]:
    """A part with its first sketch on XY and a second on XZ, nothing drawn yet."""
    bus = Bus()
    return bus, created(bus, CreateSketch(plane=Plane.XZ))


def ref(id: EntityId, feature: Feature) -> Ref:
    return Ref(entity=id, feature=feature)


# --- Sketches -----------------------------------------------------------------------------


def test_a_sketch_is_added_at_the_end_of_the_part_and_undone_like_any_change() -> None:
    bus = Bus()
    result = applied(bus.execute(CreateSketch(plane=Plane.YZ)))
    assert result.created_ids == (EntityId("e1"),)  # allocated like an entity's id
    assert result.label == "Create Sketch"
    assert result.command == CreateSketch(plane=Plane.YZ, id=EntityId("e1"))
    assert bus.document.features == (
        Sketch(id=E0, plane=Plane.XY),
        Sketch(id=EntityId("e1"), plane=Plane.YZ),
    )
    assert dict(bus.document.entities) == {}  # a sketch isn't an entity
    assert result.delta.features_after == bus.document.features
    bus.undo()
    assert bus.document == Document.empty()
    bus.redo()
    assert [f.id for f in bus.document.features] == [E0, EntityId("e1")]


def test_a_sketch_takes_a_plane_and_an_unused_id() -> None:
    bus = Bus()
    error = refused(bus.execute(CreateSketch(plane="xw")))  # type: ignore[arg-type]
    assert (error.code, error.field) == (ErrorCode.VALUE_OUT_OF_RANGE, "plane")
    assert applied(bus.execute(CreateSketch(plane="xz"))).command == CreateSketch(  # type: ignore[arg-type]
        plane=Plane.XZ, id=EntityId("e1")
    )
    error = refused(bus.execute(CreateSketch(plane=Plane.XY, id=E0)))
    assert (error.code, error.field) == (ErrorCode.ID_TAKEN, "id")
    # Ids are one space: geometry can't take a sketch's id, nor a sketch an entity's.
    taken = CreateCircle(center=ORIGIN, radius=1.0, sketch=E0, id=EntityId("e1"))
    assert refused(bus.execute(taken)).code is ErrorCode.ID_TAKEN
    circle = created(bus, CreateCircle(center=ORIGIN, radius=1.0, sketch=E0))
    assert circle == EntityId("e2")  # the next free number
    assert refused(bus.execute(CreateSketch(plane=Plane.XY, id=circle))).code is ErrorCode.ID_TAKEN


def test_geometry_goes_in_the_only_sketch_or_the_one_named() -> None:
    bus = Bus()
    line = applied(bus.execute(CreateLine(start=ORIGIN, end=Point2(x=1.0, y=0.0))))
    assert line.command.sketch == E0  # type: ignore[union-attr]  # recorded, for replay
    second = created(bus, CreateSketch(plane=Plane.XZ))
    error = refused(bus.execute(CreateLine(start=ORIGIN, end=Point2(x=1.0, y=0.0))))
    assert (error.code, error.field) == (ErrorCode.SKETCH_REQUIRED, "sketch")
    assert "2 sketches (e0, e2)" in error.message
    there = created(bus, CreateCircle(center=ORIGIN, radius=2.0, sketch=second))
    assert bus.document.entities[there] == Circle(center=ORIGIN, radius=2.0, sketch=second)
    assert part.sketch_of(bus.document, there) == second


def test_a_part_with_no_sketch_has_nowhere_to_draw() -> None:
    bus = Bus()
    applied(bus.execute(DeleteEntities(ids=(E0,))))
    assert bus.document.features == ()
    error = refused(bus.execute(CreateRectangle(corner=ORIGIN, width=1.0, height=1.0)))
    assert error.code is ErrorCode.SKETCH_REQUIRED
    assert "no sketch" in error.message


def test_geometry_must_name_a_sketch_that_exists() -> None:
    bus = Bus()
    error = refused(bus.execute(CreateCircle(center=ORIGIN, radius=1.0, sketch=EntityId("e9"))))
    assert (error.code, error.field, error.message) == (
        ErrorCode.ENTITY_NOT_FOUND,
        "sketch",
        "no sketch 'e9'",
    )
    circle = created(bus, CreateCircle(center=ORIGIN, radius=1.0))
    error = refused(bus.execute(CreateCircle(center=ORIGIN, radius=1.0, sketch=circle)))
    assert (error.code, error.field) == (ErrorCode.ENTITY_WRONG_KIND, "sketch")


# --- Relations stay inside one sketch -----------------------------------------------------


def test_a_constraint_or_dimension_across_sketches_is_refused() -> None:
    bus, second = two_sketches()
    here = created(bus, CreateLine(start=ORIGIN, end=Point2(x=10.0, y=0.0), sketch=E0))
    there = created(bus, CreateLine(start=ORIGIN, end=Point2(x=0.0, y=10.0), sketch=second))
    across = (ref(here, Feature.CURVE), ref(there, Feature.CURVE))
    error = refused(bus.execute(CreateConstraint(type=ConstraintType.PERPENDICULAR, refs=across)))
    assert (error.code, error.ids) == (ErrorCode.SKETCH_MIXED, (E0, second))
    distance = CreateDistanceDimension(
        a=ref(here, Feature.START),
        b=ref(there, Feature.END),
        orientation=DistanceOrientation.ALIGNED,
        offset=1.0,
    )
    assert refused(bus.execute(distance)).code is ErrorCode.SKETCH_MIXED
    inferred = CreateDimension(refs=(across[0], across[1]), placement=Point2(x=5.0, y=5.0))
    assert refused(bus.execute(inferred)).code is ErrorCode.SKETCH_MIXED
    # Within the second sketch, the same constraint is fine.
    other = created(bus, CreateLine(start=ORIGIN, end=Point2(x=10.0, y=1.0), sketch=second))
    within = (ref(there, Feature.CURVE), ref(other, Feature.CURVE))
    constraint = created(bus, CreateConstraint(type=ConstraintType.PERPENDICULAR, refs=within))
    assert part.sketch_of(bus.document, constraint) == second


def test_a_fillet_rounds_two_lines_of_one_sketch_and_the_arc_joins_them() -> None:
    bus, second = two_sketches()
    a = created(bus, CreateLine(start=Point2(x=50.0, y=0.0), end=ORIGIN, sketch=second))
    b = created(bus, CreateLine(start=ORIGIN, end=Point2(x=0.0, y=40.0), sketch=second))
    arc = created(bus, FilletCorner(a=a, b=b, radius=5.0))
    found = bus.document.entities[arc]
    assert isinstance(found, Arc)
    assert found.sketch == second
    elsewhere = created(bus, CreateLine(start=Point2(x=0.0, y=40.0), end=ORIGIN, sketch=E0))
    error = refused(bus.execute(FilletCorner(a=b, b=elsewhere, radius=5.0)))
    assert (error.code, error.field) == (ErrorCode.SKETCH_MIXED, "b")


def test_geometry_moves_one_sketch_at_a_time_and_a_sketch_isnt_moved_by_id() -> None:
    bus, second = two_sketches()
    here = created(bus, CreateCircle(center=ORIGIN, radius=1.0, sketch=E0))
    there = created(bus, CreateCircle(center=ORIGIN, radius=1.0, sketch=second))
    error = refused(bus.execute(MoveEntities(ids=(here, there), dx=1.0, dy=0.0)))
    assert error.code is ErrorCode.SKETCH_MIXED
    error = refused(bus.execute(MoveEntities(ids=(second,), dx=1.0, dy=0.0)))
    assert (error.code, error.field) == (ErrorCode.ENTITY_WRONG_KIND, "ids[0]")
    applied(bus.execute(MoveEntities(ids=(there,), dx=3.0, dy=4.0)))
    assert bus.document.entities[there] == Circle(
        center=Point2(x=3.0, y=4.0), radius=1.0, sketch=second
    )


def test_suggestions_stay_inside_one_sketch() -> None:
    """Two lines meeting end to end suggest a coincidence; the same two lines drawn in two
    sketches, on two planes, don't meet at all."""
    bus, second = two_sketches()
    created(bus, CreateLine(start=ORIGIN, end=Point2(x=10.0, y=0.0), sketch=E0))
    created(
        bus, CreateLine(start=Point2(x=10.0, y=0.001), end=Point2(x=10.0, y=10.0), sketch=second)
    )
    across = bus.queries.suggest_constraints(tolerance=0.01)
    assert not [s for s in across if s.type is ConstraintType.COINCIDENT]
    assert all(len(s.refs) == 1 for s in across)  # each line level or upright on its own
    created(bus, CreateLine(start=Point2(x=10.0, y=0.001), end=Point2(x=10.0, y=10.0), sketch=E0))
    found = bus.queries.suggest_constraints(tolerance=0.01)
    assert any(s.type is ConstraintType.COINCIDENT for s in found)
    for suggestion in found:
        assert len({part.sketch_of(bus.document, r.entity) for r in suggestion.refs}) == 1


def test_constraints_in_two_sketches_solve_apart() -> None:
    bus, second = two_sketches()
    here = created(bus, CreateLine(start=ORIGIN, end=Point2(x=10.0, y=1.0), sketch=E0))
    there = created(bus, CreateLine(start=ORIGIN, end=Point2(x=10.0, y=1.0), sketch=second))
    before = bus.document.entities[there]
    applied(
        bus.execute(
            CreateConstraint(type=ConstraintType.HORIZONTAL, refs=(ref(here, Feature.CURVE),))
        )
    )
    assert bus.document.entities[there] is before  # the other sketch wasn't touched
    status = bus.queries.solve_status()
    assert status.entity_dof[here] == 3
    assert status.entity_dof[there] == 4
    assert status.dof == 7  # the part's total


# --- Deleting and editing sketches -----------------------------------------------------------


def test_deleting_a_sketch_deletes_what_is_drawn_in_it_and_undo_brings_all_of_it_back() -> None:
    bus, second = two_sketches()
    kept = created(bus, CreateRectangle(corner=ORIGIN, width=10.0, height=5.0, sketch=E0))
    circle = created(bus, CreateCircle(center=ORIGIN, radius=2.0, sketch=second))
    line = created(bus, CreateLine(start=ORIGIN, end=Point2(x=5.0, y=1.0), sketch=second))
    level = created(
        bus, CreateConstraint(type=ConstraintType.HORIZONTAL, refs=(ref(line, Feature.CURVE),))
    )
    check = created(
        bus, CreateCheck(metric=Metric.BBOX_WIDTH, expected=4.0, tolerance=1e-9, ids=(circle,))
    )
    whole = bus.document
    result = applied(bus.execute(DeleteEntities(ids=(second,))))
    assert result.label == "Delete Sketch"
    assert [f.id for f in bus.document.features] == [E0]
    assert set(bus.document.entities) == {kept, check}  # the check stays, failing (C-1)
    assert level not in bus.document.entities
    assert not bus.queries.check(bus.document.entities[check]).passed  # type: ignore[arg-type]
    bus.undo()
    assert bus.document == whole


def test_a_sketch_moves_to_another_plane_whole() -> None:
    bus, second = two_sketches()
    circle = created(bus, CreateCircle(center=Point2(x=3.0, y=4.0), radius=2.0, sketch=second))
    drawn = bus.document.entities[circle]
    result = applied(bus.execute(ModifyEntity(id=second, changes={"plane": "yz"})))
    assert result.label == "Change Plane"
    assert result.command == ModifyEntity(id=second, changes=MappingProxyType({"plane": Plane.YZ}))
    assert bus.document.features[1] == Sketch(id=second, plane=Plane.YZ)
    assert bus.document.entities[circle] is drawn  # its 2D coordinates are the same
    bus.undo()
    assert bus.document.features[1] == Sketch(id=second, plane=Plane.XZ)


@pytest.mark.parametrize(
    ("changes", "code", "field"),
    [
        ({"id": "e5"}, ErrorCode.VALUE_OUT_OF_RANGE, "id"),
        ({"plane": "up"}, ErrorCode.VALUE_OUT_OF_RANGE, "plane"),
        ({"width": 1.0}, ErrorCode.FIELD_UNKNOWN, "width"),
    ],
)
def test_a_sketch_keeps_its_id_and_has_only_a_plane_to_change(
    changes: dict[str, object], code: ErrorCode, field: str
) -> None:
    error = refused(Bus().execute(ModifyEntity(id=E0, changes=changes)))  # type: ignore[arg-type]
    assert (error.code, error.field) == (code, field)


def test_geometry_stays_in_the_sketch_it_was_drawn_in() -> None:
    bus, second = two_sketches()
    circle = created(bus, CreateCircle(center=ORIGIN, radius=1.0, sketch=E0))
    error = refused(bus.execute(ModifyEntity(id=circle, changes={"sketch": second})))
    assert (error.code, error.field) == (ErrorCode.VALUE_OUT_OF_RANGE, "sketch")
    # Naming the sketch it's already in changes nothing, as setting any field to its value.
    applied(bus.execute(ModifyEntity(id=circle, changes={"sketch": E0, "radius": 2.0})))


# --- Queries ---------------------------------------------------------------------------------


def test_sketch_of_says_where_an_entity_is() -> None:
    bus, second = two_sketches()
    circle = created(bus, CreateCircle(center=ORIGIN, radius=1.0, sketch=second))
    line = created(bus, CreateLine(start=ORIGIN, end=Point2(x=1.0, y=0.0), sketch=second))
    radius = created(
        bus, CreateDimension(refs=(ref(circle, Feature.CURVE),), placement=Point2(x=2.0, y=0.0))
    )
    level = created(
        bus, CreateConstraint(type=ConstraintType.HORIZONTAL, refs=(ref(line, Feature.CURVE),))
    )
    check = created(
        bus, CreateCheck(metric=Metric.BBOX_WIDTH, expected=2.0, tolerance=1e-9, ids=(circle,))
    )
    queries = bus.queries
    assert [queries.sketch_of(id) for id in (circle, line, radius, level)] == [second] * 4
    assert [queries.sketch_of(id) for id in (check, second, E0, EntityId("e99"))] == [None] * 4


def test_measurements_read_one_sketch() -> None:
    bus, second = two_sketches()
    here = created(bus, CreateRectangle(corner=ORIGIN, width=10.0, height=5.0, sketch=E0))
    there = created(bus, CreateCircle(center=ORIGIN, radius=2.0, sketch=second))
    queries = bus.queries
    answers = (
        queries.measure_distance(ref(here, Feature.CENTER), ref(there, Feature.CENTER)),
        queries.bounding_box(),
        queries.bounding_box([here, there]),
        queries.area_properties([here, there]),
    )
    assert [getattr(a, "code", None) for a in answers] == [ErrorCode.SKETCH_MIXED] * 4
    assert queries.bounding_box([there]) == queries.bounding_box([there, there])
    across = Expectation(metric=Metric.BBOX_WIDTH, expected=10.0, tolerance=1e-9, ids=(here, there))
    result = queries.check(across)
    assert not result.passed
    assert result.error is not None
    assert result.error.code is ErrorCode.SKETCH_MIXED
    # Stored checks must be measurable now, so one across sketches isn't stored.
    stored = CreateCheck(metric=Metric.BBOX_WIDTH, expected=10.0, tolerance=1e-9, ids=(here, there))
    assert refused(bus.execute(stored)).code is ErrorCode.SKETCH_MIXED


def test_all_the_geometry_in_one_sketch_still_has_one_box() -> None:
    """A second sketch with nothing in it changes nothing: `bounding_box()` is the drawing's."""
    bus, _ = two_sketches()
    created(bus, CreateRectangle(corner=ORIGIN, width=10.0, height=5.0, sketch=E0))
    box = bus.queries.bounding_box()
    assert not isinstance(box, Error)
    assert (box.width, box.height) == (10.0, 5.0)


def test_constraint_and_dimension_options_across_sketches_say_why_not() -> None:
    bus, second = two_sketches()
    here = created(bus, CreateLine(start=ORIGIN, end=Point2(x=10.0, y=0.0), sketch=E0))
    there = created(bus, CreateLine(start=ORIGIN, end=Point2(x=0.0, y=10.0), sketch=second))
    refs = (ref(here, Feature.CURVE), ref(there, Feature.CURVE))
    options = bus.queries.applicable_constraints(refs)
    assert options
    assert {getattr(o.error, "code", None) for o in options} == {ErrorCode.SKETCH_MIXED}
    inferred = bus.queries.infer_dimension(refs, Point2(x=5.0, y=5.0))
    assert getattr(inferred, "code", None) is ErrorCode.SKETCH_MIXED


# --- Every command keeps the part whole --------------------------------------------------------


def test_every_command_keeps_the_parts_features_unless_it_changes_them() -> None:
    """Each kind of command, run in the second sketch of a two-sketch part. A handler that
    builds its document afresh instead of from the one before (`dataclasses.replace`) would
    drop the features back to the default, and this fails for it; so does a new command type
    nobody added here."""
    bus, second = two_sketches()
    features = bus.document.features
    a = created(bus, CreateLine(start=Point2(x=50.0, y=0.0), end=ORIGIN, sketch=second))
    b = created(bus, CreateLine(start=ORIGIN, end=Point2(x=0.0, y=40.0), sketch=second))
    circle = created(bus, CreateCircle(center=Point2(x=20.0, y=20.0), radius=5.0, sketch=second))
    commands: list[Command] = [
        CreatePoint(position=Point2(x=1.0, y=1.0), sketch=second),
        CreateArc(center=ORIGIN, radius=3.0, start_angle=0.0, sweep_angle=90.0, sketch=second),
        CreateRectangle(corner=ORIGIN, width=4.0, height=2.0, sketch=second),
        CreateDistanceDimension(
            a=ref(a, Feature.START),
            b=ref(a, Feature.END),
            orientation=DistanceOrientation.ALIGNED,
            offset=2.0,
        ),
        CreateRadialDimension(target=circle, measure=RadialMeasure.RADIUS, label_angle=0.0),
        CreateAngleDimension(a=ref(a, Feature.CURVE), b=ref(b, Feature.CURVE), offset=5.0),
        CreateDimension(refs=(ref(circle, Feature.CURVE),), placement=Point2(x=30.0, y=20.0)),
        CreateConstraint(type=ConstraintType.HORIZONTAL, refs=(ref(a, Feature.CURVE),)),
        CreateCheck(metric=Metric.BBOX_WIDTH, expected=10.0, tolerance=1e-9, ids=(circle,)),
        MoveEntities(ids=(circle,), dx=1.0, dy=0.0),
        ModifyEntity(id=circle, changes={"radius": 6.0}),
        FilletCorner(a=a, b=b, radius=5.0),
        DeleteEntities(ids=(circle,)),
    ]
    changing = {CreateSketch, CreateExtrude}  # add features: tested in their own files
    assert {type(c) for c in commands} | changing | {CreateLine, CreateCircle} == set(
        get_args(Command)
    )
    for command in commands:
        applied(bus.execute(command))
        assert bus.document.features == features, command.kind


# --- Sessions -------------------------------------------------------------------------------

POINTS = st.builds(
    Point2,
    x=st.integers(-20, 20).map(float),
    y=st.integers(-20, 20).map(float),
)


@st.composite
def steps(draw: st.DrawFn) -> list[tuple[str, object]]:
    """Commands against a part with sketches coming and going: geometry drawn into one by
    name or by default, constraints that may reach across sketches, moves, edits, deletes of
    geometry and of sketches, and undo and redo. Many are refused, which is part of it. Drawing
    is drawn three times as often as the rest, so there is usually something to relate."""
    line = st.tuples(st.just("line"), st.tuples(POINTS, POINTS, st.integers(0, 3)))
    circle = st.tuples(st.just("circle"), st.tuples(POINTS, st.integers(0, 3)))
    return draw(
        st.lists(
            st.one_of(
                st.tuples(st.just("sketch"), st.sampled_from(list(Plane))),
                line,
                line,
                line,
                circle,
                circle,
                circle,
                st.tuples(st.just("relate"), st.tuples(st.integers(0, 30), st.integers(0, 30))),
                st.tuples(st.just("relate"), st.tuples(st.integers(0, 30), st.integers(0, 30))),
                st.tuples(st.just("move"), st.lists(st.integers(0, 30), min_size=1, max_size=3)),
                st.tuples(
                    st.just("plane"), st.tuples(st.integers(0, 3), st.sampled_from(list(Plane)))
                ),
                st.tuples(st.just("delete"), st.integers(0, 40)),
                st.tuples(st.just("undo"), st.none()),
                st.tuples(st.just("redo"), st.none()),
            ),
            min_size=8,
            max_size=40,
        )
    )


def _sketch(document: Document, index: int) -> EntityId | None:
    """The index-th sketch, or None to leave it to the bus: indexes past the end mean None."""
    found = part.sketches(document)
    return found[index] if index < len(found) else None


def _geometry(document: Document, index: int) -> EntityId:
    ids = sorted(id for id, e in document.entities.items() if isinstance(e, GEOMETRY))
    return ids[index % len(ids)] if ids else EntityId("e99")


def _point(document: Document, id: EntityId) -> Ref:
    entity = document.entities.get(id)
    return Ref(entity=id, feature=Feature.END if isinstance(entity, Line) else Feature.CENTER)


def _command(document: Document, kind: str, value: object) -> Command | None:
    match kind, value:
        case "sketch", Plane() as plane:
            return CreateSketch(plane=plane)
        case "line", (Point2() as a, Point2() as b, int(i)) if a != b:
            return CreateLine(start=a, end=b, sketch=_sketch(document, i))
        case "circle", (Point2() as c, int(i)):
            return CreateCircle(center=c, radius=3.0, sketch=_sketch(document, i))
        case "relate", (int(a), int(b)):
            # Two points made to meet: a line's end or a circle's centre. Picked across the
            # whole part, so some pairs are in two sketches and must be refused.
            refs = tuple(_point(document, _geometry(document, i)) for i in (a, b))
            return CreateConstraint(type=ConstraintType.COINCIDENT, refs=refs)
        case "move", list() as picks:
            return MoveEntities(ids=tuple(_geometry(document, i) for i in picks), dx=1.0, dy=0.0)
        case "plane", (int(i), Plane() as plane):
            found = _sketch(document, i)
            return None if found is None else ModifyEntity(id=found, changes={"plane": plane})
        case "delete", int(i):
            everything = sorted([*document.entities, *part.sketches(document)])
            return DeleteEntities(ids=(everything[i % len(everything)],)) if everything else None
    return None


def assert_whole(document: Document) -> None:
    """The part's own rules: ids unique across features and entities, geometry in a sketch
    the part has, every relation inside one sketch."""
    features = [f.id for f in document.features]
    assert len(features) == len(set(features))
    assert not set(features) & set(document.entities)
    for id, entity in document.entities.items():
        if isinstance(entity, GEOMETRY):
            assert entity.sketch in features, id
        elif not isinstance(entity, Expectation):
            assert part.sketch_of(document, id) in features, id
            assert (
                part.one_sketch(
                    document, (r.entity for r in getattr(entity, "refs", ())), field="", what=""
                )
                is None
            )


@settings(max_examples=150, deadline=None)
@given(steps())
def test_every_command_keeps_the_part_whole_and_undo_redo_and_files_exact(
    session: list[tuple[str, object]],
) -> None:
    bus = Bus()
    for kind, value in session:
        if kind == "undo":
            bus.undo()
        elif kind == "redo":
            bus.redo()
        else:
            command = _command(bus.document, kind, value)
            if command is None:
                continue
            before = bus.document
            result = bus.execute(command)
            if isinstance(result, Rejected) or is_empty(result.delta):
                assert bus.document == before  # nothing changed, so nothing to undo
            else:
                after = bus.document
                bus.undo()
                assert bus.document == before
                bus.redo()
                assert bus.document == after
        assert_whole(bus.document)
        assert snapshot.loads(snapshot.dumps(bus.document)) == bus.document


def test_the_rules_hold_for_hand_built_documents_too() -> None:
    """A loaded file goes through the same rules: geometry in a missing sketch is refused."""
    document = Document(
        entities=MappingProxyType(
            {EntityId("e1"): Line(start=ORIGIN, end=Point2(x=1.0, y=0.0), sketch=EntityId("e5"))}
        ),
        next_id=2,
    )
    with pytest.raises(Exception, match="no sketch 'e5'"):
        snapshot.loads(snapshot.dumps(document))
    assert Rectangle(corner=ORIGIN, width=1.0, height=1.0).sketch == E0
