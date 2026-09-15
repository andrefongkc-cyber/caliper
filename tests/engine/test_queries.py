import itertools
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateArc,
    CreateCircle,
    CreateDistanceDimension,
    CreateLine,
    CreateRadialDimension,
    CreateRectangle,
    ModifyEntity,
)
from caliper.contracts.document import (
    POINT_FEATURES,
    Arc,
    Circle,
    DistanceOrientation,
    Entity,
    EntityId,
    Feature,
    Line,
    Point2,
    RadialMeasure,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import AreaProperties, BoundingBox, Distance, Queries
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry.fake_kernel import FakeKernel

E1, E2, E3 = EntityId("e1"), EntityId("e2"), EntityId("e3")


def P(x: float, y: float) -> Point2:  # noqa: N802
    return Point2(x=x, y=y)


def bus_with(*commands: Command) -> Bus:
    bus = Bus(kernel=FakeKernel())
    for command in commands:
        assert isinstance(bus.execute(command), Applied)
    return bus


def rectangle(
    x: float = 0.0, y: float = 0.0, width: float = 100.0, height: float = 50.0
) -> Command:
    return CreateRectangle(corner=P(x, y), width=width, height=height)


def box(result: BoundingBox | Error) -> tuple[float, float, float, float]:
    assert isinstance(result, BoundingBox), result
    return (result.x_min, result.y_min, result.x_max, result.y_max)


def error_code(result: object) -> ErrorCode:
    assert isinstance(result, Error), result
    return result.code


# --- bounding_box -----------------------------------------------------------------------


def test_rectangle_bounds_are_its_corner_and_size() -> None:
    queries = bus_with(rectangle(10.0, -5.0, 100.0, 50.0)).queries
    assert box(queries.bounding_box()) == (10.0, -5.0, 110.0, 45.0)
    assert box(queries.bounding_box([E1])) == (10.0, -5.0, 110.0, 45.0)


def test_bounds_follow_a_width_edit() -> None:
    bus = bus_with(rectangle())
    bus.execute(ModifyEntity(id=E1, changes={"width": 120.0}))
    result = bus.queries.bounding_box()
    assert isinstance(result, BoundingBox)
    assert (result.width, result.height) == (120.0, 50.0)


def test_bounds_of_each_geometry_type() -> None:
    queries = bus_with(
        CreateLine(start=P(5.0, 1.0), end=P(-2.0, 3.0)),
        CreateCircle(center=P(1.0, 2.0), radius=3.0),
        CreateArc(center=P(0.0, 0.0), radius=10.0, start_angle=0.0, sweep_angle=90.0),
    ).queries
    assert box(queries.bounding_box([E1])) == (-2.0, 1.0, 5.0, 3.0)
    assert box(queries.bounding_box([E2])) == (-2.0, -1.0, 4.0, 5.0)
    assert box(queries.bounding_box([E3])) == (0.0, 0.0, 10.0, 10.0)


C45 = math.cos(math.radians(45.0))


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (315.0, (C45, -C45, 1.0, C45)),
        (-45.0, (C45, -C45, 1.0, C45)),
        (45.0, (-C45, C45, C45, 1.0)),
        (100.0, (-1.0, -0.5, math.cos(math.radians(100.0)), math.sin(math.radians(100.0)))),
    ],
    ids=["crosses 0°", "negative start crosses 0°", "crosses 90°", "crosses 180°"],
)
def test_arc_bounds_include_axis_crossings(
    start: float, expected: tuple[float, float, float, float]
) -> None:
    sweep = 110.0 if start == 100.0 else 90.0  # 100° → 210°
    arc = CreateArc(center=P(0.0, 0.0), radius=1.0, start_angle=start, sweep_angle=sweep)
    assert box(bus_with(arc).queries.bounding_box()) == pytest.approx(expected, abs=1e-15)


def test_whole_document_bounds_cover_all_geometry_and_skip_annotations() -> None:
    queries = bus_with(
        rectangle(0.0, 0.0, 10.0, 10.0),
        CreateCircle(center=P(50.0, 50.0), radius=5.0),
        CreateDistanceDimension(
            a=Ref(entity=E1, feature=Feature.BOTTOM_LEFT),
            b=Ref(entity=E2, feature=Feature.CENTER),
            orientation=DistanceOrientation.ALIGNED,
            offset=500.0,
        ),
    ).queries
    assert box(queries.bounding_box()) == (0.0, 0.0, 55.0, 55.0)
    assert box(queries.bounding_box([E2, E1, E2])) == (0.0, 0.0, 55.0, 55.0)


