"""Closed profiles from lines and arcs (N4): what the area query and an area check accept,
what they refuse and why, and the stress plate's outline and holes on both kernels."""

import math
import random
import time
from types import MappingProxyType

import pytest

from caliper.contracts.commands import CreateCheck, CreateLine
from caliper.contracts.document import (
    Arc,
    Circle,
    DistanceDimension,
    DistanceOrientation,
    Document,
    Entity,
    EntityId,
    Feature,
    Line,
    Metric,
    Point,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.kernel import Kernel
from caliper.contracts.queries import AreaProperties
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry.fake_kernel import FakeKernel
from caliper.engine.queries import DocumentQueries
from tests.engine.constraints.test_numerics import replay

P = Point2


@pytest.fixture(scope="module", params=["fake", "occt"])
def kernel(request: pytest.FixtureRequest) -> Kernel:
    if request.param == "fake":
        return FakeKernel()
    occt = pytest.importorskip(
        "caliper.engine.geometry.occt_kernel", reason="the occt extra isn't installed"
    )
    made: Kernel = occt.OCCTKernel()
    return made


def document(*entities: Entity) -> Document:
    """The entities as e1, e2, ... in the order given."""
    return Document(
        entities=MappingProxyType({EntityId(f"e{n}"): e for n, e in enumerate(entities, 1)}),
        next_id=len(entities) + 1,
    )


def ids(*numbers: int) -> list[EntityId]:
    return [EntityId(f"e{n}") for n in numbers]


def area(kernel: Kernel, doc: Document, selected: list[EntityId]) -> AreaProperties:
    found = DocumentQueries(doc, kernel=kernel).area_properties(selected)
    assert isinstance(found, AreaProperties), found
    return found


def refused(doc: Document, selected: list[EntityId]) -> Error:
    found = DocumentQueries(doc, kernel=FakeKernel()).area_properties(selected)
    assert isinstance(found, Error), found
    return found


def lines(*corners: tuple[float, float]) -> list[Line]:
    points = [P(x=x, y=y) for x, y in corners]
    return [Line(start=a, end=b) for a, b in zip(points, [*points[1:], points[0]], strict=True)]


def plate_outline(w: float, h: float, r: float) -> list[Entity]:
    """A plate's outline as the stress plate draws it: four edges, four fillet arcs."""
    return [
        Line(start=P(x=r, y=0), end=P(x=w - r, y=0)),
        Arc(center=P(x=w - r, y=r), radius=r, start_angle=270, sweep_angle=90),
        Line(start=P(x=w, y=r), end=P(x=w, y=h - r)),
        Arc(center=P(x=w - r, y=h - r), radius=r, start_angle=0, sweep_angle=90),
        Line(start=P(x=w - r, y=h), end=P(x=r, y=h)),
        Arc(center=P(x=r, y=h - r), radius=r, start_angle=90, sweep_angle=90),
        Line(start=P(x=0, y=h - r), end=P(x=0, y=r)),
        Arc(center=P(x=r, y=r), radius=r, start_angle=180, sweep_angle=90),
    ]


# --- What makes a profile ----------------------------------------------------------------


def test_lines_join_in_any_order_and_either_direction(kernel: Kernel) -> None:
    pentagon = lines((0, 0), (8, 0), (10, 6), (4, 10), (-2, 6))
    flipped = [pentagon[0], Line(start=pentagon[1].end, end=pentagon[1].start), *pentagon[2:]]
    shuffled = [flipped[n] for n in (3, 0, 4, 2, 1)]
    props = area(kernel, document(*shuffled), ids(1, 2, 3, 4, 5))
    shoelace = sum((a.start.x * a.end.y - a.end.x * a.start.y) / 2 for a in pentagon)
    assert props.area == pytest.approx(shoelace, rel=1e-9)


def test_a_circle_from_two_arcs(kernel: Kernel) -> None:
    halves = [
        Arc(center=P(x=5, y=5), radius=4, start_angle=0, sweep_angle=180),
        Arc(center=P(x=5, y=5), radius=4, start_angle=180, sweep_angle=180),
    ]
    props = area(kernel, document(*halves), ids(1, 2))
    assert props.area == pytest.approx(math.pi * 16, rel=1e-9)
    assert (props.centroid.x, props.centroid.y) == (pytest.approx(5), pytest.approx(5))


def test_mixed_lines_and_arcs_with_holes_of_every_kind(kernel: Kernel) -> None:
    w, h, r = 120.0, 80.0, 10.0
    outline = plate_outline(w, h, r)
    d_arc = Arc(center=P(x=35, y=50), radius=10, start_angle=0, sweep_angle=180)
    d_chord = Line(start=P(x=25, y=50), end=P(x=45, y=50))
    hole = Circle(center=P(x=90, y=20), radius=4)
    window = Rectangle(corner=P(x=70, y=40), width=30, height=20)
    doc = document(*outline, d_arc, d_chord, hole, window)
    props = area(kernel, doc, ids(*range(1, 13)))
    expected = w * h - (4 - math.pi) * r * r - math.pi * 100 / 2 - math.pi * 16 - 600
    assert props.area == pytest.approx(expected, rel=1e-9)


def test_the_stress_plate_s_outline_and_holes(kernel: Kernel) -> None:
    """The recorded stress plate (bench/sessions), as solved: its outline, the 18 holes clear
    of the star, the D cutout, the star, and the tiny hole, against the formulas."""
    plate = replay("stress-plate-build").document
    entities = plate.entities

    def pick(test: object) -> list[EntityId]:
        return sorted(i for i, e in entities.items() if test(e))  # type: ignore[operator]

    outline = pick(
        lambda e: (
            (isinstance(e, Arc) and math.isclose(e.radius, 12))
            or (
                isinstance(e, Line)
                and not e.construction
                and (
                    ({e.start.x, e.end.x} <= {0.0, 240.0} and e.start.x == e.end.x)
                    or ({e.start.y, e.end.y} <= {0.0, 160.0} and e.start.y == e.end.y)
                )
            )
        )
    )
    assert len(outline) == 8
    holes = pick(
        lambda e: (
            isinstance(e, Circle)
            and not e.construction
            and math.isclose(e.radius, 3)
            and math.hypot(e.center.x - 120, e.center.y - 80) > 30
        )  # two overlap the star
    )
    assert len(holes) == 18
    tiny = pick(lambda e: isinstance(e, Circle) and math.isclose(e.radius, 0.1))
    d = pick(
        lambda e: (
            (isinstance(e, Arc) and math.isclose(e.radius, 16.25))
            or (isinstance(e, Line) and math.isclose(e.start.y, 140) and math.isclose(e.end.y, 140))
        )
    )
    star = pick(
        lambda e: (
            isinstance(e, Line)
            and not e.construction
            and all(math.hypot(p.x - 120, p.y - 80) < 25.001 for p in (e.start, e.end))
        )
    )
    assert (len(tiny), len(d), len(star)) == (1, 2, 24)
    props = area(kernel, plate, outline + holes + tiny + d + star)
    plate_area = 240 * 160 - (4 - math.pi) * 144
    theta = 2 * math.asin(15 / 16.25)  # the D's arc, through (20, 140), (35, 150), (50, 140)
    d_area = 16.25**2 / 2 * (theta - math.sin(theta))  # the segment above its 30 mm chord
    star_area = 12 * 25 * 12 * math.sin(math.radians(15))  # 24 triangles, tips R25, R12
    expected = plate_area - 18 * math.pi * 9 - math.pi * 0.01 - d_area - star_area
    assert props.area == pytest.approx(expected, rel=1e-9)


def test_the_stress_plate_area_agrees_across_kernels() -> None:
    occt = pytest.importorskip("caliper.engine.geometry.occt_kernel")
    plate = replay("stress-plate-build").document
    outline = sorted(
        i
        for i, e in plate.entities.items()
        if (isinstance(e, Arc) and math.isclose(e.radius, 12))
        or (
            isinstance(e, Line)
            and not e.construction
            and (
                ({e.start.x, e.end.x} <= {0.0, 240.0} and e.start.x == e.end.x)
                or ({e.start.y, e.end.y} <= {0.0, 160.0} and e.start.y == e.end.y)
            )
        )
    )
    holes = sorted(
        i
        for i, e in plate.entities.items()
        if isinstance(e, Circle)
        and not e.construction
        and math.hypot(e.center.x - 120, e.center.y - 80) > 30
    )
    fake = area(FakeKernel(), plate, outline + holes)
    real = area(occt.OCCTKernel(), plate, outline + holes)
    assert real.area == pytest.approx(fake.area, rel=1e-9)
    for got, want in (
        (real.centroid.x, fake.centroid.x),
        (real.centroid.y, fake.centroid.y),
        (real.ixx, fake.ixx),
        (real.iyy, fake.iyy),
        (real.ixy, fake.ixy),
    ):
        assert got == pytest.approx(want, rel=1e-7, abs=1e-6 * 240**3)


# --- What isn't one -------------------------------------------------------------------------


def square(x: float, y: float, size: float) -> list[Line]:
    return lines((x, y), (x + size, y), (x + size, y + size), (x, y + size))


@pytest.mark.parametrize(
    ("entities", "picked", "says"),
    [
        (square(0, 0, 10)[:3], [1, 2, 3], "joins nothing"),
        (
            [*square(0, 0, 10), Line(start=P(x=10, y=10), end=P(x=15, y=15))],
            [1, 2, 3, 4, 5],
            "can't branch",
        ),
        (
            [
                Line(start=P(x=0, y=0), end=P(x=10, y=0)),
                Line(start=P(x=10, y=0), end=P(x=5, y=8)),
                Line(start=P(x=5, y=8.0001), end=P(x=0, y=0)),
            ],
            [1, 2, 3],
            "joins nothing",
        ),
        (
            [*square(0, 0, 10), Line(start=P(x=5, y=0), end=P(x=5, y=10))],
            [1, 2, 3, 4, 5],
            "joins nothing",
        ),
        (
            [*square(0, 0, 10), Circle(center=P(x=10, y=5), radius=2)],
            [1, 2, 3, 4, 5],
            "cross or touch",
        ),
        (
            [*square(0, 0, 10), Circle(center=P(x=5, y=8), radius=2)],
            [1, 2, 3, 4, 5],
            "cross or touch",
        ),
        (lines((0, 0), (10, 10), (10, 0), (0, 10)), [1, 2, 3, 4], "cross or touch"),
        ([*square(0, 0, 10), *square(10, 2, 6)], list(range(1, 9)), "cross or touch"),
        (
            [
                Circle(center=P(x=0, y=0), radius=5),
                Arc(center=P(x=0, y=0), radius=5, start_angle=0, sweep_angle=180),
                Arc(center=P(x=0, y=0), radius=5, start_angle=180, sweep_angle=180),
            ],
            [1, 2, 3],
            "overlap",
        ),
        ([*square(0, 0, 10), *square(20, 0, 10)], list(range(1, 9)), "more than one profile"),
        (
            [*square(0, 0, 30), *square(5, 5, 20), Circle(center=P(x=15, y=15), radius=3)],
            list(range(1, 10)),
            "island",
        ),
        ([*square(0, 0, 10), Point(position=P(x=5, y=5))], [1, 2, 3, 4, 5], "is a point"),
        (
            # The stress plate's slot as the prompt asks for it: a rectangle with end arcs.
            # Not one loop: the arcs join the rectangle's corners, which aren't ends.
            [
                Rectangle(corner=P(x=40, y=115), width=40, height=10),
                Arc(center=P(x=40, y=120), radius=5, start_angle=90, sweep_angle=180),
                Arc(center=P(x=80, y=120), radius=5, start_angle=270, sweep_angle=180),
            ],
            [1, 2, 3],
            "joins nothing",
        ),
    ],
    ids=[
        "open",
        "a tail",
        "a gap of 1e-4",
        "a branch",
        "a hole crossing the outline",
        "a hole touching the outline",
        "a loop crossing itself",
        "a loop on another's side",
        "the same circle twice",
        "side by side",
        "an island in a hole",
        "a point",
        "a slot of a rectangle and arcs",
    ],
)
def test_what_isn_t_one_closed_profile_is_refused_with_the_reason(
    entities: list[Entity], picked: list[int], says: str
) -> None:
    error = refused(document(*entities), ids(*picked))
    assert error.code is ErrorCode.PROFILE_NOT_CLOSED
    assert says in error.message
    assert set(error.ids) <= set(ids(*picked))


def test_a_branch_names_where_and_what() -> None:
    doc = document(*square(0, 0, 10), Line(start=P(x=0, y=0), end=P(x=-5, y=-5)))
    error = refused(doc, ids(1, 2, 3, 4, 5))
    assert "3 edge ends meet at (0, 0)" in error.message
    assert set(error.ids) == {"e1", "e4", "e5"}


def test_other_refusals_keep_their_codes() -> None:
    doc = document(
        *square(0, 0, 10),
        Line(start=P(x=0, y=0), end=P(x=0, y=0)),
        DistanceDimension(
            a=Ref(entity=EntityId("e1"), feature=Feature.START),
            b=Ref(entity=EntityId("e1"), feature=Feature.END),
            orientation=DistanceOrientation.ALIGNED,
            offset=2,
        ),
        Circle(center=P(x=5, y=5), radius=1, construction=True),
    )
    assert refused(doc, []).code is ErrorCode.SELECTION_EMPTY
    assert refused(doc, ids(1, 2, 3, 4, 5)).code is ErrorCode.GEOMETRY_DEGENERATE
    assert refused(doc, ids(6)).code is ErrorCode.ENTITY_WRONG_KIND
    assert refused(doc, ids(1, 2, 3, 4, 7)).code is ErrorCode.PROFILE_CONSTRUCTION
    assert refused(doc, ids(9)).code is ErrorCode.ENTITY_NOT_FOUND


def test_ends_join_within_the_solver_s_margin_and_not_beyond(kernel: Kernel) -> None:
    near = square(0, 0, 10)
    near[2] = Line(start=P(x=10, y=10 + 1e-9), end=P(x=0, y=10))  # 1e-9 off: one point
    assert area(kernel, document(*near), ids(1, 2, 3, 4)).area == pytest.approx(100, rel=1e-9)
    far = square(0, 0, 10)
    far[2] = Line(start=P(x=10, y=10 + 1e-3), end=P(x=0, y=10))
    assert "joins nothing" in refused(document(*far), ids(1, 2, 3, 4)).message


def test_without_a_kernel_a_bad_profile_still_says_why() -> None:
    queries = DocumentQueries(document(*square(0, 0, 10)[:3]), kernel=None)
    found = queries.area_properties(ids(1, 2, 3))
    assert isinstance(found, Error)
    assert found.code is ErrorCode.PROFILE_NOT_CLOSED
    whole = DocumentQueries(document(*square(0, 0, 10)), kernel=None).area_properties(
        ids(1, 2, 3, 4)
    )
    assert isinstance(whole, Error)
    assert whole.code is ErrorCode.KERNEL_UNAVAILABLE


def test_an_area_check_of_a_traced_outline() -> None:
    # The gap N4 closes: an area check on lines and arcs was refused, whatever they made.
    bus = Bus(kernel=FakeKernel())
    for line in lines((0, 0), (30, 0), (30, 20), (0, 20)):
        bus.execute(CreateLine(start=line.start, end=line.end))
    check = CreateCheck(
        metric=Metric.AREA, expected=600.0, tolerance=1e-6, ids=tuple(ids(1, 2, 3, 4))
    )
    result = bus.execute(check)
    assert not hasattr(result, "errors"), result
    (stored,) = [e for e in bus.document.entities.values() if e.kind == "check"]
    assert bus.queries.check(stored).passed  # type: ignore[arg-type]


def test_a_large_profile_is_found_quickly() -> None:
    # 2,000 edges in random order: joining is by a grid, crossing by a sweep, not all pairs.
    count = 2000
    corners = [
        (100 * math.cos(2 * math.pi * k / count), 100 * math.sin(2 * math.pi * k / count))
        for k in range(count)
    ]
    edges = lines(*corners)
    random.Random(4).shuffle(edges)
    started = time.perf_counter()
    props = area(FakeKernel(), document(*edges), ids(*range(1, count + 1)))
    assert time.perf_counter() - started < 5.0
    polygon = count / 2 * 100**2 * math.sin(2 * math.pi / count)
    assert props.area == pytest.approx(polygon, rel=1e-9)
