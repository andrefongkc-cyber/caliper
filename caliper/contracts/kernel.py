"""Geometry kernel protocol. PROVISIONAL: expected to change shape at V2.

Unlike the rest of contracts/, this protocol does not freeze. It will change when solids,
booleans, and topology arrive, and it stays deliberately small until then: only what V1
queries need from a B-rep kernel.

The kernel sits below the engine; the UI and AI layers never call it. Nothing it returns
is serialized, because kernel output can differ in the last bits across platforms.

Implementations: FakeKernel (analytic, for tests) and OCCTKernel
(caliper/engine/geometry/occt_kernel.py, the only module allowed to import OCP). Both must
pass the same conformance suite.

N4 (2026-09-30): a face was one Rectangle or Circle. It is now bounded by `Loop`s, one outer
and any holes, of lines and arcs joined end to end; the engine finds them
(`caliper.engine.profiles`), so a kernel is given loops, not a pile of edges.

V2's F2 (2026-10-01): solids. A face is extruded from a sketch's plane (`Frame`), solids
combine by `union` and `cut`, and a solid gives its `volume`, `bounding_box_3d`, and a `mesh`
to draw. Shapes stay opaque and are never serialized.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from caliper.contracts.document import Geometry
from caliper.contracts.errors import ErrorCode
from caliper.contracts.queries import AreaProperties, BoundingBox, BoundingBox3, Mesh, Point3


@dataclass(frozen=True, slots=True, kw_only=True)
class Loop:
    """A closed boundary: one Rectangle or Circle on its own, or Lines and Arcs in order.

    In order means each edge, as traversed, ends where the next one starts, and the last ends
    where the first starts, to within the engine's joining tolerance (they may differ in the
    last bits). `reversed[i]` is true where edge i is traversed from its end to its start: an
    Arc's end is where its counter-clockwise sweep ends. The loop may run either way round.
    """

    edges: tuple[Geometry, ...]
    reversed: tuple[bool, ...] = ()
    """Empty means no edge is reversed; otherwise one flag per edge."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Frame:
    """Where a face drawn in 2D sits in 3D: a 2D point (u, v) is `origin + u * x + v * y`.

    `x` and `y` are unit directions at right angles; the normal, the way an extrusion goes, is
    x cross y. A sketch's plane gives its frame (`caliper.engine.part.frame`).
    """

    origin: Point3
    x: Point3
    y: Point3


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
    def make_face(self, outer: Loop, holes: Sequence[Loop] = ()) -> Shape:
        """Planar face inside `outer` and outside every hole. The engine has checked that the
        holes are inside `outer`, and that no boundaries cross; a loop that isn't closed, or a
        Rectangle or Circle that isn't alone in its loop, is `profile.not_closed`, and a
        non-finite or zero-size edge `geometry.degenerate`."""
        ...

    def area_properties(self, face: Shape) -> AreaProperties: ...

    def bounding_box(self, shape: Shape) -> BoundingBox: ...

    def is_valid(self, shape: Shape) -> bool:
        """Whether the shape passes the kernel's own topology and geometry checks."""
        ...

    # --- Solids (V2) --------------------------------------------------------------------

    def extrude(self, face: Shape, frame: Frame, depth: float) -> Shape:
        """The solid swept by `face`, placed on `frame`, `depth` mm along the frame's normal.
        `depth` must be more than 0: `value.not_positive` otherwise."""
        ...

    def union(self, a: Shape, b: Shape) -> Shape:
        """Everything in `a` or `b`. `kernel.unsupported` where this kernel can't be exact."""
        ...

    def cut(self, a: Shape, b: Shape) -> Shape:
        """What of `a` isn't in `b`, which may be nothing: a solid with no volume.
        `kernel.unsupported` where this kernel can't be exact."""
        ...

    def volume(self, solid: Shape) -> float:
        """mm³: 0 for a solid that is nothing."""
        ...

    def bounding_box_3d(self, solid: Shape) -> BoundingBox3:
        """Tight bounds of a solid with volume; `selection.empty` for one with none."""
        ...

    def mesh(self, solid: Shape, tolerance: float) -> Mesh:
        """Triangles within `tolerance` mm of the solid's surface, wound outward."""
        ...
