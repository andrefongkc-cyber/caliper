"""Kernel conformance suite (ADR 0001).

Every Kernel implementation must pass this file. It runs against FakeKernel always, and
against OCCTKernel whenever the `occt` extra is installed, so a test can't be
green on the fake and red on the real kernel.

Expected values are the textbook formulas. Tolerances are the contract: relative 1e-9, or
an absolute bound that grows with the shape's size, because a real kernel's integrals and
bounding boxes are accurate to its geometric tolerance, not to the last bit.
"""

import math
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.document import Arc, Circle, Geometry, Line, Point2, Rectangle
from caliper.contracts.errors import ErrorCode
from caliper.contracts.kernel import Kernel, KernelError, Loop
from caliper.engine.geometry.fake_kernel import FakeKernel

LINEAR_TOLERANCE = 1e-6
"""mm. About 10x OCCT's default geometric precision (1e-7 mm)."""


@pytest.fixture(scope="module", params=["fake", "occt"])
def kernel(request: pytest.FixtureRequest) -> Kernel:
    if request.param == "fake":
        return FakeKernel()
    occt = pytest.importorskip(
        "caliper.engine.geometry.occt_kernel",
        reason="the occt extra isn't installed",
    )
    kernel: Kernel = occt.OCCTKernel()
    return kernel


def approx(expected: float, *, scale: float, dimension: int) -> Any:
    """Tolerance for a quantity with units of mm**dimension on a shape of size `scale`."""
    return pytest.approx(expected, rel=1e-9, abs=LINEAR_TOLERANCE * scale ** (dimension - 1))


coordinates = st.floats(min_value=-1e3, max_value=1e3)
lengths = st.floats(min_value=0.1, max_value=1e3)
points = st.builds(Point2, x=coordinates, y=coordinates)
rectangles = st.builds(Rectangle, corner=points, width=lengths, height=lengths)
circles = st.builds(Circle, center=points, radius=lengths)


def rectangle_scale(r: Rectangle) -> float:
    return max(1.0, abs(r.corner.x), abs(r.corner.y), r.width, r.height)


def circle_scale(c: Circle) -> float:
    return max(1.0, abs(c.center.x), abs(c.center.y), c.radius)


# --- Area properties --------------------------------------------------------------------


@given(rect=rectangles)
def test_rectangle_area_properties(kernel: Kernel, rect: Rectangle) -> None:
    props = kernel.area_properties(kernel.make_face(Loop(edges=(rect,))))
    w, h, s = rect.width, rect.height, rectangle_scale(rect)
    assert props.area == approx(w * h, scale=s, dimension=2)
    assert props.centroid.x == approx(rect.corner.x + w / 2, scale=s, dimension=1)
    assert props.centroid.y == approx(rect.corner.y + h / 2, scale=s, dimension=1)
    assert props.ixx == approx(w * h**3 / 12, scale=s, dimension=4)
    assert props.iyy == approx(h * w**3 / 12, scale=s, dimension=4)
    assert props.ixy == approx(0.0, scale=s, dimension=4)


@given(circle=circles)
def test_circle_area_properties(kernel: Kernel, circle: Circle) -> None:
    props = kernel.area_properties(kernel.make_face(Loop(edges=(circle,))))
    r, s = circle.radius, circle_scale(circle)
    assert props.area == approx(math.pi * r**2, scale=s, dimension=2)
    assert props.centroid.x == approx(circle.center.x, scale=s, dimension=1)
    assert props.centroid.y == approx(circle.center.y, scale=s, dimension=1)
    assert props.ixx == approx(math.pi * r**4 / 4, scale=s, dimension=4)
    assert props.iyy == approx(math.pi * r**4 / 4, scale=s, dimension=4)
    assert props.ixy == approx(0.0, scale=s, dimension=4)


@given(rect=rectangles, dx=coordinates, dy=coordinates)
def test_translation_moves_the_centroid_and_nothing_else(
    kernel: Kernel, rect: Rectangle, dx: float, dy: float
) -> None:
    moved = Rectangle(
        corner=Point2(x=rect.corner.x + dx, y=rect.corner.y + dy),
        width=rect.width,
        height=rect.height,
    )
    before = kernel.area_properties(kernel.make_face(Loop(edges=(rect,))))
    after = kernel.area_properties(kernel.make_face(Loop(edges=(moved,))))
    s = max(rectangle_scale(rect), rectangle_scale(moved))
    assert after.area == approx(before.area, scale=s, dimension=2)
    assert after.ixx == approx(before.ixx, scale=s, dimension=4)
    assert after.iyy == approx(before.iyy, scale=s, dimension=4)
    assert after.centroid.x == approx(before.centroid.x + dx, scale=s, dimension=1)
    assert after.centroid.y == approx(before.centroid.y + dy, scale=s, dimension=1)


