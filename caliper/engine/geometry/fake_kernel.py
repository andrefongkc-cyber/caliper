"""Analytic, in-memory Kernel for tests.

Supports what the provisional Kernel protocol asks for with closed-form formulas. A face is
inside one loop of lines and arcs (or one Rectangle or Circle), less any holes, measured by
Green's theorem over each edge (`caliper.engine.profiles.properties`). A solid (V2's F2) is a
set of prisms whose insides don't overlap: a face swept along its frame's normal, so its volume
is the face's area times the depth, exactly.

Combining solids is exact only where no 3D boolean is needed: pieces whose boxes don't overlap,
or, on one frame, a piece wholly inside another (a union that adds nothing, a cut through
the whole depth that leaves a hole, or one that leaves nothing). Anything else is
`kernel.unsupported`, never a guess. It lets engine tests run without OCCT, and it is held to
the same conformance suite as OCCTKernel (tests/engine/geometry/).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from caliper.contracts.document import Point2
from caliper.contracts.errors import ErrorCode
from caliper.contracts.kernel import Frame, KernelError, Loop, Shape
from caliper.contracts.queries import AreaProperties, BoundingBox, BoundingBox3, Mesh, Point3
from caliper.engine import profiles
from caliper.engine.geometry.triangulate import triangulate

if TYPE_CHECKING:
    from caliper.contracts.kernel import Kernel


@dataclass(frozen=True, slots=True)
class FakeFace:
    """A planar face: inside `outer`, outside each hole."""

    outer: Loop
    holes: tuple[Loop, ...] = ()


@dataclass(frozen=True, slots=True)
class FakePrism:
    """`face`, placed on `frame`, swept `depth` along the frame's normal."""

    face: FakeFace
    frame: Frame
    depth: float


@dataclass(frozen=True, slots=True)
class FakeSolid:
    """Prisms whose insides don't overlap. None at all is a solid that is nothing."""

    prisms: tuple[FakePrism, ...]


class FakeKernel:
    def make_face(self, outer: Loop, holes: Sequence[Loop] = ()) -> Shape:
        for loop in (outer, *holes):
            if (problem := profiles.loop_problem(loop)) is not None:
                raise KernelError(problem.code, problem.message)
        return FakeFace(outer=outer, holes=tuple(holes))

    def area_properties(self, face: Shape) -> AreaProperties:
        own = _face(face)
        return profiles.properties(profiles.Profile(outer=own.outer, holes=own.holes))

    def bounding_box(self, shape: Shape) -> BoundingBox:
        return profiles.bounds(_face(shape).outer)

    def is_valid(self, shape: Shape) -> bool:
        return isinstance(shape, FakeFace | FakeSolid)

    # --- Solids -------------------------------------------------------------------------

    def extrude(self, face: Shape, frame: Frame, depth: float) -> Shape:
        if not depth > 0:
            raise KernelError(ErrorCode.VALUE_NOT_POSITIVE, "depth must be greater than 0")
        return FakeSolid(prisms=(FakePrism(face=_face(face), frame=frame, depth=depth),))

    def union(self, a: Shape, b: Shape) -> Shape:
        pieces = list(_solid(a).prisms)
        for added in _solid(b).prisms:
            if any(_contains(piece, added) for piece in pieces):
                continue  # adds nothing
            pieces = [piece for piece in pieces if not _contains(added, piece)]
            if not all(_apart(piece, added) for piece in pieces):
                raise _unsupported("join solids that overlap")
            pieces.append(added)
        return FakeSolid(prisms=tuple(pieces))

    def cut(self, a: Shape, b: Shape) -> Shape:
        left: list[FakePrism] = []
        for piece in _solid(a).prisms:
            kept: FakePrism | None = piece
            for tool in _solid(b).prisms:
                if kept is None or _apart(kept, tool):
                    continue
                if _contains(tool, kept):
                    kept = None
                elif _through(tool, kept):
                    kept = replace(
                        kept, face=replace(kept.face, holes=(*kept.face.holes, tool.face.outer))
                    )
                else:
                    raise _unsupported("cut one solid with another that overlaps it partly")
            if kept is not None:
                left.append(kept)
        return FakeSolid(prisms=tuple(left))

    def volume(self, solid: Shape) -> float:
        return sum(_area(piece.face) * piece.depth for piece in _solid(solid).prisms)

    def bounding_box_3d(self, solid: Shape) -> BoundingBox3:
        boxes = [_box(piece) for piece in _solid(solid).prisms]
        if not boxes:
            raise KernelError(ErrorCode.SELECTION_EMPTY, "the solid is nothing: it has no bounds")
        return BoundingBox3(
            x_min=min(b.x_min for b in boxes),
            y_min=min(b.y_min for b in boxes),
            z_min=min(b.z_min for b in boxes),
            x_max=max(b.x_max for b in boxes),
            y_max=max(b.y_max for b in boxes),
            z_max=max(b.z_max for b in boxes),
        )

    def mesh(self, solid: Shape, tolerance: float) -> Mesh:
        vertices: list[Point3] = []
        triangles: list[tuple[int, int, int]] = []

        def add(*corners: Point3) -> None:
            start = len(vertices)
            vertices.extend(corners)
            triangles.append((start, start + 1, start + 2))

        for piece in _solid(solid).prisms:
            frame, depth = piece.frame, piece.depth
            outer = profiles.polygon(piece.face.outer, tolerance)
            holes = [profiles.polygon(hole, tolerance) for hole in piece.face.holes]
            for a, b, c in triangulate(outer, holes):
                add(*(_at(frame, p, depth) for p in (a, b, c)))  # top, facing along the normal
                add(*(_at(frame, p, 0.0) for p in (a, c, b)))  # bottom, facing back
            # The sides: every loop run with the face on its left, so outward is on its right.
            rings = [_ccw(outer), *(_ccw(hole)[::-1] for hole in holes)]
            for ring in rings:
                for p, q in zip(ring, [*ring[1:], ring[0]], strict=True):
                    p0, q0 = _at(frame, p, 0.0), _at(frame, q, 0.0)
                    p1, q1 = _at(frame, p, depth), _at(frame, q, depth)
                    add(p0, q0, q1)
                    add(p0, q1, p1)
        return Mesh(vertices=tuple(vertices), triangles=tuple(triangles))