def test_bounding_box_reports_bad_input_as_errors() -> None:
    queries = bus_with(
        rectangle(),
        CreateDistanceDimension(
            a=Ref(entity=E1, feature=Feature.BOTTOM_LEFT),
            b=Ref(entity=E1, feature=Feature.TOP_RIGHT),
            orientation=DistanceOrientation.ALIGNED,
            offset=5.0,
        ),
    ).queries
    assert error_code(queries.bounding_box([EntityId("nope")])) == ErrorCode.ENTITY_NOT_FOUND
    assert error_code(queries.bounding_box([E1, E2])) == ErrorCode.ENTITY_WRONG_KIND
    assert error_code(Bus().queries.bounding_box()) == ErrorCode.SELECTION_EMPTY


coordinate = st.floats(min_value=-1e4, max_value=1e4)
size = st.floats(min_value=0.01, max_value=1e4)


@given(
    center=st.tuples(coordinate, coordinate),
    radius=size,
    start=st.floats(min_value=-720.0, max_value=720.0),
    sweep=st.floats(min_value=0.01, max_value=359.99),
)
def test_arc_bounds_are_tight_around_the_curve(
    center: tuple[float, float], radius: float, start: float, sweep: float
) -> None:
    arc = CreateArc(center=P(*center), radius=radius, start_angle=start, sweep_angle=sweep)
    result = bus_with(arc).queries.bounding_box()
    assert isinstance(result, BoundingBox)

    samples = 3600
    angles = [math.radians(start + sweep * i / samples) for i in range(samples + 1)]
    xs = [center[0] + radius * math.cos(a) for a in angles]
    ys = [center[1] + radius * math.sin(a) for a in angles]
    # Sampling every ≤0.1° misses an extreme by at most r·(1 - cos 0.05°) < 4e-7·r.
    slack = 1e-6 * radius + 1e-9 * max(1.0, abs(center[0]), abs(center[1]))
    sampled = (min(xs), min(ys), max(xs), max(ys))
    assert box(result) == pytest.approx(sampled, abs=slack)


@given(
    rects=st.lists(st.tuples(coordinate, coordinate, size, size), min_size=1, max_size=6),
    dx=coordinate,
    dy=coordinate,
)
def test_bounds_translate_with_the_geometry(
    rects: list[tuple[float, float, float, float]], dx: float, dy: float
) -> None:
    original = bus_with(*(rectangle(x, y, w, h) for x, y, w, h in rects)).queries.bounding_box()
    moved = bus_with(
        *(rectangle(x + dx, y + dy, w, h) for x, y, w, h in rects)
    ).queries.bounding_box()
    assert isinstance(original, BoundingBox)
    assert isinstance(moved, BoundingBox)
    tol = 1e-9 * max(1.0, abs(dx), abs(dy), *(abs(v) for r in rects for v in r))
    assert moved.x_min == pytest.approx(original.x_min + dx, abs=tol)
    assert moved.y_min == pytest.approx(original.y_min + dy, abs=tol)
    assert moved.width == pytest.approx(original.width, abs=tol)
    assert moved.height == pytest.approx(original.height, abs=tol)


# --- entity_at_point --------------------------------------------------------------------


def test_a_rectangle_is_hit_on_its_outline_and_anywhere_inside() -> None:
    queries = bus_with(rectangle()).queries
    assert queries.entity_at_point(P(50.0, 0.0), 0.0) == E1  # bottom edge
    assert queries.entity_at_point(P(100.5, 25.0), 1.0) == E1  # just outside the right edge
    assert queries.entity_at_point(P(101.0, 51.0), 1.5) == E1  # off the corner: √2 away
    assert queries.entity_at_point(P(101.0, 51.0), 1.4) is None
    assert queries.entity_at_point(P(50.0, 25.0), 0.0) == E1  # middle of the inside
    assert queries.entity_at_point(P(50.0, 60.0), 1.0) is None  # outside, far from an edge


def test_the_smallest_shape_around_a_click_wins() -> None:
    queries = bus_with(
        rectangle(0.0, 0.0, 100.0, 100.0),
        CreateCircle(center=P(50.0, 50.0), radius=10.0),
        rectangle(40.0, 40.0, 2.0, 2.0),
    ).queries
    assert queries.entity_at_point(P(20.0, 20.0), 1.0) == E1
    assert queries.entity_at_point(P(55.0, 55.0), 1.0) == E2  # inside the circle too
    assert queries.entity_at_point(P(41.0, 41.0), 0.1) == E3  # inside all three


