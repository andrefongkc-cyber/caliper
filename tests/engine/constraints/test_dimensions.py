"""The dimension system: all seven kinds, inferred from selection and placement, and driving.

A driven dimension (value None) only measures. A driving one moves geometry to its value,
and changing the value moves it again, in the engine.
"""

import math

import pytest

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateAngleDimension,
    CreateArc,
    CreateCircle,
    CreateDimension,
    CreateDistanceDimension,
    CreateLine,
    CreatePoint,
    CreateRectangle,
    ModifyEntity,
    Rejected,
)
from caliper.contracts.document import (
    AngleDimension,
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
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import DimensionType
from caliper.engine.commands.bus import Bus

TOL = 1e-9
E = EntityId


def pt(x: float, y: float) -> Point2:
    return Point2(x=x, y=y)


def ref(entity: str, feature: str) -> Ref:
    return Ref(entity=E(entity), feature=Feature(feature))


def curve(entity: str) -> Ref:
    return ref(entity, "curve")


def run(bus: Bus, command: Command) -> Applied:
    result = bus.execute(command)
    assert isinstance(result, Applied), result
    return result


def rejected(result: CommandResult) -> Error:
    assert isinstance(result, Rejected), result
    return result.errors[0]


def value(bus: Bus, id: str) -> float:
    measured = bus.queries.dimension_value(E(id))
    assert isinstance(measured, float), measured
    return measured


def kind(bus: Bus, id: str) -> DimensionType:
    found = bus.queries.dimension_type(E(id))
    assert isinstance(found, DimensionType), found
    return found


def line(bus: Bus, id: str) -> Line:
    entity = bus.document.entities[E(id)]
    assert isinstance(entity, Line)
    return entity


def slanted() -> Bus:
    """e1: a line from (0, 0) to (30, 40), 50 long."""
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(30, 40)))
    return bus


# --- Inference from selection and placement --------------------------------------------------


@pytest.mark.parametrize(
    ("placement", "expected", "measured"),
    [
        (pt(15, -10), DimensionType.HORIZONTAL_DISTANCE, 30.0),  # below: measures across
        (pt(15, 60), DimensionType.HORIZONTAL_DISTANCE, 30.0),  # above
        (pt(-10, 20), DimensionType.VERTICAL_DISTANCE, 40.0),  # beside: measures up
        (pt(5, 30), DimensionType.LENGTH, 50.0),  # off to the side of the line itself
        (pt(40, 50), DimensionType.LENGTH, 50.0),
    ],
)
def test_one_line_is_a_length_or_its_extent_depending_on_placement(
    placement: Point2, expected: DimensionType, measured: float
) -> None:
    bus = slanted()
    assert bus.queries.infer_dimension([curve("e1")], placement) == expected
    result = run(bus, CreateDimension(refs=(curve("e1"),), placement=placement))
    assert kind(bus, "e2") == expected
    assert value(bus, "e2") == measured
    assert result.label == f"Create {expected.value.replace('_', ' ').title()} Dimension"
    assert isinstance(result.command, CreateDimension)
    assert result.command.type is expected  # the resolved command records what was inferred


def test_a_level_line_is_always_a_length() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(100, 0)))
    for placement in (pt(50, -10), pt(50, 10), pt(-5, 0), pt(200, 30)):
        assert bus.queries.infer_dimension([curve("e1")], placement) is DimensionType.LENGTH


def test_two_points_give_horizontal_vertical_or_aligned_distances() -> None:
    bus = Bus(kernel=None)
    run(bus, CreatePoint(position=pt(0, 0)))
    run(bus, CreatePoint(position=pt(40, 30)))
    refs = [ref("e1", "point"), ref("e2", "point")]
    assert bus.queries.infer_dimension(refs, pt(20, -5)) is DimensionType.HORIZONTAL_DISTANCE
    assert bus.queries.infer_dimension(refs, pt(50, 15)) is DimensionType.VERTICAL_DISTANCE
    assert bus.queries.infer_dimension(refs, pt(10, 20)) is DimensionType.DISTANCE
    run(bus, CreateDimension(refs=tuple(refs), placement=pt(10, 20)))
    assert value(bus, "e3") == 50.0