# --- Bounding boxes ---------------------------------------------------------------------


@given(rect=rectangles)
def test_rectangle_bounding_box_is_tight(kernel: Kernel, rect: Rectangle) -> None:
    box = kernel.bounding_box(kernel.make_face(Loop(edges=(rect,))))
    s = rectangle_scale(rect)
    assert box.x_min == approx(rect.corner.x, scale=s, dimension=1)
    assert box.y_min == approx(rect.corner.y, scale=s, dimension=1)
    assert box.x_max == approx(rect.corner.x + rect.width, scale=s, dimension=1)
    assert box.y_max == approx(rect.corner.y + rect.height, scale=s, dimension=1)


@given(circle=circles)
def test_circle_bounding_box_is_tight(kernel: Kernel, circle: Circle) -> None:
    box = kernel.bounding_box(kernel.make_face(Loop(edges=(circle,))))
    c, r, s = circle.center, circle.radius, circle_scale(circle)
    assert box.x_min == approx(c.x - r, scale=s, dimension=1)
    assert box.y_min == approx(c.y - r, scale=s, dimension=1)
    assert box.x_max == approx(c.x + r, scale=s, dimension=1)
    assert box.y_max == approx(c.y + r, scale=s, dimension=1)


# --- Validity and rejection -------------------------------------------------------------


@given(profile=st.one_of(rectangles, circles))
def test_faces_from_valid_profiles_are_valid(kernel: Kernel, profile: Geometry) -> None:
    assert kernel.is_valid(kernel.make_face(Loop(edges=(profile,))))


ORIGIN = Point2(x=0.0, y=0.0)
UNIT_RECTANGLE = Rectangle(corner=ORIGIN, width=1.0, height=1.0)
UNIT_CIRCLE = Circle(center=ORIGIN, radius=1.0)


@pytest.mark.parametrize(
    "boundary",
    [
        [],
        [Line(start=ORIGIN, end=Point2(x=1.0, y=0.0))],
        [UNIT_RECTANGLE, UNIT_CIRCLE],
        [
            Line(start=ORIGIN, end=Point2(x=1.0, y=0.0)),
            Line(start=Point2(x=1.0, y=0.0), end=Point2(x=1.0, y=1.0)),
            Line(start=Point2(x=1.0, y=1.0), end=Point2(x=0.0, y=0.9)),
        ],
    ],
    ids=["empty", "open-line", "two-profiles", "gap"],
)
def test_rejects_boundaries_that_are_not_one_closed_profile(
    kernel: Kernel, boundary: list[Geometry]
) -> None:
    with pytest.raises(KernelError) as caught:
        kernel.make_face(Loop(edges=tuple(boundary)))
    assert caught.value.code is ErrorCode.PROFILE_NOT_CLOSED


@pytest.mark.parametrize(
    "profile",
    [
        Line(start=ORIGIN, end=ORIGIN),
        Rectangle(corner=ORIGIN, width=0.0, height=1.0),
        Rectangle(corner=ORIGIN, width=1.0, height=-1.0),
        Rectangle(corner=Point2(x=math.nan, y=0.0), width=1.0, height=1.0),
        Circle(center=ORIGIN, radius=0.0),
        Circle(center=ORIGIN, radius=math.inf),
    ],
    ids=[
        "zero-length-line",
        "zero-width",
        "negative-height",
        "nan-corner",
        "zero-radius",
        "infinite-radius",
    ],
)
def test_rejects_degenerate_profiles(kernel: Kernel, profile: Geometry) -> None:
    with pytest.raises(KernelError) as caught:
        kernel.make_face(Loop(edges=(profile,)))
    assert caught.value.code is ErrorCode.GEOMETRY_DEGENERATE


# --- Loops of lines and arcs, and holes (N4) ---------------------------------------------
# Expected values come from textbook formulas, and for polygons from the polygon formulas,
# worked out here independently of the engine's integrals.


