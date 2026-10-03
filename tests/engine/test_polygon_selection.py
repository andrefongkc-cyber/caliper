"""A box selection seen at an angle (ADR 0016, `Queries.entities_in_polygon`): a convex
polygon on the sketch's plane, window or crossing. A box's corners give exactly
`entities_in_box`'s answer; any polygon gives the same decisions as a box does after the whole
sketch is turned with it."""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateArc,
    CreateCircle,
    CreateLine,
    CreatePoint,
    CreateRectangle,
)
from caliper.contracts.document import EntityId, Point2
from caliper.contracts.queries import BoundingBox
from caliper.engine.commands.bus import Bus


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def created(bus: Bus, command: Command) -> EntityId:
    return applied(bus.execute(command)).created_ids[0]


def p(x: float, y: float) -> Point2:
    return Point2(x=x, y=y)


DIAMOND = [p(0, -10), p(10, 0), p(0, 10), p(-10, 0)]
"""A square turned 45°, centred on the origin, 10 to each corner."""


def test_a_polygon_that_isnt_one_convex_shape_matches_nothing() -> None:
    bus = Bus()
    created(bus, CreatePoint(position=p(0, 0)))
    for corners in (
        [],
        [p(0, 0), p(1, 0)],
        [p(0, 0), p(1, 0), p(2, 0)],  # no area
        [p(0, 0), p(4, 0), p(1, 1), p(0, 4)],  # an arrow: not convex
        [p(0, 0), p(math.inf, 0), p(0, 1)],
        [p(0, 0), (1, 0), p(0, 1)],
        "abc",
    ):
        for crossing in (False, True):
            assert bus.queries.entities_in_polygon(corners, crossing=crossing) == ()  # type: ignore[arg-type]


def test_inside_and_touching_a_turned_square() -> None:
    bus = Bus()
    inside_line = created(bus, CreateLine(start=p(-2, 0), end=p(2, 1)))
    crossing_line = created(bus, CreateLine(start=p(0, 0), end=p(20, 0)))
    outside_line = created(bus, CreateLine(start=p(8, 8), end=p(20, 8)))  # past the edge
    small = created(bus, CreateCircle(center=p(0, 0), radius=3.0))
    big = created(bus, CreateCircle(center=p(0, 0), radius=8.0))  # its sides reach out
    near = created(bus, CreateCircle(center=p(10, 10), radius=8.0))  # reaches the edge
    far = created(bus, CreateCircle(center=p(20, 20), radius=3.0))
    box = created(bus, CreateRectangle(corner=p(4, 4), width=10.0, height=10.0))  # a corner in
    held_box = created(bus, CreateRectangle(corner=p(-2, -2), width=4.0, height=4.0))
    arc_in = created(bus, CreateArc(center=p(0, 0), radius=4.0, start_angle=0.0, sweep_angle=180.0))
    arc_bulge = created(  # its ends are inside, its middle crosses the upper right edge
        bus, CreateArc(center=p(0, 0), radius=7.5, start_angle=20.0, sweep_angle=50.0)
    )
    arc_across = created(  # its ends are outside, it passes through
        bus, CreateArc(center=p(0, -30), radius=30.0, start_angle=60.0, sweep_angle=60.0)
    )
    window = set(bus.queries.entities_in_polygon(DIAMOND, crossing=False))
    crossing = set(bus.queries.entities_in_polygon(DIAMOND, crossing=True))
    assert window == {inside_line, small, held_box, arc_in}
    assert crossing == window | {crossing_line, big, near, box, arc_bulge, arc_across}
    assert outside_line not in crossing
    assert far not in crossing


def test_a_polygon_inside_a_circle_or_rectangle_touches_it() -> None:
    bus = Bus()
    circle = created(bus, CreateCircle(center=p(0, 0), radius=50.0))
    rectangle = created(bus, CreateRectangle(corner=p(-40, -40), width=80.0, height=80.0))
    assert set(bus.queries.entities_in_polygon(DIAMOND, crossing=True)) == {circle, rectangle}
    assert bus.queries.entities_in_polygon(DIAMOND, crossing=False) == ()


def test_corners_in_any_order_or_direction_and_closed_or_not_are_one_polygon() -> None:
    bus = Bus()
    created(bus, CreateLine(start=p(-2, 0), end=p(2, 1)))
    created(bus, CreateCircle(center=p(0, 0), radius=8.0))
    expected = bus.queries.entities_in_polygon(DIAMOND, crossing=True)
    for corners in (DIAMOND[::-1], DIAMOND[2:] + DIAMOND[:2], [*DIAMOND, DIAMOND[0]]):
        assert bus.queries.entities_in_polygon(corners, crossing=True) == expected


# --- Against the box ----------------------------------------------------------------------

