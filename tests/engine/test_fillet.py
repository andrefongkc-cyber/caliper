"""FilletCorner: rounding the corner where two lines meet."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateCircle,
    CreateLine,
    FilletCorner,
    Rejected,
)
from caliper.contracts.document import Arc, EntityId, Feature, Line, Point2, Ref
from caliper.contracts.errors import ErrorCode
from caliper.engine.commands.bus import Bus

E1, E2, E3 = EntityId("e1"), EntityId("e2"), EntityId("e3")


def P(x: float, y: float) -> Point2:  # noqa: N802
    return Point2(x=x, y=y)


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def rejection(result: CommandResult) -> list[tuple[ErrorCode, str | None]]:
    assert isinstance(result, Rejected), result
    return [(e.code, e.field) for e in result.errors]


ALONG_X = Point2(x=100.0, y=0.0)
UP_Y = Point2(x=0.0, y=80.0)
ORIGIN = Point2(x=0.0, y=0.0)


def corner_bus(away_a: Point2 = ALONG_X, away_b: Point2 = UP_Y, corner: Point2 = ORIGIN) -> Bus:
    """Two lines meeting at `corner`: e1 arrives there, e2 leaves it."""
    bus = Bus()
    applied(bus.execute(CreateLine(start=away_a, end=corner)))
    applied(bus.execute(CreateLine(start=corner, end=away_b)))
    return bus


def line(bus: Bus, id: EntityId) -> Line:
    entity = bus.document.entities[id]
    assert isinstance(entity, Line), entity
    return entity


def arc(bus: Bus, id: EntityId = E3) -> Arc:
    entity = bus.document.entities[id]
    assert isinstance(entity, Arc), entity
    return entity


def at(bus: Bus, id: EntityId, feature: Feature) -> Point2:
    point = bus.queries.feature_point(Ref(entity=id, feature=feature))
    assert isinstance(point, Point2), point
    return point


# --- A rounded right angle --------------------------------------------------------------


def test_a_right_angle_is_rounded_exactly() -> None:
    bus = corner_bus()
    result = applied(bus.execute(FilletCorner(a=E1, b=E2, radius=10.0)))

    assert line(bus, E1) == Line(start=P(100.0, 0.0), end=P(10.0, 0.0))
    assert line(bus, E2) == Line(start=P(0.0, 10.0), end=P(0.0, 80.0))
    assert arc(bus) == Arc(center=P(10.0, 10.0), radius=10.0, start_angle=180.0, sweep_angle=90.0)
    assert result.created_ids == (E3,)
    assert result.label == "Fillet Corner"
    assert result.command == FilletCorner(a=E1, b=E2, radius=10.0, id=E3)
    assert bus.document.next_id == 4


def test_the_arc_joins_the_two_trimmed_ends() -> None:
    bus = corner_bus()
    applied(bus.execute(FilletCorner(a=E1, b=E2, radius=10.0)))
    ends = {at(bus, E3, Feature.START), at(bus, E3, Feature.END)}
    assert ends == {line(bus, E1).end, line(bus, E2).start}
    middle = at(bus, E3, Feature.MID)
    assert (middle.x, middle.y) == pytest.approx((2.9289321881345254, 2.9289321881345254))


@pytest.mark.parametrize(
    ("away_a", "away_b", "start_angle", "sweep"),
    [
        (P(100.0, 0.0), P(0.0, 80.0), 180.0, 90.0),
        (P(0.0, 80.0), P(100.0, 0.0), 180.0, 90.0),
        (P(-100.0, 0.0), P(0.0, -80.0), 0.0, 90.0),
        (P(100.0, 0.0), P(-100.0, 100.0), 225.0, 45.0),
        (P(100.0, 0.0), P(100.0, 100.0), 135.0, 135.0),
    ],
    ids=[
        "right angle",
        "right angle, lines the other way round",
        "right angle, into the third quadrant",
        "wide corner, 45° arc",
        "sharp corner, 135° arc",
    ],
)
def test_the_arc_sweeps_counter_clockwise_the_short_way(
    away_a: Point2, away_b: Point2, start_angle: float, sweep: float
) -> None:
    bus = corner_bus(away_a, away_b)
    applied(bus.execute(FilletCorner(a=E1, b=E2, radius=10.0)))
    rounded = arc(bus)
    assert rounded.start_angle == pytest.approx(start_angle)
    assert rounded.sweep_angle == pytest.approx(sweep)
    assert 0.0 < rounded.sweep_angle < 180.0
    # Sweeping counter-clockwise from the start angle arrives at the other tangent point.
    end = at(bus, E3, Feature.END)
    expected = math.radians(rounded.start_angle + rounded.sweep_angle)
    assert (end.x, end.y) == pytest.approx(
        (
            rounded.center.x + rounded.radius * math.cos(expected),
            rounded.center.y + rounded.radius * math.sin(expected),
        )
    )


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (
            CreateLine(start=P(100.0, 0.0), end=P(0.0, 0.0)),
            CreateLine(start=P(0.0, 0.0), end=P(0.0, 80.0)),
        ),
        (
            CreateLine(start=P(0.0, 0.0), end=P(100.0, 0.0)),
            CreateLine(start=P(0.0, 0.0), end=P(0.0, 80.0)),
        ),
        (
            CreateLine(start=P(0.0, 0.0), end=P(100.0, 0.0)),
            CreateLine(start=P(0.0, 80.0), end=P(0.0, 0.0)),
        ),
        (
            CreateLine(start=P(100.0, 0.0), end=P(0.0, 0.0)),
            CreateLine(start=P(0.0, 80.0), end=P(0.0, 0.0)),
        ),
    ],
    ids=["end-start", "start-start", "start-end", "end-end"],
)
def test_either_end_of_either_line_can_be_the_corner(a: Command, b: Command) -> None:
    bus = Bus()
    applied(bus.execute(a))
    applied(bus.execute(b))
    applied(bus.execute(FilletCorner(a=E1, b=E2, radius=10.0)))
    # Whichever end met at the corner moves to the tangent point; the far ends stay put.
    assert {line(bus, E1).start, line(bus, E1).end} == {P(100.0, 0.0), P(10.0, 0.0)}
    assert {line(bus, E2).start, line(bus, E2).end} == {P(0.0, 80.0), P(0.0, 10.0)}
    assert arc(bus) == Arc(center=P(10.0, 10.0), radius=10.0, start_angle=180.0, sweep_angle=90.0)


def test_undo_restores_the_sharp_corner_in_one_step() -> None:
    bus = corner_bus()
    before = bus.document
    applied(bus.execute(FilletCorner(a=E1, b=E2, radius=10.0)))
    after = bus.document
    assert bus.undo_label == "Fillet Corner"
    bus.undo()
    assert bus.document == before
    bus.redo()
    assert bus.document == after


def test_replaying_the_resolved_command_reproduces_the_document() -> None:
    bus = corner_bus()
    resolved = applied(bus.execute(FilletCorner(a=E1, b=E2, radius=10.0))).command
    replayed = corner_bus()
    applied(replayed.execute(resolved))
    assert replayed.document == bus.document


# --- Errors -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "errors"),
    [
        (FilletCorner(a=E1, b=E2, radius=0.0), [(ErrorCode.VALUE_NOT_POSITIVE, "radius")]),
        (FilletCorner(a=E1, b=E2, radius=-5.0), [(ErrorCode.VALUE_NOT_POSITIVE, "radius")]),
        (FilletCorner(a=E1, b=E2, radius=math.nan), [(ErrorCode.VALUE_NOT_FINITE, "radius")]),
        (FilletCorner(a=E1, b=E2, radius="10"), [(ErrorCode.VALUE_WRONG_TYPE, "radius")]),  # type: ignore[arg-type]
        (FilletCorner(a=E1, b=EntityId("e9"), radius=10.0), [(ErrorCode.ENTITY_NOT_FOUND, "b")]),
        (FilletCorner(a=EntityId("Nope"), b=E2, radius=10.0), [(ErrorCode.ID_INVALID, "a")]),
        (FilletCorner(a=E1, b=E1, radius=10.0), [(ErrorCode.REFERENCE_DEGENERATE, "b")]),
    ],
    ids=[
        "zero radius",
        "negative radius",
        "nan radius",
        "text radius",
        "missing line",
        "malformed id",
        "same line twice",
    ],
)
def test_invalid_fillets_are_rejected(
    command: FilletCorner, errors: list[tuple[ErrorCode, str]]
) -> None:
    bus = corner_bus()
    before = bus.document
    assert rejection(bus.execute(command)) == errors
    assert bus.document == before


def test_a_fillet_needs_two_lines() -> None:
    bus = corner_bus()
    applied(bus.execute(CreateCircle(center=P(0.0, 0.0), radius=5.0)))
    assert rejection(bus.execute(FilletCorner(a=E1, b=E3, radius=10.0))) == [
        (ErrorCode.ENTITY_WRONG_KIND, "b")
    ]


@pytest.mark.parametrize(
    ("away_a", "away_b", "corner_b"),
    [
        (P(100.0, 0.0), P(50.0, 80.0), P(50.0, 50.0)),
        (P(100.0, 0.0), P(-100.0, 0.0), P(0.0, 0.0)),
        (P(100.0, 0.0), P(200.0, 0.0), P(100.0, 0.0)),
    ],
    ids=["lines never meet", "in line, opposite ways", "in line, end to end"],
)
def test_lines_without_a_corner_are_rejected(
    away_a: Point2, away_b: Point2, corner_b: Point2
) -> None:
    bus = Bus()
    applied(bus.execute(CreateLine(start=away_a, end=P(0.0, 0.0))))
    applied(bus.execute(CreateLine(start=corner_b, end=away_b)))
    assert rejection(bus.execute(FilletCorner(a=E1, b=E2, radius=10.0))) == [
        (ErrorCode.GEOMETRY_DEGENERATE, "b")
    ]


@pytest.mark.parametrize(
    ("radius", "rejected"),
    [(79.9, False), (80.0, True), (100.0, True)],
    ids=["just fits", "exactly the line's length", "far too big"],
)
def test_a_radius_that_would_trim_past_an_end_is_rejected(radius: float, rejected: bool) -> None:
    """The 80 mm line sets the limit: a 90° corner needs `radius` mm of each line, and a
    fillet that would eat a whole line is rejected too."""
    bus = corner_bus()
    result = bus.execute(FilletCorner(a=E1, b=E2, radius=radius))
    if not rejected:
        assert isinstance(result, Applied)
        return
    assert rejection(result) == [(ErrorCode.VALUE_OUT_OF_RANGE, "radius")]
    assert isinstance(result, Rejected)
    assert "the radius must stay under 80.0" in result.errors[0].message


# --- Any corner -------------------------------------------------------------------------


@given(
    corner=st.tuples(
        st.floats(min_value=-1e3, max_value=1e3), st.floats(min_value=-1e3, max_value=1e3)
    ),
    heading=st.floats(min_value=0.0, max_value=360.0),
    angle=st.floats(min_value=5.0, max_value=175.0),
    length_a=st.floats(min_value=1.0, max_value=1e3),
    length_b=st.floats(min_value=1.0, max_value=1e3),
    fraction=st.floats(min_value=0.01, max_value=0.99),
)
def test_any_corner_is_rounded_tangent_to_both_lines(
    corner: tuple[float, float],
    heading: float,
    angle: float,
    length_a: float,
    length_b: float,
    fraction: float,
) -> None:
    """The arc touches both lines where they now end, and bridges the corner."""
    c = P(*corner)
    a_dir = math.radians(heading)
    b_dir = math.radians(heading + angle)
    away_a = P(c.x + length_a * math.cos(a_dir), c.y + length_a * math.sin(a_dir))
    away_b = P(c.x + length_b * math.cos(b_dir), c.y + length_b * math.sin(b_dir))
    # The largest radius that fits, computed the textbook way, then stay under it.
    largest = min(length_a, length_b) * math.tan(math.radians(angle) / 2)
    radius = largest * fraction

    bus = Bus()
    applied(bus.execute(CreateLine(start=away_a, end=c)))
    applied(bus.execute(CreateLine(start=c, end=away_b)))
    applied(bus.execute(FilletCorner(a=E1, b=E2, radius=radius)))

    rounded = arc(bus)
    scale = max(1.0, abs(c.x), abs(c.y), length_a, length_b)
    tol = 1e-9 * scale
    assert rounded.radius == radius
    assert rounded.sweep_angle == pytest.approx(180.0 - angle, abs=1e-9 * 180.0)
    # Both lines keep their far end and now stop on the arc.
    assert line(bus, E1).start == away_a
    assert line(bus, E2).end == away_b
    touch_a, touch_b = line(bus, E1).end, line(bus, E2).start
    for touch in (touch_a, touch_b):
        assert math.dist((touch.x, touch.y), (rounded.center.x, rounded.center.y)) == pytest.approx(
            radius, abs=tol
        )
    # Tangency: the centre sits exactly `radius` from each line, measured perpendicular.
    for away, touch in ((away_a, touch_a), (away_b, touch_b)):
        along = (away.x - c.x, away.y - c.y)
        length = math.hypot(*along)
        unit = (along[0] / length, along[1] / length)
        to_center = (rounded.center.x - c.x, rounded.center.y - c.y)
        perpendicular = abs(unit[0] * to_center[1] - unit[1] * to_center[0])
        assert perpendicular == pytest.approx(radius, abs=tol)
        assert math.dist((touch.x, touch.y), (c.x, c.y)) <= length + tol
    ends = sorted((p.x, p.y) for p in (at(bus, E3, Feature.START), at(bus, E3, Feature.END)))
    expected = sorted((p.x, p.y) for p in (touch_a, touch_b))
    assert [v for point in ends for v in point] == pytest.approx(
        [v for point in expected for v in point], abs=tol
    )