def test_an_outline_under_the_pointer_beats_a_shape_around_it() -> None:
    queries = bus_with(
        rectangle(0.0, 0.0, 100.0, 100.0),
        CreateLine(start=P(10.0, 50.0), end=P(90.0, 50.0)),
        CreateArc(center=P(50.0, 50.0), radius=30.0, start_angle=0.0, sweep_angle=90.0),
    ).queries
    assert queries.entity_at_point(P(30.0, 50.4), 0.5) == E2  # near the line, inside e1
    assert queries.entity_at_point(P(30.0, 51.0), 0.5) == E1  # too far from the line
    assert queries.entity_at_point(P(50.0, 80.2), 0.5) == E3  # near the arc
    assert queries.entity_at_point(P(99.8, 30.0), 0.5) == E1  # its own edge


def test_identical_shapes_around_a_click_go_to_the_lower_id() -> None:
    queries = bus_with(
        rectangle(),
        CreateRectangle(id=EntityId("a1"), corner=P(0.0, 0.0), width=100.0, height=50.0),
    ).queries
    assert queries.entity_at_point(P(50.0, 25.0), 1.0) == EntityId("a1")


def test_the_nearest_entity_wins_and_ties_go_to_the_lower_id() -> None:
    queries = bus_with(rectangle(0.0, 0.0), rectangle(0.0, 3.0), rectangle(0.0, 3.0)).queries
    assert queries.entity_at_point(P(50.0, 1.0), 5.0) == E1  # 1 from e1, 2 from e2/e3
    assert queries.entity_at_point(P(50.0, 2.5), 5.0) == E2  # e2 and e3 coincide


def test_each_geometry_type_is_hit_on_its_curve() -> None:
    queries = bus_with(
        CreateLine(start=P(0.0, 0.0), end=P(10.0, 0.0)),
        CreateCircle(center=P(100.0, 0.0), radius=10.0),
        CreateArc(center=P(200.0, 0.0), radius=10.0, start_angle=0.0, sweep_angle=90.0),
    ).queries
    assert queries.entity_at_point(P(5.0, 0.5), 1.0) == E1
    assert queries.entity_at_point(P(11.0, 0.0), 0.5) is None  # past the line's end
    assert queries.entity_at_point(P(100.0, 10.2), 0.5) == E2
    assert queries.entity_at_point(P(100.0, 0.0), 0.5) == E2  # inside the circle
    assert queries.entity_at_point(P(200.0 + 7.07, 7.07), 0.1) == E3  # 45°, on the arc
    assert queries.entity_at_point(P(190.0, 0.0), 0.5) is None  # 180°, outside the sweep
    assert queries.entity_at_point(P(210.0, -0.3), 0.5) == E3  # near the start point


def test_annotations_are_never_hit() -> None:
    queries = bus_with(
        CreateCircle(center=P(0.0, 0.0), radius=1.0),
        CreateCircle(center=P(100.0, 0.0), radius=1.0),
        CreateDistanceDimension(
            a=Ref(entity=E1, feature=Feature.CENTER),
            b=Ref(entity=E2, feature=Feature.CENTER),
            orientation=DistanceOrientation.HORIZONTAL,
            offset=0.0,
        ),
    ).queries
    assert queries.entity_at_point(P(50.0, 0.0), 1.0) is None


@given(
    rect=st.tuples(coordinate, coordinate, size, size),
    t=st.floats(min_value=0.0, max_value=1.0),
    edge=st.integers(min_value=0, max_value=3),
)
def test_any_point_on_a_rectangle_edge_hits_it(
    rect: tuple[float, float, float, float], t: float, edge: int
) -> None:
    x, y, w, h = rect
    point = [P(x + t * w, y), P(x + w, y + t * h), P(x + t * w, y + h), P(x, y + t * h)][edge]
    tolerance = 1e-9 * max(1.0, abs(x), abs(y), w, h)
    assert bus_with(rectangle(x, y, w, h)).queries.entity_at_point(point, tolerance) == E1


# --- feature_point ----------------------------------------------------------------------


def at(queries: Queries, entity: EntityId, feature: Feature) -> tuple[float, float]:
    result = queries.feature_point(Ref(entity=entity, feature=feature))
    assert isinstance(result, Point2), result
    return (result.x, result.y)


