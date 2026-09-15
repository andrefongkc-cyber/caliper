"""Analytic, in-memory Kernel for tests.

Supports what the provisional Kernel protocol asks of V1 (a face bounded by exactly one
Rectangle or Circle) with closed-form formulas. It lets engine tests run without OCCT, and
it is held to the same conformance suite as OCCTKernel
(tests/engine/geometry/test_kernel_conformance.py).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from caliper.contracts.document import Circle, Geometry, Point2, Rectangle
from caliper.contracts.errors import ErrorCode
from caliper.contracts.kernel import KernelError, Shape
from caliper.contracts.queries import AreaProperties, BoundingBox

if TYPE_CHECKING:
    from caliper.contracts.kernel import Kernel


@dataclass(frozen=True, slots=True)
class FakeFace:
    """A planar face bounded by a single closed primitive."""

    boundary: Rectangle | Circle


class FakeKernel:
    def make_face(self, boundary: Sequence[Geometry]) -> Shape:
        if len(boundary) != 1 or not isinstance(boundary[0], Rectangle | Circle):
            raise KernelError(
                ErrorCode.PROFILE_NOT_CLOSED,
                "a face needs exactly one Rectangle or Circle as its boundary",
            )
        profile = boundary[0]
        numbers: tuple[float, ...]
        sizes: tuple[float, ...]
        match profile:
            case Rectangle(corner=corner, width=width, height=height):
                numbers = (corner.x, corner.y, width, height)
                sizes = (width, height)
            case Circle(center=center, radius=radius):
                numbers = (center.x, center.y, radius)
                sizes = (radius,)
            case _:
                assert_never(profile)
        if not all(math.isfinite(n) for n in numbers) or not all(s > 0 for s in sizes):
            raise KernelError(ErrorCode.GEOMETRY_DEGENERATE, f"degenerate profile: {profile}")
        return FakeFace(boundary=profile)

    def area_properties(self, face: Shape) -> AreaProperties:
        match _own(face).boundary:
            case Rectangle(corner=corner, width=w, height=h):
                return AreaProperties(
                    area=w * h,
                    centroid=Point2(x=corner.x + w / 2, y=corner.y + h / 2),
                    ixx=w * h**3 / 12,
                    iyy=h * w**3 / 12,
                    ixy=0.0,
                )
            case Circle(center=center, radius=r):
                polar_half = math.pi * r**4 / 4
                return AreaProperties(
                    area=math.pi * r**2, centroid=center, ixx=polar_half, iyy=polar_half, ixy=0.0
                )
            case other:
                assert_never(other)

    def bounding_box(self, shape: Shape) -> BoundingBox:
        match _own(shape).boundary:
            case Rectangle(corner=corner, width=w, height=h):
                return BoundingBox(
                    x_min=corner.x, y_min=corner.y, x_max=corner.x + w, y_max=corner.y + h
                )
            case Circle(center=center, radius=r):
                return BoundingBox(
                    x_min=center.x - r, y_min=center.y - r, x_max=center.x + r, y_max=center.y + r
                )
            case other:
                assert_never(other)

    def is_valid(self, shape: Shape) -> bool:
        return isinstance(shape, FakeFace)


def _own(shape: Shape) -> FakeFace:
    if not isinstance(shape, FakeFace):
        raise TypeError(f"shape {shape!r} was not made by FakeKernel")
    return shape


if TYPE_CHECKING:
    _conforms: Kernel = FakeKernel()
