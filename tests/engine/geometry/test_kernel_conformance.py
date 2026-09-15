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

from caliper.contracts.document import Circle, Geometry, Line, Point2, Rectangle
from caliper.contracts.errors import ErrorCode
from caliper.contracts.kernel import Kernel, KernelError
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
    props = kernel.area_properties(kernel.make_face([rect]))
    w, h, s = rect.width, rect.height, rectangle_scale(rect)
    assert props.area == approx(w * h, scale=s, dimension=2)
    assert props.centroid.x == approx(rect.corner.x + w / 2, scale=s, dimension=1)
    assert props.centroid.y == approx(rect.corner.y + h / 2, scale=s, dimension=1)
    assert props.ixx == approx(w * h**3 / 12, scale=s, dimension=4)
    assert props.iyy == approx(h * w**3 / 12, scale=s, dimension=4)
    assert props.ixy == approx(0.0, scale=s, dimension=4)


@given(circle=circles)
def test_circle_area_properties(kernel: Kernel, circle: Circle) -> None:
    props = kernel.area_properties(kernel.make_face([circle]))
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
    before = kernel.area_properties(kernel.make_face([rect]))
    after = kernel.area_properties(kernel.make_face([moved]))
    s = max(rectangle_scale(rect), rectangle_scale(moved))
    assert after.area == approx(before.area, scale=s, dimension=2)
    assert after.ixx == approx(before.ixx, scale=s, dimension=4)
    assert after.iyy == approx(before.iyy, scale=s, dimension=4)
    assert after.centroid.x == approx(before.centroid.x + dx, scale=s, dimension=1)
    assert after.centroid.y == approx(before.centroid.y + dy, scale=s, dimension=1)


# --- Bounding boxes ---------------------------------------------------------------------


@given(rect=rectangles)
def test_rectangle_bounding_box_is_tight(kernel: Kernel, rect: Rectangle) -> None:
    box = kernel.bounding_box(kernel.make_face([rect]))
    s = rectangle_scale(rect)
    assert box.x_min == approx(rect.corner.x, scale=s, dimension=1)
    assert box.y_min == approx(rect.corner.y, scale=s, dimension=1)
    assert box.x_max == approx(rect.corner.x + rect.width, scale=s, dimension=1)
    assert box.y_max == approx(rect.corner.y + rect.height, scale=s, dimension=1)


@given(circle=circles)
def test_circle_bounding_box_is_tight(kernel: Kernel, circle: Circle) -> None:
    box = kernel.bounding_box(kernel.make_face([circle]))
    c, r, s = circle.center, circle.radius, circle_scale(circle)
    assert box.x_min == approx(c.x - r, scale=s, dimension=1)
    assert box.y_min == approx(c.y - r, scale=s, dimension=1)
    assert box.x_max == approx(c.x + r, scale=s, dimension=1)
    assert box.y_max == approx(c.y + r, scale=s, dimension=1)


# --- Validity and rejection -------------------------------------------------------------


@given(profile=st.one_of(rectangles, circles))
def test_faces_from_valid_profiles_are_valid(kernel: Kernel, profile: Geometry) -> None:
    assert kernel.is_valid(kernel.make_face([profile]))


ORIGIN = Point2(x=0.0, y=0.0)
UNIT_RECTANGLE = Rectangle(corner=ORIGIN, width=1.0, height=1.0)
UNIT_CIRCLE = Circle(center=ORIGIN, radius=1.0)


@pytest.mark.parametrize(
    "boundary",
    [
        [],
        [Line(start=ORIGIN, end=Point2(x=1.0, y=0.0))],
        [UNIT_RECTANGLE, UNIT_CIRCLE],
    ],
    ids=["empty", "open-line", "two-profiles"],
)
def test_rejects_boundaries_that_are_not_one_closed_profile(
    kernel: Kernel, boundary: list[Geometry]
) -> None:
    with pytest.raises(KernelError) as caught:
        kernel.make_face(boundary)
    assert caught.value.code is ErrorCode.PROFILE_NOT_CLOSED


@pytest.mark.parametrize(
    "profile",
    [
        Rectangle(corner=ORIGIN, width=0.0, height=1.0),
        Rectangle(corner=ORIGIN, width=1.0, height=-1.0),
        Rectangle(corner=Point2(x=math.nan, y=0.0), width=1.0, height=1.0),
        Circle(center=ORIGIN, radius=0.0),
        Circle(center=ORIGIN, radius=math.inf),
    ],
    ids=["zero-width", "negative-height", "nan-corner", "zero-radius", "infinite-radius"],
)
def test_rejects_degenerate_profiles(kernel: Kernel, profile: Geometry) -> None:
    with pytest.raises(KernelError) as caught:
        kernel.make_face([profile])
    assert caught.value.code is ErrorCode.GEOMETRY_DEGENERATE