def test_feature_points_of_each_geometry_type() -> None:
    queries = bus_with(
        CreateLine(start=P(0.0, 0.0), end=P(10.0, 4.0)),
        CreateCircle(center=P(1.0, 2.0), radius=3.0),
        CreateArc(center=P(0.0, 0.0), radius=10.0, start_angle=0.0, sweep_angle=90.0),
        rectangle(10.0, 20.0, 100.0, 50.0),
    ).queries
    assert at(queries, E1, Feature.START) == (0.0, 0.0)
    assert at(queries, E1, Feature.END) == (10.0, 4.0)
    assert at(queries, E1, Feature.MID) == (5.0, 2.0)
    assert at(queries, E2, Feature.CENTER) == (1.0, 2.0)
    assert at(queries, E3, Feature.CENTER) == (0.0, 0.0)
    assert at(queries, E3, Feature.START) == (10.0, 0.0)
    assert at(queries, E3, Feature.END) == (0.0, 10.0)  # exact at multiples of 90°
    assert at(queries, E3, Feature.MID) == pytest.approx((10.0 * C45, 10.0 * C45))
    e4 = EntityId("e4")
    assert at(queries, e4, Feature.BOTTOM_LEFT) == (10.0, 20.0)
    assert at(queries, e4, Feature.BOTTOM_RIGHT) == (110.0, 20.0)
    assert at(queries, e4, Feature.TOP_RIGHT) == (110.0, 70.0)
    assert at(queries, e4, Feature.TOP_LEFT) == (10.0, 70.0)
    assert at(queries, e4, Feature.CENTER) == (60.0, 45.0)


def test_every_type_exposes_exactly_its_contract_features() -> None:
    bus = bus_with(
        CreateLine(start=P(0.0, 0.0), end=P(10.0, 4.0)),
        CreateCircle(center=P(1.0, 2.0), radius=3.0),
        CreateArc(center=P(0.0, 0.0), radius=10.0, start_angle=30.0, sweep_angle=200.0),
        rectangle(),
    )
    for id, entity in bus.document.entities.items():
        for feature in Feature:
            result = bus.queries.feature_point(Ref(entity=id, feature=feature))
            if feature in POINT_FEATURES[type(entity)]:
                assert isinstance(result, Point2), (entity.kind, feature, result)
            else:
                assert error_code(result) == ErrorCode.REFERENCE_INVALID_FEATURE


def test_feature_point_follows_a_width_edit() -> None:
    bus = bus_with(rectangle())
    bus.execute(ModifyEntity(id=E1, changes={"width": 120.0}))
    assert at(bus.queries, E1, Feature.TOP_RIGHT) == (120.0, 50.0)


@pytest.mark.parametrize(
    ("ref", "code", "field"),
    [
        (
            Ref(entity=EntityId("nope"), feature=Feature.CENTER),
            ErrorCode.ENTITY_NOT_FOUND,
            "ref.entity",
        ),
        (Ref(entity=E2, feature=Feature.CENTER), ErrorCode.ENTITY_WRONG_KIND, "ref.entity"),
        (Ref(entity=E1, feature=Feature.START), ErrorCode.REFERENCE_INVALID_FEATURE, "ref.feature"),
        (
            Ref(entity=EntityId("Not An Id"), feature=Feature.CENTER),
            ErrorCode.ID_INVALID,
            "ref.entity",
        ),
        (Ref(entity=E1, feature="nope"), ErrorCode.REFERENCE_INVALID_FEATURE, "ref.feature"),
        ("e1.center", ErrorCode.VALUE_WRONG_TYPE, "ref"),
    ],
    ids=[
        "unknown entity",
        "annotation",
        "feature not on a rectangle",
        "malformed id",
        "unknown feature",
        "not a ref",
    ],
)
def test_feature_point_reports_bad_references_as_errors(
    ref: object, code: ErrorCode, field: str
) -> None:
    queries = bus_with(
        rectangle(),
        CreateDistanceDimension(
            a=Ref(entity=E1, feature=Feature.BOTTOM_LEFT),
            b=Ref(entity=E1, feature=Feature.TOP_RIGHT),
            orientation=DistanceOrientation.ALIGNED,
            offset=5.0,
        ),
    ).queries
    result = queries.feature_point(ref)  # type: ignore[arg-type]
    assert isinstance(result, Error), result
    assert (result.code, result.field) == (code, field)


# --- nearest_feature --------------------------------------------------------------------


def test_nearest_feature_snaps_to_a_nearby_corner() -> None:
    queries = bus_with(rectangle()).queries
    assert queries.nearest_feature(P(100.6, 0.4), 1.0) == Ref(
        entity=E1, feature=Feature.BOTTOM_RIGHT
    )
    assert queries.nearest_feature(P(50.0, 25.3), 0.5) == Ref(entity=E1, feature=Feature.CENTER)
    assert queries.nearest_feature(P(50.0, 0.0), 1.0) is None  # on an edge, but far from features


