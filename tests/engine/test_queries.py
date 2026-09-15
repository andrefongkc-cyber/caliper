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
    CreateRectangle,
    ModifyEntity,
)
from caliper.contracts.document import (
    DistanceOrientation,
    Document,
    EntityId,
    Feature,
    Point2,
    Ref,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import BoundingBox, Expectation, Metric
from caliper.engine.commands.bus import Bus
from caliper.engine.queries import DocumentQueries

E1, E2, E3 = EntityId("e1"), EntityId("e2"), EntityId("e3")


def P(x: float, y: float) -> Point2:  # noqa: N802
    return Point2(x=x, y=y)


def bus_with(*commands: Command) -> Bus:
    bus = Bus()
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


def test_rectangle_is_hit_on_its_outline_not_its_interior() -> None:
    queries = bus_with(rectangle()).queries
    assert queries.entity_at_point(P(50.0, 0.0), 0.0) == E1  # bottom edge
    assert queries.entity_at_point(P(100.5, 25.0), 1.0) == E1  # just outside the right edge
    assert queries.entity_at_point(P(99.5, 25.0), 1.0) == E1  # just inside the right edge
    assert queries.entity_at_point(P(101.0, 51.0), 1.5) == E1  # off the corner: √2 away
    assert queries.entity_at_point(P(101.0, 51.0), 1.4) is None
    assert queries.entity_at_point(P(50.0, 25.0), 1.0) is None  # middle of the interior


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
    assert queries.entity_at_point(P(100.0, 0.0), 0.5) is None  # circle center
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


@pytest.mark.parametrize(
    ("point", "tolerance"),
    [
        (P(50.0, 0.0), -1.0),
        (P(50.0, 0.0), math.nan),
        (P(50.0, 0.0), math.inf),
        (P(math.nan, 0.0), 1.0),
        (P(50.0, math.inf), 1.0),
    ],
)
def test_invalid_hit_test_input_matches_nothing(point: Point2, tolerance: float) -> None:
    assert bus_with(rectangle()).queries.entity_at_point(point, tolerance) is None


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


# --- Snapshot binding and the rest of the protocol --------------------------------------


def test_queries_answer_for_the_snapshot_they_were_taken_from() -> None:
    bus = bus_with(rectangle())
    before = bus.queries
    bus.execute(ModifyEntity(id=E1, changes={"width": 120.0}))
    assert box(before.bounding_box()) == (0.0, 0.0, 100.0, 50.0)
    assert box(bus.queries.bounding_box()) == (0.0, 0.0, 120.0, 50.0)


def test_queries_landing_in_v1_say_so() -> None:
    queries = DocumentQueries(Document.empty())
    ref = Ref(entity=E1, feature=Feature.CENTER)
    everything = BoundingBox(x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0)
    with pytest.raises(NotImplementedError):
        queries.feature_point(ref)
    with pytest.raises(NotImplementedError):
        queries.measure_distance(ref, ref)
    with pytest.raises(NotImplementedError):
        queries.entities_in_box(everything, crossing=False)
    with pytest.raises(NotImplementedError):
        queries.nearest_feature(P(0.0, 0.0), 1.0)
    with pytest.raises(NotImplementedError):
        queries.dimension_value(E1)
    with pytest.raises(NotImplementedError):
        queries.area_properties([E1])
    with pytest.raises(NotImplementedError):
        queries.check(Expectation(metric=Metric.BBOX_WIDTH, expected=1.0, tolerance=0.0))
