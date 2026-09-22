"""Drawing what a V1.5 document can hold: points, construction geometry, dimensions to
lines, angle dimensions, and driven versus driving values.

Placement math is checked against the real engine, not against a copy of its formulas.
"""

import math

import pytest

from caliper.app import theme
from caliper.app.dimension_layout import (
    Segment,
    anchors,
    angle_layout,
    engine_placement,
    layout,
    offset_for,
)
from caliper.app.viewport.annotations import label_anchors
from caliper.contracts.commands import (
    Applied,
    CreateCircle,
    CreateDimension,
    CreateLine,
    CreatePoint,
    ModifyEntity,
)
from caliper.contracts.document import (
    AngleDimension,
    DistanceDimension,
    Feature,
    Point2,
    Ref,
)
from caliper.contracts.document import DistanceOrientation as O
from caliper.contracts.queries import DimensionType
from caliper.engine.commands.bus import Bus
from tests.app.test_canvas import pixel

P = Point2
TYPE_FOR = {
    O.HORIZONTAL: DimensionType.HORIZONTAL_DISTANCE,
    O.VERTICAL: DimensionType.VERTICAL_DISTANCE,
    O.ALIGNED: DimensionType.DISTANCE,
}


def _points(bus: Bus, *points: Point2) -> list[Ref]:
    refs = []
    for p in points:
        (id,) = bus.execute(CreatePoint(position=p)).created_ids
        refs.append(Ref(entity=id, feature=Feature.POINT))
    return refs


def _line(bus: Bus, a: Point2, b: Point2) -> Ref:
    (id,) = bus.execute(CreateLine(start=a, end=b)).created_ids
    return Ref(entity=id, feature=Feature.CURVE)


def _created(bus: Bus, command: CreateDimension) -> object:
    result = bus.execute(command)
    assert isinstance(result, Applied), result
    (id,) = result.created_ids
    return bus.document.entities[id]


# --- Distance placement against the engine -------------------------------------------------

CASES = [
    (P(x=10, y=5), P(x=70, y=45), P(x=33, y=90)),  # slanted pair, label above
    (P(x=70, y=45), P(x=10, y=5), P(x=33, y=90)),  # the same pair, picked the other way
    (P(x=0, y=0), P(x=100, y=30), P(x=-40, y=12)),  # label beside
    (P(x=0, y=0), P(x=100, y=0), P(x=50, y=-20)),  # level pair, label below
]


@pytest.mark.parametrize("orientation", list(O))
@pytest.mark.parametrize(("a", "b", "placement"), CASES)
def test_a_dimension_the_engine_creates_is_drawn_through_its_placement(
    orientation: O, a: Point2, b: Point2, placement: Point2
) -> None:
    bus = Bus()
    ref_a, ref_b = _points(bus, a, b)
    dim = _created(
        bus,
        CreateDimension(
            refs=(ref_a, ref_b),
            placement=engine_placement(orientation, a, b, placement),
            type=TYPE_FOR[orientation],
        ),
    )
    assert isinstance(dim, DistanceDimension)
    assert dim.offset == pytest.approx(offset_for(orientation, a, b, placement), abs=1e-9)
    geo = layout(orientation, a, b, dim.offset)
    ux, uy = geo.along
    cross = (placement.x - geo.start.x) * uy - (placement.y - geo.start.y) * ux
    assert cross == pytest.approx(0, abs=1e-9)


def test_the_engine_still_measures_horizontal_offsets_across_a_to_b() -> None:
    """The contract gap `engine_placement` works around. When this fails, the engine has
    adopted the shell's rule: delete `engine_placement` and pass the real placement."""
    bus = Bus()
    a, b, placement = P(x=10, y=5), P(x=70, y=45), P(x=33, y=90)
    dim = _created(
        bus,
        CreateDimension(
            refs=tuple(_points(bus, a, b)),
            placement=placement,
            type=DimensionType.HORIZONTAL_DISTANCE,
        ),
    )
    assert isinstance(dim, DistanceDimension)
    assert dim.offset != pytest.approx(offset_for(O.HORIZONTAL, a, b, placement), abs=1e-6)


def test_a_distance_to_a_line_attaches_at_the_perpendicular_foot() -> None:
    bus = Bus()
    (point,) = _points(bus, P(x=30, y=40))
    line = _line(bus, P(x=0, y=0), P(x=100, y=10))
    dim = _created(bus, CreateDimension(refs=(point, line), placement=P(x=60, y=40)))
    assert isinstance(dim, DistanceDimension)
    a, b = anchors(P(x=30, y=40), Segment(P(x=0, y=0), P(x=100, y=10)))
    (id,) = [k for k, v in bus.document.entities.items() if v == dim]
    measured = bus.queries.dimension_value(id)
    assert math.hypot(b.x - a.x, b.y - a.y) == pytest.approx(measured)


# --- Angle placement against the engine ---------------------------------------------------


