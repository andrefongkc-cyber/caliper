"""Analytic, in-memory Kernel for tests.

Supports what the provisional Kernel protocol asks for with closed-form formulas. A face is
inside one loop of lines and arcs (or one Rectangle or Circle), less any holes, measured by
Green's theorem over each edge (`caliper.engine.profiles.properties`). A solid (V2's F2) is a
set of prisms whose insides don't overlap: a face swept along its frame's normal, so its volume
is the face's area times the depth, exactly.

Combining solids is exact only where no 3D boolean is needed: pieces whose boxes don't overlap,
or pieces on one plane family, where one frame is the other moved along its normal, or turned
over (ADR 0016: a face's plane is a sketch plane moved by a depth). There a piece wholly inside
another adds nothing, and a cut splits the piece into layers along the normal: as it was below
the tool and above it, and between, holed by the tool's outline, or gone where the tool covers
it. So a pocket cut from a top or bottom face, a slot, and a hole through are exact. Anything
else is `kernel.unsupported`, never a guess. It lets engine tests run without OCCT, and it is
held to the same conformance suite as OCCTKernel (tests/engine/geometry/).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from caliper.contracts.document import Arc, Circle, Geometry, Line, Point2, Rectangle
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
        pieces = list(_solid(a).prisms)
        for tool in _solid(b).prisms:
            left: list[FakePrism] = []
            for piece in pieces:
                rest = [piece] if _apart(piece, tool) else _less(piece, tool)
                if rest is None:
                    raise _unsupported("cut one solid with another that overlaps it partly")
                left += rest
            pieces = left
        return FakeSolid(prisms=tuple(pieces))

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
    """Exact for any frame: along each of the part's axes, the face's extent in the frame's
    own directions, and the depth along its normal."""
    frame, n = piece.frame, _normal(piece.frame)
    lows, highs = [], []
    for axis in ("x", "y", "z"):
        a, b, w = getattr(frame.x, axis), getattr(frame.y, axis), getattr(n, axis)
        low, high = profiles.extent(piece.face.outer, a, b)
        start = getattr(frame.origin, axis)
        lows.append(start + low + min(0.0, w * piece.depth))
        highs.append(start + high + max(0.0, w * piece.depth))
    return BoundingBox3(
        x_min=lows[0], y_min=lows[1], z_min=lows[2], x_max=highs[0], y_max=highs[1], z_max=highs[2]
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


@dataclass(frozen=True, slots=True)
class _Placed:
    """A prism seen on another's frame: its face in that frame's coordinates, and the stretch
    of that frame's normal it fills, from `low` to `high`."""

    face: FakeFace
    low: float
    high: float


_ALONG = 1e-9
"""How far, in mm, two frames' origins may stray from one line along the normal and still be
one plane family: the rounding in working out a face's plane, not a real offset."""


def _on(frame: Frame, piece: FakePrism) -> _Placed | None:
    """`piece` on `frame`: when its own frame is `frame` moved along the normal, or that turned
    over (y and the normal reversed, as a bottom face's plane is). None otherwise."""
    if piece.frame == frame:
        return _Placed(piece.face, 0.0, piece.depth)
    own = piece.frame
    if own.x != frame.x:
        return None
    if own.y == frame.y:
        over = False
    elif own.y == Point3(x=-frame.y.x, y=-frame.y.y, z=-frame.y.z):
        over = True
    else:
        return None
    o, p = frame.origin, own.origin
    d = Point3(x=p.x - o.x, y=p.y - o.y, z=p.z - o.z)
    if abs(_dot(d, frame.x)) > _ALONG or abs(_dot(d, frame.y)) > _ALONG:
        return None
    k = _dot(d, _normal(frame))
    if not over:
        return _Placed(piece.face, k, k + piece.depth)
    return _Placed(_turned_over(piece.face), k - piece.depth, k)


