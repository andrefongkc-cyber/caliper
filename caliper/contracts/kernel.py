"""Geometry kernel protocol. PROVISIONAL: expected to change shape at V2.

Unlike the rest of contracts/, this protocol does not freeze. It will change when solids,
booleans, and topology arrive, and it stays deliberately small until then: only what V1
queries need from a B-rep kernel.

The kernel sits below the engine; the UI and AI layers never call it. Nothing it returns
is serialized, because kernel output can differ in the last bits across platforms.

Implementations: FakeKernel (analytic, for tests) and OCCTKernel
(caliper/engine/geometry/occt_kernel.py, the only module allowed to import OCP). Both must
pass the same conformance suite.
"""

from collections.abc import Sequence
from typing import Protocol

from caliper.contracts.document import Geometry
from caliper.contracts.errors import ErrorCode
from caliper.contracts.queries import AreaProperties, BoundingBox


class Shape(Protocol):
    """Opaque handle owned by one kernel. Never serialized, never passed to another kernel."""


class KernelError(Exception):
    """The kernel could not build or evaluate a shape.

    Internal to the engine, which converts it to an `Error` at the query boundary.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class Kernel(Protocol):
    def make_face(self, boundary: Sequence[Geometry]) -> Shape:
        """Planar face bounded by a closed profile. V1: exactly one Rectangle or Circle."""
        ...

    def area_properties(self, face: Shape) -> AreaProperties: ...

    def bounding_box(self, shape: Shape) -> BoundingBox: ...

    def is_valid(self, shape: Shape) -> bool:
        """Whether the shape passes the kernel's own topology and geometry checks."""
        ...
