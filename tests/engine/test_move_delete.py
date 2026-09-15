"""MoveEntities and DeleteEntities through the bus."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateArc,
    CreateCircle,
    CreateDistanceDimension,
    CreateLine,
    CreateRadialDimension,
    CreateRectangle,
    DeleteEntities,
    MoveEntities,
    Rejected,
)
from caliper.contracts.document import (
    POINT_FEATURES,
    Arc,
    Circle,
    DistanceDimension,
    DistanceOrientation,
    EntityId,
    Feature,
    Line,
    Point2,
    RadialDimension,
    RadialMeasure,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import ErrorCode
from caliper.contracts.queries import BoundingBox
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot

E1, E2, E3, E4, E5 = (EntityId(f"e{n}") for n in range(1, 6))


def P(x: float, y: float) -> Point2:  # noqa: N802
    return Point2(x=x, y=y)


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def rejection(result: CommandResult) -> list[tuple[ErrorCode, str | None]]:
    assert isinstance(result, Rejected), result
    return [(e.code, e.field) for e in result.errors]


def bus_with(*commands: Command) -> Bus:
    bus = Bus()
    for command in commands:
        applied(bus.execute(command))
    return bus


def one_of_each() -> Bus:
    return bus_with(
        CreateLine(start=P(0.0, 0.0), end=P(10.0, 0.0)),
        CreateCircle(center=P(1.0, 2.0), radius=3.0),
        CreateArc(center=P(-1.0, -2.0), radius=4.0, start_angle=30.0, sweep_angle=60.0),
        CreateRectangle(corner=P(5.0, 6.0), width=7.0, height=8.0),
    )


def distance(a: EntityId, fa: Feature, b: EntityId, fb: Feature) -> CreateDistanceDimension:
    return CreateDistanceDimension(
        a=Ref(entity=a, feature=fa),
        b=Ref(entity=b, feature=fb),
        orientation=DistanceOrientation.ALIGNED,
        offset=5.0,
    )


# --- Move -------------------------------------------------------------------------------


def test_move_translates_every_geometry_type_exactly() -> None:
    bus = one_of_each()
    result = applied(bus.execute(MoveEntities(ids=(E1, E2, E3, E4), dx=1.5, dy=-2.0)))
    entities = bus.document.entities
    assert entities[E1] == Line(start=P(1.5, -2.0), end=P(11.5, -2.0))
    assert entities[E2] == Circle(center=P(2.5, 0.0), radius=3.0)
    assert entities[E3] == Arc(center=P(0.5, -4.0), radius=4.0, start_angle=30.0, sweep_angle=60.0)
    assert entities[E4] == Rectangle(corner=P(6.5, 4.0), width=7.0, height=8.0)
    assert result.delta.modified == {E1, E2, E3, E4}
    assert result.label == "Move 4 Entities"
    assert result.created_ids == ()
    assert bus.undo_label == "Move 4 Entities"


def test_move_resolves_its_command() -> None:
    bus = one_of_each()
    result = applied(bus.execute(MoveEntities(ids=(E2, E2), dx=1, dy=0)))  # type: ignore[arg-type]
    assert result.command == MoveEntities(ids=(E2,), dx=1.0, dy=0.0)
    assert isinstance(result.command.dx, float)
    assert bus.document.entities[E2] == Circle(center=P(2.0, 2.0), radius=3.0)  # moved once
    assert result.label == "Move Circle"


def test_dimensions_follow_moved_geometry_and_are_not_moved_themselves() -> None:
    bus = bus_with(
        CreateRectangle(corner=P(0.0, 0.0), width=10.0, height=10.0),
        CreateRectangle(corner=P(20.0, 0.0), width=10.0, height=10.0),
        distance(E1, Feature.CENTER, E2, Feature.CENTER),
    )
    dimension = bus.document.entities[E3]
    applied(bus.execute(MoveEntities(ids=(E2, E3), dx=10.0, dy=0.0)))
    assert bus.document.entities[E3] == dimension
    assert bus.queries.dimension_value(E3) == 30.0


def test_moving_only_annotations_or_by_zero_changes_nothing() -> None:
    bus = bus_with(
        CreateCircle(center=P(0.0, 0.0), radius=1.0),
        CreateRadialDimension(target=E1, measure=RadialMeasure.RADIUS, label_angle=0.0),
    )
    for command in (
        MoveEntities(ids=(E2,), dx=5.0, dy=5.0),
        MoveEntities(ids=(E1,), dx=0.0, dy=0.0),
    ):
        result = applied(bus.execute(command))
        assert not result.delta.before
        assert not result.delta.after
    assert bus.undo_label == "Create Radial Dimension"


def test_undo_and_redo_a_move() -> None:
    bus = one_of_each()
    before = bus.document
    bus.execute(MoveEntities(ids=(E1, E4), dx=3.0, dy=4.0))
    after = bus.document
    bus.undo()
    assert bus.document == before
    bus.redo()
    assert bus.document == after


@pytest.mark.parametrize(
    ("command", "errors"),
    [
        (MoveEntities(ids=(), dx=1.0, dy=0.0), [(ErrorCode.SELECTION_EMPTY, "ids")]),
        (MoveEntities(ids=(E1, E5), dx=1.0, dy=0.0), [(ErrorCode.ENTITY_NOT_FOUND, "ids[1]")]),
        (
            MoveEntities(ids=(EntityId("Bad Id"),), dx=1.0, dy=0.0),
            [(ErrorCode.ID_INVALID, "ids[0]")],
        ),
        (MoveEntities(ids="e1", dx=1.0, dy=0.0), [(ErrorCode.VALUE_WRONG_TYPE, "ids")]),  # type: ignore[arg-type]
        (MoveEntities(ids=(E1,), dx=math.nan, dy=0.0), [(ErrorCode.VALUE_NOT_FINITE, "dx")]),
        (MoveEntities(ids=(E1,), dx=1.0, dy="up"), [(ErrorCode.VALUE_WRONG_TYPE, "dy")]),  # type: ignore[arg-type]
        (
            MoveEntities(ids=(E5, E1), dx=math.inf, dy=0.0),
            [(ErrorCode.ENTITY_NOT_FOUND, "ids[0]"), (ErrorCode.VALUE_NOT_FINITE, "dx")],
        ),
    ],
    ids=[
        "no ids",
        "missing id",
        "malformed id",
        "ids not a list",
        "nan dx",
        "text dy",
        "several problems",
    ],
)
def test_invalid_moves_are_rejected_and_change_nothing(
    command: MoveEntities, errors: list[tuple[ErrorCode, str]]
) -> None:
    bus = one_of_each()
    before = bus.document
    assert rejection(bus.execute(command)) == errors
    assert bus.document == before


def test_a_move_past_the_largest_float_is_rejected() -> None:
    bus = bus_with(CreateRectangle(corner=P(1.7e308, 0.0), width=1.0, height=1.0))
    assert rejection(bus.execute(MoveEntities(ids=(E1,), dx=1.7e308, dy=0.0))) == [
        (ErrorCode.VALUE_NOT_FINITE, "ids")
    ]


def test_a_move_that_collapses_a_line_is_rejected() -> None:
    bus = bus_with(CreateLine(start=P(0.0, 0.0), end=P(1e-17, 0.0)))
    assert rejection(bus.execute(MoveEntities(ids=(E1,), dx=1.0, dy=0.0))) == [
        (ErrorCode.GEOMETRY_DEGENERATE, "ids")
    ]


# --- Delete -----------------------------------------------------------------------------


def test_delete_cascades_to_dimensions_that_reference_deleted_geometry() -> None:
    bus = bus_with(
        CreateRectangle(corner=P(0.0, 0.0), width=10.0, height=10.0),  # e1
        CreateCircle(center=P(30.0, 0.0), radius=2.0),  # e2
        distance(E1, Feature.CENTER, E2, Feature.CENTER),  # e3: refers to e1 and e2
        CreateRadialDimension(target=E2, measure=RadialMeasure.DIAMETER, label_angle=0.0),  # e4
        distance(E1, Feature.BOTTOM_LEFT, E1, Feature.TOP_RIGHT),  # e5: refers to e1 only
    )
    result = applied(bus.execute(DeleteEntities(ids=(E2,))))
    assert set(bus.document.entities) == {E1, E5}
    assert result.delta.removed == {E2, E3, E4}
    assert result.label == "Delete Circle"
    assert result.command == DeleteEntities(ids=(E2,))
    assert bus.document.next_id == 6

    bus.undo()
    assert set(bus.document.entities) == {E1, E2, E3, E4, E5}
    bus.redo()
    assert set(bus.document.entities) == {E1, E5}


def test_deleting_an_annotation_leaves_its_geometry() -> None:
    bus = bus_with(
        CreateCircle(center=P(0.0, 0.0), radius=1.0),
        CreateRadialDimension(target=E1, measure=RadialMeasure.RADIUS, label_angle=0.0),
    )
    result = applied(bus.execute(DeleteEntities(ids=(E2, E2))))
    assert set(bus.document.entities) == {E1}
    assert result.label == "Delete Radial Dimension"
    assert result.command == DeleteEntities(ids=(E2,))


def test_ids_are_never_reused_after_a_delete() -> None:
    bus = one_of_each()
    applied(bus.execute(DeleteEntities(ids=(E3, E4))))
    created = applied(bus.execute(CreateCircle(center=P(0.0, 0.0), radius=1.0)))
    assert created.created_ids == (E5,)


@pytest.mark.parametrize(
    ("command", "errors"),
    [
        (DeleteEntities(ids=()), [(ErrorCode.SELECTION_EMPTY, "ids")]),
        (DeleteEntities(ids=(E1, E5)), [(ErrorCode.ENTITY_NOT_FOUND, "ids[1]")]),
        (DeleteEntities(ids=(EntityId("E1"),)), [(ErrorCode.ID_INVALID, "ids[0]")]),
        (DeleteEntities(ids=None), [(ErrorCode.VALUE_WRONG_TYPE, "ids")]),  # type: ignore[arg-type]
    ],
    ids=["no ids", "missing id", "malformed id", "ids not a list"],
)
def test_invalid_deletes_are_rejected_and_change_nothing(
    command: DeleteEntities, errors: list[tuple[ErrorCode, str]]
) -> None:
    bus = one_of_each()
    before = bus.document
    assert rejection(bus.execute(command)) == errors
    assert bus.document == before


# --- Properties -------------------------------------------------------------------------

coordinate = st.floats(min_value=-1e4, max_value=1e4)
size = st.floats(min_value=0.01, max_value=1e4)
geometry = st.one_of(
    st.builds(
        lambda x, y, dx, dy: CreateLine(start=P(x, y), end=P(x + dx, y + dy)),
        coordinate,
        coordinate,
        size,
        coordinate,
    ),
    st.builds(lambda x, y, r: CreateCircle(center=P(x, y), radius=r), coordinate, coordinate, size),
    st.builds(
        lambda x, y, r, a, s: CreateArc(center=P(x, y), radius=r, start_angle=a, sweep_angle=s),
        coordinate,
        coordinate,
        size,
        st.floats(min_value=-360.0, max_value=360.0),
        st.floats(min_value=0.01, max_value=359.99),
    ),
    st.builds(
        lambda x, y, w, h: CreateRectangle(corner=P(x, y), width=w, height=h),
        coordinate,
        coordinate,
        size,
        size,
    ),
)


@st.composite
def sketches(draw: st.DrawFn) -> Bus:
    """A few geometry entities, with distance and radial dimensions attached to them."""
    bus = bus_with(*draw(st.lists(geometry, min_size=1, max_size=6)))
    ids = sorted(bus.document.entities)

    def feature_of(id: EntityId) -> Feature:
        features = POINT_FEATURES[type(bus.document.entities[id])]  # type: ignore[index]
        return draw(st.sampled_from(sorted(features)))

    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        a, b = draw(st.sampled_from(ids)), draw(st.sampled_from(ids))
        fa, fb = feature_of(a), feature_of(b)
        if (a, fa) != (b, fb):
            applied(bus.execute(distance(a, fa, b, fb)))
    for id in ids:
        if isinstance(bus.document.entities[id], Circle | Arc) and draw(st.booleans()):
            radial = CreateRadialDimension(target=id, measure=RadialMeasure.RADIUS, label_angle=0.0)
            applied(bus.execute(radial))
    return bus


@given(bus=sketches(), dx=coordinate, dy=coordinate)
def test_moving_everything_shifts_the_bounds_and_keeps_dimension_values(
    bus: Bus, dx: float, dy: float
) -> None:
    entities = bus.document.entities
    geometry_ids = sorted(
        i for i, e in entities.items() if isinstance(e, Line | Circle | Arc | Rectangle)
    )
    dimension_ids = sorted(set(entities) - set(geometry_ids))
    before = bus.queries.bounding_box()
    values = [bus.queries.dimension_value(i) for i in dimension_ids]
    applied(bus.execute(MoveEntities(ids=tuple(geometry_ids), dx=dx, dy=dy)))
    after = bus.queries.bounding_box()
    assert isinstance(before, BoundingBox)
    assert isinstance(after, BoundingBox)
    tol = 1e-9 * max(
        1.0,
        abs(dx),
        abs(dy),
        abs(before.x_min),
        abs(before.x_max),
        abs(before.y_min),
        abs(before.y_max),
    )
    assert after.x_min == pytest.approx(before.x_min + dx, abs=tol)
    assert after.y_max == pytest.approx(before.y_max + dy, abs=tol)
    for id, value in zip(dimension_ids, values, strict=True):
        assert bus.queries.dimension_value(id) == pytest.approx(value, abs=2 * tol)


@given(bus=sketches(), data=st.data())
def test_any_delete_leaves_a_valid_document_and_undoes_exactly(
    bus: Bus, data: st.DataObject
) -> None:
    original = bus.document
    ids = data.draw(st.lists(st.sampled_from(sorted(original.entities)), min_size=1, unique=True))
    applied(bus.execute(DeleteEntities(ids=tuple(ids))))
    remaining = bus.document
    assert not set(ids) & set(remaining.entities)
    # No annotation is left pointing at a deleted entity: the file reloads and validates.
    assert snapshot.loads(snapshot.dumps(remaining)) == remaining
    for entity in remaining.entities.values():
        if isinstance(entity, DistanceDimension):
            assert {entity.a.entity, entity.b.entity} <= set(remaining.entities)
        if isinstance(entity, RadialDimension):
            assert entity.target in remaining.entities
    bus.undo()
    assert bus.document == original
    assert snapshot.dumps(bus.document) == snapshot.dumps(original)