def polygon(*corners: tuple[float, float]) -> Loop:
    points = [Point2(x=x, y=y) for x, y in corners]
    return Loop(
        edges=tuple(
            Line(start=a, end=b) for a, b in zip(points, [*points[1:], points[0]], strict=True)
        )
    )


def polygon_properties(
    corners: list[tuple[float, float]],
) -> tuple[float, float, float, float, float, float]:
    """Area, centroid, and second moments about the centroid of a simple polygon."""
    a = cx = cy = ixx = iyy = ixy = 0.0
    for (x0, y0), (x1, y1) in zip(corners, [*corners[1:], corners[0]], strict=True):
        cross = x0 * y1 - x1 * y0
        a += cross / 2
        cx += (x0 + x1) * cross / 6
        cy += (y0 + y1) * cross / 6
        ixx += (y0 * y0 + y0 * y1 + y1 * y1) * cross / 12
        iyy += (x0 * x0 + x0 * x1 + x1 * x1) * cross / 12
        ixy += (x0 * y1 + 2 * x0 * y0 + 2 * x1 * y1 + x1 * y0) * cross / 24
    cx, cy = cx / a, cy / a
    return a, cx, cy, ixx - a * cy * cy, iyy - a * cx * cx, ixy - a * cx * cy


def rounded_rectangle(x: float, y: float, w: float, h: float, r: float) -> Loop:
    """A rectangle with corners of radius r, counter-clockwise, as lines and arcs."""
    p = Point2
    return Loop(
        edges=(
            Line(start=p(x=x + r, y=y), end=p(x=x + w - r, y=y)),
            Arc(center=p(x=x + w - r, y=y + r), radius=r, start_angle=270.0, sweep_angle=90.0),
            Line(start=p(x=x + w, y=y + r), end=p(x=x + w, y=y + h - r)),
            Arc(center=p(x=x + w - r, y=y + h - r), radius=r, start_angle=0.0, sweep_angle=90.0),
            Line(start=p(x=x + w - r, y=y + h), end=p(x=x + r, y=y + h)),
            Arc(center=p(x=x + r, y=y + h - r), radius=r, start_angle=90.0, sweep_angle=90.0),
            Line(start=p(x=x, y=y + h - r), end=p(x=x, y=y + r)),
            Arc(center=p(x=x + r, y=y + r), radius=r, start_angle=180.0, sweep_angle=90.0),
        )
    )


@pytest.mark.parametrize(
    "corners",
    [
        [(0.0, 0.0), (4.0, 0.0), (1.0, 3.0)],
        [(0.0, 0.0), (6.0, 0.0), (6.0, 2.0), (2.0, 2.0), (2.0, 5.0), (0.0, 5.0)],
        [(10.0, 10.0), (12.0, 15.0), (9.0, 13.0), (5.0, 14.0), (6.0, 11.0)],
    ],
    ids=["triangle", "l-shape", "concave"],
)
def test_a_polygon_of_lines(kernel: Kernel, corners: list[tuple[float, float]]) -> None:
    props = kernel.area_properties(kernel.make_face(polygon(*corners)))
    area, cx, cy, ixx, iyy, ixy = polygon_properties(corners)
    s = max(abs(v) for corner in corners for v in corner)
    assert props.area == approx(area, scale=s, dimension=2)
    assert props.centroid.x == approx(cx, scale=s, dimension=1)
    assert props.centroid.y == approx(cy, scale=s, dimension=1)
    assert props.ixx == approx(ixx, scale=s, dimension=4)
    assert props.iyy == approx(iyy, scale=s, dimension=4)
    assert props.ixy == approx(ixy, scale=s, dimension=4)


def test_a_half_disc_of_an_arc_and_its_chord(kernel: Kernel) -> None:
    r = 3.0
    half = Loop(
        edges=(
            Arc(center=ORIGIN, radius=r, start_angle=0.0, sweep_angle=180.0),
            Line(start=Point2(x=-r, y=0.0), end=Point2(x=r, y=0.0)),
        )
    )
    face = kernel.make_face(half)
    props = kernel.area_properties(face)
    assert props.area == approx(math.pi * r**2 / 2, scale=r, dimension=2)
    assert props.centroid.x == approx(0.0, scale=r, dimension=1)
    assert props.centroid.y == approx(4 * r / (3 * math.pi), scale=r, dimension=1)
    assert props.ixx == approx((math.pi / 8 - 8 / (9 * math.pi)) * r**4, scale=r, dimension=4)
    assert props.iyy == approx(math.pi * r**4 / 8, scale=r, dimension=4)
    box = kernel.bounding_box(face)
    assert (box.x_min, box.y_min) == (
        approx(-r, scale=r, dimension=1),
        approx(0.0, scale=r, dimension=1),
    )
    assert (box.x_max, box.y_max) == (
        approx(r, scale=r, dimension=1),
        approx(r, scale=r, dimension=1),
    )