def test_nearest_feature_measures_to_features_not_outlines() -> None:
    queries = bus_with(
        CreateLine(start=P(0.0, 1.0), end=P(200.0, 1.0)),  # outline 1 away, mid at 100
        CreateCircle(center=P(52.0, 0.0), radius=30.0),  # center 2 away
    ).queries
    assert queries.nearest_feature(P(50.0, 0.0), 5.0) == Ref(entity=E2, feature=Feature.CENTER)


def test_coincident_features_go_to_the_lower_id() -> None:
    queries = bus_with(
        CreateLine(start=P(100.0, 0.0), end=P(200.0, 0.0)),
        rectangle(),  # bottom-right corner at the line's start
    ).queries
    assert queries.nearest_feature(P(100.0, 0.1), 1.0) == Ref(entity=E1, feature=Feature.START)


@given(
    center=st.tuples(coordinate, coordinate),
    radius=size,
    start=st.floats(min_value=-720.0, max_value=720.0),
    sweep=st.floats(min_value=0.01, max_value=359.99),
)
def test_arc_features_lie_on_the_arc_and_snap_back_to_themselves(
    center: tuple[float, float], radius: float, start: float, sweep: float
) -> None:
    arc = CreateArc(center=P(*center), radius=radius, start_angle=start, sweep_angle=sweep)
    queries = bus_with(arc).queries
    scale = 1e-9 * max(1.0, radius, abs(center[0]), abs(center[1]))
    for feature in POINT_FEATURES[Arc]:
        point = queries.feature_point(Ref(entity=E1, feature=feature))
        assert isinstance(point, Point2)
        snapped = queries.nearest_feature(point, 0.0)
        assert snapped is not None
        assert queries.feature_point(snapped) == point
        if feature is not Feature.CENTER:
            assert queries.entity_at_point(point, scale * 10) == E1


@pytest.mark.parametrize("method", ["entity_at_point", "nearest_feature"])
@pytest.mark.parametrize(
    ("point", "tolerance"),
    [
        (P(100.0, 0.0), -1.0),
        (P(100.0, 0.0), math.nan),
        (P(100.0, 0.0), math.inf),
        (P(math.nan, 0.0), 1.0),
        (P(100.0, math.inf), 1.0),
    ],
)
def test_invalid_pick_input_matches_nothing(method: str, point: Point2, tolerance: float) -> None:
    queries = bus_with(rectangle()).queries
    assert getattr(queries, method)(point, tolerance) is None


# --- Snapshot binding and the rest of the protocol --------------------------------------


def test_queries_answer_for_the_snapshot_they_were_taken_from() -> None:
    bus = bus_with(rectangle())
    before = bus.queries
    bus.execute(ModifyEntity(id=E1, changes={"width": 120.0}))
    assert box(before.bounding_box()) == (0.0, 0.0, 100.0, 50.0)
    assert box(bus.queries.bounding_box()) == (0.0, 0.0, 120.0, 50.0)


# --- measure_distance -------------------------------------------------------------------


def test_measure_distance_between_features() -> None:
    queries = bus_with(rectangle(0.0, 0.0, 30.0, 40.0)).queries
    bl, tr = Ref(entity=E1, feature=Feature.BOTTOM_LEFT), Ref(entity=E1, feature=Feature.TOP_RIGHT)
    assert queries.measure_distance(bl, tr) == Distance(value=50.0, dx=30.0, dy=40.0)
    assert queries.measure_distance(tr, bl) == Distance(value=50.0, dx=-30.0, dy=-40.0)
    assert queries.measure_distance(bl, bl) == Distance(value=0.0, dx=0.0, dy=0.0)


def test_measure_distance_names_the_bad_reference() -> None:
    queries = bus_with(rectangle()).queries
    good = Ref(entity=E1, feature=Feature.CENTER)
    missing = Ref(entity=E2, feature=Feature.CENTER)
    for a, b, field in [(missing, good, "a.entity"), (good, missing, "b.entity")]:
        result = queries.measure_distance(a, b)
        assert isinstance(result, Error)
        assert (result.code, result.field) == (ErrorCode.ENTITY_NOT_FOUND, field)


# --- dimension_value --------------------------------------------------------------------


def distance_dimension(
    a: Feature, b: Feature, orientation: DistanceOrientation, entity: EntityId = E1
) -> Command:
    return CreateDistanceDimension(
        a=Ref(entity=entity, feature=a),
        b=Ref(entity=entity, feature=b),
        orientation=orientation,
        offset=5.0,
    )