def _contains(outer: FakePrism, inner: FakePrism) -> bool:
    """Whether `inner` is wholly inside `outer`: on its plane family, within its depth, and its
    outline inside `outer`'s face, clear of its holes."""
    placed = _on(outer.frame, inner)
    return (
        placed is not None
        and placed.low >= 0.0
        and placed.high <= outer.depth
        and profiles.within(placed.face.outer, _region(outer.face))
    )


def _less(piece: FakePrism, tool: FakePrism) -> list[FakePrism] | None:
    """`piece` less `tool`, as layers along `piece`'s normal: as it was below the tool and above
    it, and between, gone if the tool covers the face, or holed by the tool's outline if that
    lies inside it. None when that isn't exact: another plane, or outlines that cross."""
    placed = _on(piece.frame, tool)
    if placed is None:
        return None
    low, high = max(placed.low, 0.0), min(placed.high, piece.depth)
    if low >= high:
        return [piece]  # they only touch
    middle: FakeFace | None
    if profiles.within(piece.face.outer, _region(placed.face)):
        middle = None
    elif not placed.face.holes and profiles.within(placed.face.outer, _region(piece.face)):
        middle = replace(piece.face, holes=(*piece.face.holes, placed.face.outer))
    else:
        return None
    if low == 0.0 and high == piece.depth:
        return [] if middle is None else [replace(piece, face=middle)]
    layers = ((0.0, low, piece.face), (low, high, middle), (high, piece.depth, piece.face))
    return [_layer(piece, start, end, face) for start, end, face in layers if face and end > start]


def _layer(piece: FakePrism, start: float, end: float, face: FakeFace) -> FakePrism:
    """`face` on `piece`'s frame moved `start` along the normal, `end - start` deep."""
    if start == 0.0:
        return FakePrism(face=face, frame=piece.frame, depth=end)
    o, n = piece.frame.origin, _normal(piece.frame)
    origin = Point3(x=o.x + start * n.x, y=o.y + start * n.y, z=o.z + start * n.z)
    return FakePrism(face=face, frame=replace(piece.frame, origin=origin), depth=end - start)


def _turned_over(face: FakeFace) -> FakeFace:
    """`face` seen from its other side: y reversed. Each arc's ends swap, so its loop runs the
    other way along it."""
    return FakeFace(outer=_mirrored(face.outer), holes=tuple(map(_mirrored, face.holes)))


def _mirrored(loop: Loop) -> Loop:
    flags = loop.reversed or (False,) * len(loop.edges)
    edges: list[Geometry] = []
    backwards: list[bool] = []
    for edge, back in zip(loop.edges, flags, strict=True):
        match edge:
            case Line(start=a, end=b):
                edges.append(replace(edge, start=_flip(a), end=_flip(b)))
                backwards.append(back)
            case Arc(center=c, start_angle=start, sweep_angle=sweep):
                edges.append(replace(edge, center=_flip(c), start_angle=(-start - sweep) % 360.0))
                backwards.append(not back)
            case Circle(center=c):
                edges.append(replace(edge, center=_flip(c)))
                backwards.append(back)
            case Rectangle(corner=c, height=h):
                edges.append(replace(edge, corner=Point2(x=c.x, y=-c.y - h)))
                backwards.append(back)
    return Loop(edges=tuple(edges), reversed=tuple(backwards) if any(backwards) else ())


def _flip(p: Point2) -> Point2:
    return Point2(x=p.x, y=-p.y)


def _dot(a: Point3, b: Point3) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def _region(face: FakeFace) -> profiles.Profile:
    return profiles.Profile(outer=face.outer, holes=face.holes)


def _ccw(points: list[Point2]) -> list[Point2]:
    total = sum(
        p.x * q.y - q.x * p.y for p, q in zip(points, [*points[1:], points[0]], strict=True)
    )
    return points if total > 0 else points[::-1]


if TYPE_CHECKING:
    _conforms: Kernel = FakeKernel()