def test_a_slot_of_two_lines_and_two_arcs(kernel: Kernel) -> None:
    # A 40 mm slot, 10 wide, the stress plate's: one loop, mixed lines and arcs.
    p = Point2
    slot = Loop(
        edges=(
            Line(start=p(x=40.0, y=115.0), end=p(x=80.0, y=115.0)),
            Arc(center=p(x=80.0, y=120.0), radius=5.0, start_angle=270.0, sweep_angle=180.0),
            Line(start=p(x=80.0, y=125.0), end=p(x=40.0, y=125.0)),
            Arc(center=p(x=40.0, y=120.0), radius=5.0, start_angle=90.0, sweep_angle=180.0),
        )
    )
    props = kernel.area_properties(kernel.make_face(slot))
    assert props.area == approx(40 * 10 + math.pi * 25, scale=125, dimension=2)
    assert props.centroid.x == approx(60.0, scale=125, dimension=1)
    assert props.centroid.y == approx(120.0, scale=125, dimension=1)
    assert props.ixy == approx(0.0, scale=125, dimension=4)


def test_the_same_loop_backwards_or_started_elsewhere_is_the_same_face(kernel: Kernel) -> None:
    forwards = rounded_rectangle(5.0, -3.0, 30.0, 20.0, 4.0)
    backwards = Loop(edges=tuple(reversed(forwards.edges)), reversed=(True,) * len(forwards.edges))
    rotated = Loop(edges=forwards.edges[3:] + forwards.edges[:3])
    expected = kernel.area_properties(kernel.make_face(forwards))
    for loop in (backwards, rotated):
        props = kernel.area_properties(kernel.make_face(loop))
        assert props.area == approx(expected.area, scale=35, dimension=2)
        assert props.centroid.x == approx(expected.centroid.x, scale=35, dimension=1)
        assert props.ixx == approx(expected.ixx, scale=35, dimension=4)


def test_holes_are_taken_out(kernel: Kernel) -> None:
    w, h, r = 120.0, 80.0, 12.0
    outer = rounded_rectangle(0.0, 0.0, w, h, r)
    hole = Loop(edges=(Circle(center=Point2(x=30.0, y=40.0), radius=5.0),))
    window = Loop(edges=(Rectangle(corner=Point2(x=60.0, y=20.0), width=40.0, height=30.0),))
    face = kernel.make_face(outer, [hole, window])
    assert kernel.is_valid(face)
    props = kernel.area_properties(face)
    area = w * h - (4 - math.pi) * r * r - math.pi * 25 - 40 * 30
    assert props.area == approx(area, scale=w, dimension=2)
    # The centroid moves away from what was taken out, by the moments of the holes.
    plate = w * h - (4 - math.pi) * r * r
    cx = (plate * 60 - math.pi * 25 * 30 - 1200 * 80) / area
    cy = (plate * 40 - math.pi * 25 * 40 - 1200 * 35) / area
    assert props.centroid.x == approx(cx, scale=w, dimension=1)
    assert props.centroid.y == approx(cy, scale=w, dimension=1)


@given(
    x=coordinates,
    y=coordinates,
    w=st.floats(min_value=1.0, max_value=500.0),
    h=st.floats(min_value=1.0, max_value=500.0),
    share=st.floats(min_value=0.05, max_value=0.45),
)
def test_rounded_rectangles(
    kernel: Kernel, x: float, y: float, w: float, h: float, share: float
) -> None:
    r = share * min(w, h)
    props = kernel.area_properties(kernel.make_face(rounded_rectangle(x, y, w, h, r)))
    s = max(1.0, abs(x), abs(y), w, h)
    assert props.area == approx(w * h - (4 - math.pi) * r * r, scale=s, dimension=2)
    assert props.centroid.x == approx(x + w / 2, scale=s, dimension=1)
    assert props.centroid.y == approx(y + h / 2, scale=s, dimension=1)
    assert props.ixy == approx(0.0, scale=s, dimension=4)