def test_a_circle_is_a_diameter_or_a_radius_placed_inside() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=10))
    assert bus.queries.infer_dimension([curve("e1")], pt(20, 0)) is DimensionType.DIAMETER
    assert bus.queries.infer_dimension([curve("e1")], pt(3, 0)) is DimensionType.RADIUS
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(20, 20)))
    dimension = bus.document.entities[E("e2")]
    assert isinstance(dimension, RadialDimension)
    assert dimension.measure is RadialMeasure.DIAMETER
    assert dimension.label_angle == 45.0
    assert value(bus, "e2") == 20.0


def test_an_arc_is_a_radius() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateArc(center=pt(0, 0), radius=7, start_angle=0, sweep_angle=90))
    assert bus.queries.infer_dimension([curve("e1")], pt(20, 20)) is DimensionType.RADIUS
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(20, 20)))
    assert value(bus, "e2") == 7.0


def test_two_circles_measure_between_their_centres() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=5))
    run(bus, CreateCircle(center=pt(30, 40), radius=8))
    run(bus, CreateDimension(refs=(curve("e1"), curve("e2")), placement=pt(0, 40)))
    dimension = bus.document.entities[E("e3")]
    assert isinstance(dimension, DistanceDimension)
    assert (dimension.a, dimension.b) == (ref("e1", "center"), ref("e2", "center"))
    assert kind(bus, "e3") is DimensionType.DISTANCE
    assert value(bus, "e3") == 50.0


def test_a_point_and_a_line_is_the_perpendicular_distance() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(100, 0)))
    run(bus, CreatePoint(position=pt(250, 12)))
    run(bus, CreateDimension(refs=(ref("e2", "point"), curve("e1")), placement=pt(260, 6)))
    assert kind(bus, "e3") is DimensionType.DISTANCE
    assert value(bus, "e3") == 12.0  # to the line's extension, not its end


def test_two_parallel_lines_are_a_distance_and_two_crossing_lines_an_angle() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(100, 0)))
    run(bus, CreateLine(start=pt(0, 20), end=pt(100, 20)))
    run(bus, CreateLine(start=pt(0, 0), end=pt(50, 50)))
    assert (
        bus.queries.infer_dimension([curve("e1"), curve("e2")], pt(50, 10))
        is DimensionType.DISTANCE
    )
    assert (
        bus.queries.infer_dimension([curve("e1"), curve("e3")], pt(30, 10)) is DimensionType.ANGLE
    )
    run(bus, CreateDimension(refs=(curve("e1"), curve("e2")), placement=pt(50, 10)))
    assert value(bus, "e4") == 20.0


def test_angle_placement_picks_the_angle_or_its_supplement() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(100, 0)))
    run(bus, CreateLine(start=pt(0, 0), end=pt(50, 50)))
    refs = (curve("e1"), curve("e2"))
    run(bus, CreateDimension(refs=refs, placement=pt(30, 10)))  # between the two lines
    run(bus, CreateDimension(refs=refs, placement=pt(-30, 10)))  # in the neighbouring sector
    inside, outside = bus.document.entities[E("e3")], bus.document.entities[E("e4")]
    assert isinstance(inside, AngleDimension)
    assert isinstance(outside, AngleDimension)
    assert (inside.supplementary, outside.supplementary) == (False, True)
    assert value(bus, "e3") == pytest.approx(45.0, abs=TOL)
    assert value(bus, "e4") == pytest.approx(135.0, abs=TOL)
    assert kind(bus, "e3") is DimensionType.ANGLE


def test_an_explicit_kind_overrides_inference_when_it_fits() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=10))
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(30, 0), type=DimensionType.RADIUS))
    assert value(bus, "e2") == 10.0
    error = rejected(
        bus.execute(
            CreateDimension(refs=(curve("e1"),), placement=pt(30, 0), type=DimensionType.ANGLE)
        )
    )
    assert error.code is ErrorCode.CONSTRAINT_NOT_APPLICABLE
    assert error.field == "type"
    assert "radius, diameter" in error.message