def test_distance_dimension_values_follow_orientation() -> None:
    queries = bus_with(
        rectangle(0.0, 0.0, 30.0, 40.0),
        distance_dimension(Feature.BOTTOM_LEFT, Feature.TOP_RIGHT, DistanceOrientation.ALIGNED),
        distance_dimension(Feature.TOP_RIGHT, Feature.BOTTOM_LEFT, DistanceOrientation.HORIZONTAL),
        distance_dimension(Feature.TOP_RIGHT, Feature.BOTTOM_LEFT, DistanceOrientation.VERTICAL),
    ).queries
    assert queries.dimension_value(E2) == 50.0
    assert queries.dimension_value(E3) == 30.0  # absolute, whatever the a→b direction
    assert queries.dimension_value(EntityId("e4")) == 40.0


def test_radial_dimension_values() -> None:
    queries = bus_with(
        CreateCircle(center=P(0.0, 0.0), radius=7.5),
        CreateArc(center=P(0.0, 0.0), radius=2.0, start_angle=10.0, sweep_angle=20.0),
        CreateRadialDimension(target=E1, measure=RadialMeasure.DIAMETER, label_angle=45.0),
        CreateRadialDimension(target=E2, measure=RadialMeasure.RADIUS, label_angle=45.0),
    ).queries
    assert queries.dimension_value(E3) == 15.0
    assert queries.dimension_value(EntityId("e4")) == 2.0


def test_a_dimension_value_follows_its_geometry() -> None:
    bus = bus_with(
        rectangle(),
        distance_dimension(
            Feature.BOTTOM_LEFT, Feature.BOTTOM_RIGHT, DistanceOrientation.HORIZONTAL
        ),
    )
    bus.execute(ModifyEntity(id=E1, changes={"width": 120.0}))
    assert bus.queries.dimension_value(E2) == 120.0


def test_dimension_value_reports_bad_ids() -> None:
    queries = bus_with(rectangle()).queries
    assert error_code(queries.dimension_value(E2)) == ErrorCode.ENTITY_NOT_FOUND
    assert error_code(queries.dimension_value(E1)) == ErrorCode.ENTITY_WRONG_KIND
    assert error_code(queries.dimension_value(EntityId("Bad Id"))) == ErrorCode.ID_INVALID


# --- area_properties --------------------------------------------------------------------


def test_area_properties_of_a_rectangle_and_a_circle() -> None:
    queries = bus_with(
        rectangle(10.0, 20.0, 6.0, 3.0), CreateCircle(center=P(1.0, 2.0), radius=2.0)
    ).queries
    assert queries.area_properties([E1]) == AreaProperties(
        area=18.0, centroid=P(13.0, 21.5), ixx=6.0 * 27.0 / 12, iyy=3.0 * 216.0 / 12, ixy=0.0
    )
    circle = queries.area_properties([E2])
    assert isinstance(circle, AreaProperties)
    assert circle.area == pytest.approx(4.0 * math.pi)
    assert circle.centroid == P(1.0, 2.0)


def test_area_properties_through_the_occt_kernel() -> None:
    occt = pytest.importorskip(
        "caliper.engine.geometry.occt_kernel", reason="the occt extra isn't installed"
    )
    bus = Bus(kernel=occt.OCCTKernel())
    bus.execute(rectangle(10.0, 20.0, 6.0, 3.0))
    bus.execute(CreateCircle(center=P(1.0, 2.0), radius=2.0))
    fake = bus_with(rectangle(10.0, 20.0, 6.0, 3.0), CreateCircle(center=P(1.0, 2.0), radius=2.0))
    for id in (E1, E2):
        real, expected = bus.queries.area_properties([id]), fake.queries.area_properties([id])
        assert isinstance(real, AreaProperties)
        assert isinstance(expected, AreaProperties)
        assert real.area == pytest.approx(expected.area, rel=1e-9)
        assert real.centroid.x == pytest.approx(expected.centroid.x, abs=1e-9)
        assert real.ixx == pytest.approx(expected.ixx, rel=1e-9)
    line = bus.execute(CreateLine(start=P(0.0, 0.0), end=P(1.0, 1.0)))
    assert isinstance(line, Applied)
    assert error_code(bus.queries.area_properties([E3])) == ErrorCode.PROFILE_NOT_CLOSED