@pytest.mark.parametrize(
    "placement",
    [P(x=30, y=10), P(x=-30, y=-10), P(x=-10, y=30), P(x=10, y=-30), P(x=20, y=60)],
)
def test_the_angle_arc_is_in_the_sector_the_label_was_placed_in(placement: Point2) -> None:
    bus = Bus()
    first, second = P(x=-50, y=-10), P(x=50, y=10)
    a = _line(bus, first, second)
    b = _line(bus, P(x=10, y=-50), P(x=-10, y=50))
    dim = _created(bus, CreateDimension(refs=(a, b), placement=placement))
    assert isinstance(dim, AngleDimension)
    arc = angle_layout(
        Segment(first, second),
        Segment(P(x=10, y=-50), P(x=-10, y=50)),
        dim.offset,
        dim.supplementary,
    )
    assert arc is not None
    distance = math.hypot(placement.x - arc.center.x, placement.y - arc.center.y)
    assert arc.radius == pytest.approx(distance)
    direction = math.degrees(math.atan2(placement.y - arc.center.y, placement.x - arc.center.x))
    assert 0 < (direction - arc.start_angle) % 360 < arc.sweep


def test_parallel_lines_have_no_arc() -> None:
    a = Segment(P(x=0, y=0), P(x=10, y=0))
    b = Segment(P(x=0, y=5), P(x=10, y=5))
    assert angle_layout(a, b, 10, supplementary=False) is None


# --- On the canvas ------------------------------------------------------------------------


@pytest.fixture
def v15_sketch(window) -> dict[str, str]:
    s = window.session
    ids: dict[str, str] = {}
    (ids["point"],) = s.execute(CreatePoint(position=P(x=-20, y=60))).created_ids
    (ids["construction"],) = s.execute(
        CreateLine(start=P(x=0, y=80), end=P(x=100, y=80), construction=True)
    ).created_ids
    (ids["a"],) = s.execute(CreateLine(start=P(x=0, y=0), end=P(x=100, y=0))).created_ids
    (ids["b"],) = s.execute(CreateLine(start=P(x=0, y=0), end=P(x=0, y=50))).created_ids
    a = Ref(entity=ids["a"], feature=Feature.CURVE)
    b = Ref(entity=ids["b"], feature=Feature.CURVE)
    point = Ref(entity=ids["point"], feature=Feature.POINT)
    (ids["angle"],) = s.execute(CreateDimension(refs=(a, b), placement=P(x=15, y=15))).created_ids
    (ids["length"],) = s.execute(
        CreateDimension(refs=(a,), placement=P(x=50, y=-15), value=100.0)
    ).created_ids
    (ids["to_line"],) = s.execute(
        CreateDimension(refs=(point, a), placement=P(x=-35, y=30))
    ).created_ids
    return ids


def test_every_v15_dimension_is_drawn(window, v15_sketch) -> None:
    window.canvas.grab()
    assert window.canvas.hidden_dimensions == 0


def test_driven_values_are_in_parentheses_and_driving_ones_are_not(window, v15_sketch) -> None:
    texts = {text for _, text in label_anchors(window.session, window.canvas.view)}
    assert "100.00" in texts  # driving length
    assert "(90.00°)" in texts  # driven angle
    assert "(60.00)" in texts  # driven distance from the point to the line


def test_making_a_dimension_driving_drops_the_parentheses(window, v15_sketch) -> None:
    window.session.execute(ModifyEntity(id=v15_sketch["to_line"], changes={"value": 60.0}))
    texts = {text for _, text in label_anchors(window.session, window.canvas.view)}
    assert "60.00" in texts


def test_a_point_is_drawn_as_a_dot(window) -> None:
    window.session.execute(CreatePoint(position=P(x=20, y=20)))
    assert pixel(window, 20, 20) == theme.GEOMETRY
    assert pixel(window, 22, 20) != theme.GEOMETRY  # 2 mm = 10 px away: the dot is small


def _brightest_across(window, x: float, y: float) -> int:
    """The lightest pixel within 2 px across a horizontal line at model (x, y)."""
    image = window.canvas.grab().toImage()
    ratio = window.canvas.devicePixelRatioF()
    wx, wy = window.canvas.view.to_widget(P(x=x, y=y))
    cx, cy = round(wx * ratio), round(wy * ratio)
    return max(image.pixelColor(cx, cy + d).lightness() for d in range(-2, 3))


def test_construction_geometry_is_dashed_and_fainter(window) -> None:
    s = window.session
    s.execute(CreateLine(start=P(x=0, y=52), end=P(x=100, y=52), construction=True))
    s.execute(CreateLine(start=P(x=0, y=32), end=P(x=100, y=32)))
    along = [x + 0.5 for x in range(5, 60)]  # the canvas is about 490 px (98 mm) wide
    construction = [_brightest_across(window, x, 52) for x in along]
    real = [_brightest_across(window, x, 32) for x in along]
    background = _brightest_across(window, 30.5, 42)
    assert max(construction) < min(real)  # fainter everywhere
    assert min(construction) <= background + 5  # and broken by gaps
    assert min(real) > background + 40  # the real line is solid


def test_a_circle_curve_is_not_mistaken_for_a_straight_one(window) -> None:
    from caliper.app import references

    (id,) = window.session.execute(CreateCircle(center=P(x=0, y=0), radius=5)).created_ids
    ref = Ref(entity=id, feature=Feature.CURVE)
    assert references.straight(window.session.queries, ref) is None
