"""Analytic, in-memory Kernel for tests.

Supports what the provisional Kernel protocol asks for (a face inside one loop of lines and
arcs, or one Rectangle or Circle, less any holes) with closed-form formulas: Green's theorem
over each edge (`caliper.engine.profiles.properties`). It lets engine tests run without
OCCT, and it is held to the same conformance suite as OCCTKernel
(tests/engine/geometry/test_kernel_conformance.py).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from caliper.contracts.kernel import KernelError, Loop, Shape
from caliper.contracts.queries import AreaProperties, BoundingBox
from caliper.engine import profiles

if TYPE_CHECKING:
    from caliper.contracts.kernel import Kernel


@dataclass(frozen=True, slots=True)
class FakeFace:
    """A planar face: inside `outer`, outside each hole."""

    outer: Loop
    holes: tuple[Loop, ...] = ()


class FakeKernel:
    def make_face(self, outer: Loop, holes: Sequence[Loop] = ()) -> Shape:
        for loop in (outer, *holes):
            if (problem := profiles.loop_problem(loop)) is not None:
                raise KernelError(problem.code, problem.message)
        return FakeFace(outer=outer, holes=tuple(holes))

    def area_properties(self, face: Shape) -> AreaProperties:
        own = _own(face)
        return profiles.properties(profiles.Profile(outer=own.outer, holes=own.holes))

    def bounding_box(self, shape: Shape) -> BoundingBox:
        return profiles.bounds(_own(shape).outer)

    def is_valid(self, shape: Shape) -> bool:
        return isinstance(shape, FakeFace)


def _own(shape: Shape) -> FakeFace:
    if not isinstance(shape, FakeFace):
        raise TypeError(f"shape {shape!r} was not made by FakeKernel")
    return shape


if TYPE_CHECKING:
    _conforms: Kernel = FakeKernel()