def test_area_properties_errors() -> None:
    queries = bus_with(
        rectangle(),
        CreateLine(start=P(0.0, 0.0), end=P(1.0, 1.0)),
        distance_dimension(Feature.BOTTOM_LEFT, Feature.TOP_RIGHT, DistanceOrientation.ALIGNED),
    ).queries
    assert error_code(queries.area_properties([])) == ErrorCode.SELECTION_EMPTY
    assert error_code(queries.area_properties([EntityId("nope")])) == ErrorCode.ENTITY_NOT_FOUND
    assert error_code(queries.area_properties([E3])) == ErrorCode.ENTITY_WRONG_KIND
    assert error_code(queries.area_properties([E2])) == ErrorCode.PROFILE_NOT_CLOSED
    assert error_code(queries.area_properties([E1, E1])) == ErrorCode.PROFILE_NOT_CLOSED


def test_area_properties_without_a_kernel_says_so() -> None:
    bus = Bus(kernel=None)
    bus.execute(rectangle())
    assert error_code(bus.queries.area_properties([E1])) == ErrorCode.KERNEL_UNAVAILABLE
    assert error_code(bus.queries.area_properties([E2])) == ErrorCode.ENTITY_NOT_FOUND


# --- entities_in_box --------------------------------------------------------------------


def B(x_min: float, y_min: float, x_max: float, y_max: float) -> BoundingBox:  # noqa: N802
    return BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)


def test_window_selection_needs_the_whole_entity_inside() -> None:
    queries = bus_with(
        rectangle(0.0, 0.0, 10.0, 10.0),
        CreateCircle(center=P(50.0, 50.0), radius=5.0),
        CreateLine(start=P(0.0, 20.0), end=P(100.0, 20.0)),
    ).queries
    assert queries.entities_in_box(B(-1.0, -1.0, 60.0, 60.0), crossing=False) == (E1, E2)
    assert queries.entities_in_box(B(0.0, 0.0, 10.0, 10.0), crossing=False) == (E1,)  # edges count
    assert queries.entities_in_box(B(-1.0, -1.0, 99.0, 60.0), crossing=False) == (E1, E2)


def test_crossing_selection_takes_anything_the_box_touches() -> None:
    queries = bus_with(
        rectangle(0.0, 0.0, 100.0, 100.0),
        CreateCircle(center=P(50.0, 50.0), radius=10.0),
        CreateLine(start=P(-50.0, 50.0), end=P(150.0, 50.0)),
    ).queries
    # Inside the rectangle, clear of the circle and the line.
    assert queries.entities_in_box(B(20.0, 70.0, 30.0, 80.0), crossing=True) == (E1,)
    # Inside the circle, and across the line.
    assert queries.entities_in_box(B(45.0, 45.0, 55.0, 55.0), crossing=True) == (E1, E2, E3)
    # Across the rectangle's left edge and the line's left part.
    assert queries.entities_in_box(B(-5.0, 40.0, 5.0, 60.0), crossing=True) == (E1, E3)
    # Just touching the circle's top, inside the rectangle.
    assert queries.entities_in_box(B(49.0, 60.0, 51.0, 65.0), crossing=True) == (E1, E2)
    # Outside everything.
    assert queries.entities_in_box(B(200.0, 200.0, 210.0, 210.0), crossing=True) == ()


def test_crossing_an_arc_only_counts_the_drawn_part() -> None:
    queries = bus_with(
        CreateArc(center=P(0.0, 0.0), radius=10.0, start_angle=0.0, sweep_angle=90.0)
    ).queries
    assert (
        queries.entities_in_box(B(-11.0, -1.0, -9.0, 1.0), crossing=True) == ()
    )  # 180°: not drawn
    assert queries.entities_in_box(B(6.0, 6.0, 8.0, 8.0), crossing=True) == (
        E1,
    )  # 45°, crosses edges
    assert queries.entities_in_box(B(9.0, -1.0, 11.0, 1.0), crossing=True) == (
        E1,
    )  # holds the start


@pytest.mark.parametrize(
    "box",
    [B(1.0, 0.0, 0.0, 1.0), B(0.0, math.nan, 1.0, 1.0), B(0.0, 0.0, math.inf, 1.0), "e1"],
)
def test_an_invalid_box_selects_nothing(box: BoundingBox) -> None:
    queries = bus_with(rectangle()).queries
    assert queries.entities_in_box(box, crossing=True) == ()
    assert queries.entities_in_box(box, crossing=False) == ()