eighths = st.integers(-800, 800).map(lambda n: n / 8)
radii = st.integers(1, 200).map(lambda n: n / 8)
angles = st.integers(0, 359).map(float)
sweeps = st.integers(1, 359).map(float)
shapes = st.one_of(
    st.builds(lambda x, y: CreatePoint(position=p(x, y)), eighths, eighths),
    st.builds(
        lambda a, b, c, d: CreateLine(start=p(a, b), end=p(c, d)),
        eighths,
        eighths,
        eighths,
        eighths,
    ).filter(lambda c: c.start != c.end),
    st.builds(lambda x, y, r: CreateCircle(center=p(x, y), radius=r), eighths, eighths, radii),
    st.builds(
        lambda x, y, r, s, w: CreateArc(center=p(x, y), radius=r, start_angle=s, sweep_angle=w),
        eighths,
        eighths,
        radii,
        angles,
        sweeps,
    ),
    st.builds(
        lambda x, y, w, h: CreateRectangle(corner=p(x, y), width=w, height=h),
        eighths,
        eighths,
        radii,
        radii,
    ),
)


@st.composite
def boxes(draw: st.DrawFn) -> BoundingBox:
    x0, x1 = sorted(draw(st.lists(eighths, min_size=2, max_size=2, unique=True)))
    y0, y1 = sorted(draw(st.lists(eighths, min_size=2, max_size=2, unique=True)))
    return BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1)


def corners_of(box: BoundingBox) -> list[Point2]:
    return [
        p(box.x_min, box.y_min),
        p(box.x_max, box.y_min),
        p(box.x_max, box.y_max),
        p(box.x_min, box.y_max),
    ]


@settings(max_examples=60, deadline=None)
@given(
    made=st.lists(shapes, min_size=1, max_size=12),
    box=boxes(),
    turn=st.integers(0, 3),
    back=st.booleans(),
)
def test_a_boxs_corners_give_exactly_the_box_selection(
    made: list[Command], box: BoundingBox, turn: int, back: bool
) -> None:
    bus = Bus()
    for command in made:
        bus.execute(command)
    corners = corners_of(box)
    corners = corners[turn:] + corners[:turn]
    if back:
        corners.reverse()
    for crossing in (False, True):
        assert bus.queries.entities_in_polygon(corners, crossing=crossing) == (
            bus.queries.entities_in_box(box, crossing=crossing)
        )


EPSILON = 1e-6


def turned(command: Command, c: float, s: float, degrees: float) -> Command:
    """`command` with the sketch turned about the origin; rectangles aren't drawn here."""

    def rot(q: Point2) -> Point2:
        return p(c * q.x - s * q.y, s * q.x + c * q.y)

    match command:
        case CreatePoint(position=q):
            return CreatePoint(position=rot(q))
        case CreateLine(start=a, end=b):
            return CreateLine(start=rot(a), end=rot(b))
        case CreateCircle(center=q, radius=r):
            return CreateCircle(center=rot(q), radius=r)
        case CreateArc(center=q, radius=r, start_angle=a, sweep_angle=w):
            return CreateArc(
                center=rot(q), radius=r, start_angle=(a + degrees) % 360.0, sweep_angle=w
            )
    raise AssertionError(command)


def grown(box: BoundingBox, by: float) -> BoundingBox:
    return BoundingBox(
        x_min=box.x_min - by, y_min=box.y_min - by, x_max=box.x_max + by, y_max=box.y_max + by
    )


@settings(max_examples=80, deadline=None)
@given(
    made=st.lists(
        shapes.filter(lambda c: not isinstance(c, CreateRectangle)), min_size=1, max_size=10
    ),
    box=boxes(),
    degrees=st.floats(min_value=1.0, max_value=89.0),
)
def test_turning_a_sketch_and_its_box_together_changes_no_clear_decision(
    made: list[Command], box: BoundingBox, degrees: float
) -> None:
    """Every entity the box clearly takes, or clearly leaves (the same when the box is a
    millionth of a mm larger or smaller), the turned box takes or leaves in the turned
    sketch."""
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    flat, turned_bus = Bus(), Bus()
    for command in made:
        if isinstance(flat.execute(command), Applied):
            applied(turned_bus.execute(turned(command, c, s, degrees)))
    corners = [p(c * q.x - s * q.y, s * q.x + c * q.y) for q in corners_of(box)]
    for crossing in (False, True):
        small = set(flat.queries.entities_in_box(grown(box, -EPSILON), crossing=crossing))
        large = set(flat.queries.entities_in_box(grown(box, EPSILON), crossing=crossing))
        got = set(turned_bus.queries.entities_in_polygon(corners, crossing=crossing))
        for id in flat.document.entities:
            if (id in small) == (id in large):
                assert (id in got) == (id in small), (id, crossing)