@pytest.mark.parametrize(
    ("refs", "code"),
    [
        ((), ErrorCode.CONSTRAINT_NOT_APPLICABLE),
        ((ref("e1", "start"),), ErrorCode.CONSTRAINT_NOT_APPLICABLE),  # one point
        ((curve("e1"), curve("e1")), ErrorCode.REFERENCE_DEGENERATE),
        ((ref("nope", "start"),), ErrorCode.ENTITY_NOT_FOUND),
        ((ref("e1", "center"),), ErrorCode.REFERENCE_INVALID_FEATURE),
        ((curve("e1"), curve("e2"), curve("e1")), ErrorCode.REFERENCE_DEGENERATE),
    ],
)
def test_selections_that_dimension_nothing_are_rejected(
    refs: tuple[Ref, ...], code: ErrorCode
) -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreateLine(start=pt(0, 5), end=pt(10, 9)))
    before = bus.document
    assert rejected(bus.execute(CreateDimension(refs=refs, placement=pt(0, 0)))).code is code
    assert bus.document == before
    query = bus.queries.infer_dimension(refs, pt(0, 0))
    assert isinstance(query, Error)
    assert query.code is code


# --- Driving ----------------------------------------------------------------------------------


def test_driving_length_moves_the_end_and_keeps_the_start() -> None:
    bus = slanted()
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(5, 30), value=100.0))
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(60, 80))
    assert value(bus, "e2") == 100.0
    # Modify the dimension: the geometry follows, in the engine.
    before = bus.document
    run(bus, ModifyEntity(id=E("e2"), changes={"value": 150.0}))
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(90, 120))
    assert value(bus, "e2") == 150.0
    bus.undo()
    assert bus.document == before


def test_driving_the_rectangle_width_keeps_the_bottom_left_corner() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateRectangle(corner=pt(0, 0), width=100, height=50))
    run(bus, CreateDimension(refs=(ref("e1", "bottom"),), placement=pt(50, -10), value=100.0))
    run(bus, ModifyEntity(id=E("e2"), changes={"value": 120.0}))
    assert bus.document.entities[E("e1")] == Rectangle(corner=pt(0, 0), width=120, height=50)
    assert kind(bus, "e2") is DimensionType.LENGTH


def test_driving_horizontal_and_vertical_distances() -> None:
    bus = Bus(kernel=None)
    run(bus, CreatePoint(position=pt(0, 0)))
    run(bus, CreatePoint(position=pt(40, 30)))
    refs = (ref("e1", "point"), ref("e2", "point"))
    run(bus, CreateDimension(refs=refs, placement=pt(20, -5), value=25.0))
    run(bus, CreateDimension(refs=refs, placement=pt(50, 15), value=10.0))
    assert bus.queries.feature_point(ref("e2", "point")) == pt(25, 10)
    # The sign is kept: a horizontal distance never flips the point to the other side.
    run(bus, ModifyEntity(id=E("e3"), changes={"value": 5.0}))
    assert bus.queries.feature_point(ref("e2", "point")) == pt(5, 10)


def test_driving_radius_and_diameter() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(3, 4), radius=10))
    run(bus, CreateArc(center=pt(40, 0), radius=5, start_angle=0, sweep_angle=120))
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(30, 4), value=30.0))  # diameter
    run(bus, CreateDimension(refs=(curve("e2"),), placement=pt(60, 0), value=8.0))  # radius
    # A radius change never moves the centre.
    assert bus.document.entities[E("e1")] == Circle(center=pt(3, 4), radius=15)
    assert bus.document.entities[E("e2")] == Arc(
        center=pt(40, 0), radius=8, start_angle=0, sweep_angle=120
    )
    assert value(bus, "e3") == 30.0
    assert value(bus, "e4") == 8.0