geometry_commands = st.one_of(
    st.builds(
        lambda x, y, dx, dy: CreateLine(start=P(x, y), end=P(x + dx, y + dy)),
        coordinate,
        coordinate,
        size,
        st.floats(min_value=-1e4, max_value=1e4),
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
    st.builds(rectangle, coordinate, coordinate, size, size),
)


def outline_samples(entity: Entity, count: int = 720) -> list[Point2]:
    """Points along an entity's outline, computed independently of the engine."""

    def along(a: Point2, b: Point2) -> list[Point2]:
        return [
            P(a.x + (b.x - a.x) * i / count, a.y + (b.y - a.y) * i / count)
            for i in range(count + 1)
        ]

    def around(c: Point2, r: float, start: float, sweep: float) -> list[Point2]:
        angles = (math.radians(start + sweep * i / count) for i in range(count + 1))
        return [P(c.x + r * math.cos(t), c.y + r * math.sin(t)) for t in angles]

    match entity:
        case Line(start=a, end=b):
            return along(a, b)
        case Circle(center=c, radius=r):
            return around(c, r, 0.0, 360.0)
        case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
            return around(c, r, start, sweep)
        case Rectangle(corner=c, width=w, height=h):
            corners = [c, P(c.x + w, c.y), P(c.x + w, c.y + h), P(c.x, c.y + h), c]
            return [q for a, b in itertools.pairwise(corners) for q in along(a, b)]
    raise AssertionError(entity)


def grown(box: BoundingBox, by: float) -> BoundingBox:
    return B(box.x_min - by, box.y_min - by, box.x_max + by, box.y_max + by)


def holds(box: BoundingBox, p: Point2) -> bool:
    return box.x_min <= p.x <= box.x_max and box.y_min <= p.y <= box.y_max


@given(command=geometry_commands, data=st.data())
def test_box_selection_agrees_with_points_sampled_along_the_outline(
    command: Command, data: st.DataObject
) -> None:
    bus = bus_with(command)
    bounds = bus.queries.bounding_box()
    assert isinstance(bounds, BoundingBox)
    # Boxes around the entity, so they overlap it, cross it, and miss it about equally often.
    reach = max(bounds.width, bounds.height, 1.0)
    xs = st.floats(min_value=bounds.x_min - reach, max_value=bounds.x_max + reach)
    ys = st.floats(min_value=bounds.y_min - reach, max_value=bounds.y_max + reach)
    x0, x1 = sorted((data.draw(xs), data.draw(xs)))
    y0, y1 = sorted((data.draw(ys), data.draw(ys)))
    box = B(x0, y0, x1, y1)
    window = bus.queries.entities_in_box(box, crossing=False)
    crossing = bus.queries.entities_in_box(box, crossing=True)
    assert set(window) <= set(crossing)

    samples = outline_samples(bus.document.entities[E1])
    scale = max(1.0, *(abs(v) for p in samples for v in (p.x, p.y)))
    rounding = 1e-9 * scale
    # Between neighbouring samples a curve bulges out by at most r*(1 - cos(0.25°)) < 1e-5·r.
    sag = 1e-5 * scale
    if any(holds(grown(box, -rounding), p) for p in samples):
        assert crossing == (E1,), "a sampled outline point is in the box"
    spacing = max(math.dist((p.x, p.y), (q.x, q.y)) for p, q in itertools.pairwise(samples))
    if not any(holds(grown(box, sag + spacing), p) for p in samples):
        # Clear of the outline, the box is either wholly inside a closed shape or misses it.
        corner = P(box.x_min, box.y_min)
        match bus.document.entities[E1]:
            case Circle(center=c, radius=r) if math.dist((c.x, c.y), (corner.x, corner.y)) < r:
                assert crossing == (E1,), "the box is inside the circle"
            case Rectangle(corner=c, width=w, height=h) if (
                c.x < corner.x < c.x + w and c.y < corner.y < c.y + h
            ):
                assert crossing == (E1,), "the box is inside the rectangle"
            case _:
                assert crossing == (), "the box misses the entity"
    if all(holds(grown(box, -sag), p) for p in samples):
        assert window == (E1,), "every sampled point is well inside the box"
    if any(not holds(grown(box, rounding), p) for p in samples):
        assert window == (), "a sampled point is outside the box"


@given(commands=st.lists(geometry_commands, min_size=1, max_size=5))
def test_a_window_around_the_whole_document_selects_every_entity(commands: list[Command]) -> None:
    queries = bus_with(*commands).queries
    box = queries.bounding_box()
    assert isinstance(box, BoundingBox)
    assert len(queries.entities_in_box(box, crossing=False)) == len(commands)
    assert len(queries.entities_in_box(box, crossing=True)) == len(commands)