def _face(shape: Shape) -> FakeFace:
    if not isinstance(shape, FakeFace):
        raise TypeError(f"shape {shape!r} isn't a face made by FakeKernel")
    return shape


def _solid(shape: Shape) -> FakeSolid:
    if not isinstance(shape, FakeSolid):
        raise TypeError(f"shape {shape!r} isn't a solid made by FakeKernel")
    return shape


def _unsupported(what: str) -> KernelError:
    return KernelError(
        ErrorCode.KERNEL_UNSUPPORTED,
        f"the analytic kernel can't {what}; the occt extra can",
    )


def _area(face: FakeFace) -> float:
    return profiles.properties(profiles.Profile(outer=face.outer, holes=face.holes)).area


def _normal(frame: Frame) -> Point3:
    x, y = frame.x, frame.y
    return Point3(x=x.y * y.z - x.z * y.y, y=x.z * y.x - x.x * y.z, z=x.x * y.y - x.y * y.x)


def _at(frame: Frame, p: Point2, w: float) -> Point3:
    """The 3D point at (p.x, p.y) on the frame, `w` along its normal."""
    o, x, y, n = frame.origin, frame.x, frame.y, _normal(frame)
    return Point3(
        x=o.x + p.x * x.x + p.y * y.x + w * n.x,
        y=o.y + p.x * x.y + p.y * y.y + w * n.y,
        z=o.z + p.x * x.z + p.y * y.z + w * n.z,
    )


def _box(piece: FakePrism) -> BoundingBox3:
    """Exact for the origin planes, whose axes are the part's own."""
    flat = profiles.bounds(piece.face.outer)
    corners = [
        _at(piece.frame, Point2(x=u, y=v), w)
        for u in (flat.x_min, flat.x_max)
        for v in (flat.y_min, flat.y_max)
        for w in (0.0, piece.depth)
    ]
    return BoundingBox3(
        x_min=min(c.x for c in corners),
        y_min=min(c.y for c in corners),
        z_min=min(c.z for c in corners),
        x_max=max(c.x for c in corners),
        y_max=max(c.y for c in corners),
        z_max=max(c.z for c in corners),
    )


def _apart(a: FakePrism, b: FakePrism) -> bool:
    """Boxes that at most touch: the insides can't overlap."""
    p, q = _box(a), _box(b)
    return (
        p.x_max <= q.x_min
        or q.x_max <= p.x_min
        or p.y_max <= q.y_min
        or q.y_max <= p.y_min
        or p.z_max <= q.z_min
        or q.z_max <= p.z_min
    )


def _contains(outer: FakePrism, inner: FakePrism) -> bool:
    """Whether `inner` is wholly inside `outer`: the same frame, no deeper, and its outline
    inside `outer`'s face, clear of its holes."""
    return (
        outer.frame == inner.frame
        and inner.depth <= outer.depth
        and profiles.within(inner.face.outer, _region(outer.face))
    )


def _through(tool: FakePrism, piece: FakePrism) -> bool:
    """Whether cutting `tool` from `piece` leaves a hole through it: the same frame, at least
    as deep, a tool with no holes of its own, its outline inside `piece`'s face."""
    return (
        tool.frame == piece.frame
        and tool.depth >= piece.depth
        and not tool.face.holes
        and profiles.within(tool.face.outer, _region(piece.face))
    )


def _region(face: FakeFace) -> profiles.Profile:
    return profiles.Profile(outer=face.outer, holes=face.holes)


def _ccw(points: list[Point2]) -> list[Point2]:
    total = sum(
        p.x * q.y - q.x * p.y for p, q in zip(points, [*points[1:], points[0]], strict=True)
    )
    return points if total > 0 else points[::-1]


if TYPE_CHECKING:
    _conforms: Kernel = FakeKernel()
