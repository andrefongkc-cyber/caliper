"""Triangles covering a polygon with holes, for drawing the analytic kernel's solids
(`FakeKernel.mesh`). Never for measuring: areas and volumes are worked out exactly elsewhere.

Slabs: the region is cut into horizontal strips at every corner's height. No corner lies
inside a strip, and the loops don't cross (the engine checks a profile before a kernel sees
it), so within a strip the edges keep their left-to-right order. Taken in pairs, inside then
outside, they bound trapezoids that tile the strip's part of the region exactly, holes and
all; each trapezoid is two triangles. Long thin triangles are fine for drawing flat faces.
"""

from collections.abc import Sequence
from itertools import pairwise

from caliper.contracts.document import Point2

type Triangle = tuple[Point2, Point2, Point2]
type Segment = tuple[Point2, Point2]


def triangulate(outer: Sequence[Point2], holes: Sequence[Sequence[Point2]] = ()) -> list[Triangle]:
    """Counter-clockwise triangles covering the region inside `outer` and outside each hole.

    Loops may run either way round. They must not cross or touch, and no hole may be inside
    another.
    """
    loops = [list(outer), *(list(hole) for hole in holes)]
    edges: list[Segment] = [
        (p, q)
        for loop in loops
        for p, q in zip(loop, [*loop[1:], loop[0]], strict=True)
        if p.y != q.y  # level edges bound no strip
    ]
    heights = sorted({p.y for loop in loops for p in loop})
    triangles: list[Triangle] = []
    for low, high in pairwise(heights):
        middle = (low + high) / 2
        if not low < middle < high:
            continue  # heights a rounding apart: a strip with no height covers nothing
        crossing = sorted(
            (e for e in edges if min(e[0].y, e[1].y) < middle < max(e[0].y, e[1].y)),
            key=lambda e: _x(e, middle),
        )
        # Even-odd: from the left, the region starts at the first edge and ends at the next.
        for left, right in zip(crossing[::2], crossing[1::2], strict=True):
            a0, a1 = Point2(x=_x(left, low), y=low), Point2(x=_x(left, high), y=high)
            b0, b1 = Point2(x=_x(right, low), y=low), Point2(x=_x(right, high), y=high)
            for triangle in ((a0, b0, b1), (a0, b1, a1)):
                if area(triangle) > 0:  # one is nothing where the strip narrows to a point
                    triangles.append(triangle)
    return triangles


def area(points: Sequence[Point2]) -> float:
    """Signed: positive when the points run counter-clockwise."""
    total = 0.0
    for p, q in zip(points, [*points[1:], points[0]], strict=True):
        total += p.x * q.y - q.x * p.y
    return total / 2


def _x(edge: Segment, y: float) -> float:
    """Where the edge is at height `y`: exactly its corner's x at either end."""
    p, q = edge
    if y == p.y:
        return p.x
    if y == q.y:
        return q.x
    return p.x + (y - p.y) * (q.x - p.x) / (q.y - p.y)
