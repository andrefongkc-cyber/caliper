"""The grid behind picking and box selection narrows the search; it never changes an answer.

Each picker is compared with the check-everything loop it replaced, kept here as the
reference, over random sketches: small and huge shapes, clicks near and far, tolerances from
zero to enormous, and boxes of every size.
"""

import math
from types import MappingProxyType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Command,
    CreateArc,
    CreateCircle,
    CreateDistanceDimension,
    CreateLine,
    CreatePoint,
    CreateRectangle,
)
from caliper.contracts.document import (
    Circle,
    DistanceOrientation,
    Document,
    EntityId,
    Feature,
    Point,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.queries import BoundingBox
from caliper.engine import spatial
from caliper.engine.commands.bus import Bus
from caliper.engine.queries import (
    _GEOMETRY,
    DocumentQueries,
    _bounds,
    _contains,
    _curve_distances,
    _distance,
    _enclosing_area,
    _features,
    _touches,
    _valid_box,
)

# --- The reference: every entity, every time -------------------------------------------------


def entity_at_point(document: Document, point: Point2, tolerance: float) -> EntityId | None:
    if not (math.isfinite(point.x) and math.isfinite(point.y)):
        return None
    if not (math.isfinite(tolerance) and tolerance >= 0):
        return None
    ranked: list[tuple[int, float, EntityId]] = []
    for id, entity in document.entities.items():
        if not isinstance(entity, _GEOMETRY):
            continue
        if (distance := _distance(entity, point)) <= tolerance:
            ranked.append((0, distance, id))
        elif (area := _enclosing_area(entity, point)) is not None:
            ranked.append((1, area, id))
    return min(ranked)[2] if ranked else None


def nearest_feature(document: Document, point: Point2, tolerance: float) -> Ref | None:
    if not (math.isfinite(point.x) and math.isfinite(point.y)):
        return None
    if not (math.isfinite(tolerance) and tolerance >= 0):
        return None
    candidates = (
        (math.hypot(at.x - point.x, at.y - point.y), id, feature)
        for id, entity in document.entities.items()
        for feature, at in _features(entity).items()
    )
    nearest = min((c for c in candidates if c[0] <= tolerance), default=None)
    return None if nearest is None else Ref(entity=nearest[1], feature=nearest[2])


def reference_at_point(document: Document, point: Point2, tolerance: float) -> Ref | None:
    if (feature := nearest_feature(document, point, tolerance)) is not None:
        return feature
    if not (math.isfinite(point.x) and math.isfinite(point.y)):
        return None
    if not (math.isfinite(tolerance) and tolerance >= 0):
        return None
    ranked = [
        (distance, id, index, ref)
        for id, entity in document.entities.items()
        if isinstance(entity, _GEOMETRY)
        for index, (ref, distance) in enumerate(_curve_distances(id, entity, point))
        if distance <= tolerance
    ]
    return min(ranked)[3] if ranked else None


def entities_in_box(document: Document, box: BoundingBox, crossing: bool) -> tuple[EntityId, ...]:
    if not _valid_box(box):
        return ()
    return tuple(
        sorted(
            id
            for id, entity in document.entities.items()
            if isinstance(entity, _GEOMETRY)
            and (_touches(entity, box) if crossing else _contains(box, _bounds(entity)))
        )
    )


# --- Random sketches ------------------------------------------------------------------------

coordinate = st.one_of(
    st.integers(-20, 20).map(lambda v: 5.0 * v),
    st.floats(min_value=-100, max_value=100),
)
points = st.builds(Point2, x=coordinate, y=coordinate)
size = st.one_of(st.sampled_from([0.5, 5.0, 10.0]), st.floats(min_value=0.01, max_value=500))


@st.composite
def geometry(draw: st.DrawFn) -> Command:
    match draw(st.sampled_from(["point", "line", "circle", "arc", "rectangle"])):
        case "point":
            return CreatePoint(position=draw(points))
        case "line":
            start = draw(points)
            end = Point2(x=start.x + draw(size), y=start.y + draw(coordinate))
            return CreateLine(start=start, end=end)
        case "circle":
            return CreateCircle(center=draw(points), radius=draw(size))
        case "arc":
            return CreateArc(
                center=draw(points),
                radius=draw(size),
                start_angle=draw(st.floats(min_value=0, max_value=359)),
                sweep_angle=draw(st.floats(min_value=1, max_value=359)),
            )
        case _:
            return CreateRectangle(corner=draw(points), width=draw(size), height=draw(size))


@st.composite
def sketches(draw: st.DrawFn) -> Document:
    bus = Bus(kernel=None)
    for command in draw(st.lists(geometry(), min_size=0, max_size=25)):
        bus.execute(command)
    if len(bus.document.entities) >= 2:  # annotations are never picked
        first, second = sorted(bus.document.entities)[:2]
        bus.execute(
            CreateDistanceDimension(
                a=Ref(entity=first, feature=_any_feature(bus.document, first)),
                b=Ref(entity=second, feature=_any_feature(bus.document, second)),
                orientation=DistanceOrientation.ALIGNED,
                offset=3.0,
            )
        )
    return bus.document


def _any_feature(document: Document, id: EntityId) -> Feature:
    return min(_features(document.entities[id]))


tolerances = st.one_of(st.sampled_from([0.0, 0.5, 2.0, 1e6]), st.floats(min_value=0, max_value=50))


@settings(max_examples=300, deadline=None)
@given(document=sketches(), clicks=st.lists(points, min_size=1, max_size=8), tolerance=tolerances)
def test_picking_matches_checking_every_entity(
    document: Document, clicks: list[Point2], tolerance: float
) -> None:
    queries = DocumentQueries(document, kernel=None)
    for click in clicks:
        assert queries.entity_at_point(click, tolerance) == entity_at_point(
            document, click, tolerance
        )
        assert queries.nearest_feature(click, tolerance) == nearest_feature(
            document, click, tolerance
        )
        assert queries.reference_at_point(click, tolerance) == reference_at_point(
            document, click, tolerance
        )


@settings(max_examples=300, deadline=None)
@given(
    document=sketches(), corners=st.lists(points, min_size=2, max_size=2), crossing=st.booleans()
)
def test_box_selection_matches_checking_every_entity(
    document: Document, corners: list[Point2], crossing: bool
) -> None:
    (a, b) = corners
    box = BoundingBox(
        x_min=min(a.x, b.x), y_min=min(a.y, b.y), x_max=max(a.x, b.x), y_max=max(a.y, b.y)
    )
    queries = DocumentQueries(document, kernel=None)
    assert queries.entities_in_box(box, crossing=crossing) == entities_in_box(
        document, box, crossing
    )


# --- The grid itself ------------------------------------------------------------------------


def document(*entities: object) -> Document:
    return Document(
        entities=MappingProxyType({EntityId(f"e{i + 1}"): e for i, e in enumerate(entities)}),
        next_id=len(entities) + 1,
    )


def test_an_empty_document_has_nothing_near_anything() -> None:
    grid = spatial.Grid(Document.empty())
    assert grid.near(0.0, 0.0, 1e9) == []
    assert grid.overlapping((-1.0, -1.0, 1.0, 1.0)) == []


def test_geometry_all_in_one_place_still_answers() -> None:
    at = Point2(x=3.0, y=4.0)
    grid = spatial.Grid(
        document(Circle(center=at, radius=1e-9), Rectangle(corner=at, width=1e-9, height=1e-9))
    )
    assert grid.near(3.0, 4.0, 0.0) == ["e1", "e2"]
    assert grid.near(10.0, 10.0, 1.0) == []


def test_a_huge_shape_is_found_from_anywhere_it_covers() -> None:
    small = [Circle(center=Point2(x=10.0 * i, y=0.0), radius=1.0) for i in range(100)]
    grid = spatial.Grid(
        document(*small, Rectangle(corner=Point2(x=-5000, y=-5000), width=10000, height=10000))
    )
    assert grid.near(4999.0, 4999.0, 0.0) == ["e101"]
    assert "e101" in grid.near(0.0, 0.0, 0.0)


def test_an_enormous_or_unusable_reach_is_answered_without_raising() -> None:
    grid = spatial.Grid(document(Circle(center=Point2(x=0.0, y=0.0), radius=1.0)))
    assert grid.near(0.0, 0.0, 1e300) == ["e1"]
    assert grid.near(0.0, 0.0, math.inf) == ["e1"]
    assert grid.near(math.nan, 0.0, 1.0) == []


def test_each_document_gets_its_own_grid_and_keeps_it() -> None:
    first = document(Circle(center=Point2(x=0.0, y=0.0), radius=1.0))
    second = document(Circle(center=Point2(x=50.0, y=0.0), radius=1.0))
    assert spatial.grid(first) is spatial.grid(first)
    assert spatial.grid(second).near(50.0, 0.0, 0.0) == ["e1"]
    assert spatial.grid(first).near(50.0, 0.0, 0.0) == []


@pytest.mark.parametrize("reach", [0.0, 1e-12, 0.5])
def test_a_point_exactly_at_the_reach_is_found(reach: float) -> None:
    # The query box is grown by a hair, so rounding at the very edge never loses a candidate.
    grid = spatial.Grid(document(Point(position=Point2(x=0.1 + 0.2, y=0.0))))
    assert grid.near(0.3 - reach, 0.0, reach) == ["e1"]