def test_driving_an_angle_turns_the_second_line_about_the_shared_corner() -> None:
    from caliper.contracts.commands import CreateConstraint
    from caliper.contracts.document import ConstraintType

    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(100, 0)))
    run(bus, CreateLine(start=pt(0, 0), end=pt(50, 50)))
    run(
        bus,
        CreateConstraint(
            type=ConstraintType.COINCIDENT, refs=(ref("e1", "start"), ref("e2", "start"))
        ),
    )
    run(bus, CreateConstraint(type=ConstraintType.FIX, refs=(curve("e1"),)))
    run(bus, CreateDimension(refs=(curve("e1"), curve("e2")), placement=pt(30, 10), value=45.0))
    run(bus, ModifyEntity(id=E("e5"), changes={"value": 60.0}))
    e2 = line(bus, "e2")
    assert e2.start == pt(0, 0)
    assert math.degrees(math.atan2(e2.end.y, e2.end.x)) == pytest.approx(60.0, abs=1e-9)
    assert value(bus, "e5") == pytest.approx(60.0, abs=1e-9)


def test_a_driving_distance_from_a_point_to_a_line() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(100, 0)))
    run(bus, CreatePoint(position=pt(50, 12)))
    run(
        bus,
        CreateDimension(refs=(curve("e1"), ref("e2", "point")), placement=pt(60, 6), value=30.0),
    )
    assert bus.queries.feature_point(ref("e2", "point")) == pt(50, 30)


def test_turning_a_driven_dimension_driving_and_back() -> None:
    bus = slanted()
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(5, 30)))
    assert bus.queries.solve_status().dof == 4  # driven: constrains nothing
    run(bus, ModifyEntity(id=E("e2"), changes={"value": 50.0}))
    assert bus.queries.solve_status().dof == 3
    run(bus, ModifyEntity(id=E("e2"), changes={"value": None}))
    assert bus.queries.solve_status().dof == 4
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(30, 40))


@pytest.mark.parametrize(
    ("command", "field"),
    [
        (CreateDimension(refs=(curve("e1"),), placement=pt(5, 30), value=0.0), "value"),
        (CreateDimension(refs=(curve("e1"),), placement=pt(5, 30), value=-3.0), "value"),
        (
            CreateDistanceDimension(
                a=ref("e1", "start"),
                b=ref("e1", "end"),
                orientation=DistanceOrientation.ALIGNED,
                offset=0,
                value=math.nan,
            ),
            "value",
        ),
        (
            CreateAngleDimension(a=curve("e1"), b=curve("e2"), offset=5, value=180.0),
            "value",
        ),
        (
            CreateDistanceDimension(
                a=ref("e2", "start"),
                b=curve("e1"),
                orientation=DistanceOrientation.HORIZONTAL,
                offset=0,
            ),
            "orientation",
        ),
        (CreateAngleDimension(a=curve("e1"), b=ref("e2", "end"), offset=5), "b.feature"),
    ],
)
def test_bad_dimension_values_and_shapes_are_rejected(command: Command, field: str) -> None:
    bus = slanted()
    run(bus, CreateLine(start=pt(0, 10), end=pt(10, 30)))
    assert rejected(bus.execute(command)).field == field


def test_a_redundant_driving_dimension_is_refused_with_a_hint() -> None:
    bus = slanted()
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(5, 30), value=50.0))
    # A second length, placed on the other side of the line.
    assert bus.queries.infer_dimension([curve("e1")], pt(40, 50)) is DimensionType.LENGTH
    error = rejected(
        bus.execute(CreateDimension(refs=(curve("e1"),), placement=pt(40, 50), value=50.0))
    )
    assert error.code is ErrorCode.CONSTRAINT_REDUNDANT
    assert error.ids == ("e2",)
    assert "driven dimension" in error.message
    # As a driven dimension it is fine: it only measures.
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(40, 50)))


def test_a_conflicting_driving_dimension_names_the_one_in_the_way() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateRectangle(corner=pt(0, 0), width=100, height=50))
    run(bus, CreateDimension(refs=(ref("e1", "bottom"),), placement=pt(50, -10), value=100.0))
    before = bus.document
    error = rejected(
        bus.execute(CreateDimension(refs=(ref("e1", "top"),), placement=pt(50, 60), value=80.0))
    )
    assert error.code is ErrorCode.CONSTRAINT_CONFLICT
    assert error.ids == ("e2",)
    assert bus.document == before
