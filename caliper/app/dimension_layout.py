"""Where a distance dimension's line sits, from its stored inputs. Qt-free.

One rule for every orientation (proposed for the contract; see docs/workplan/shell.md):

- The measured direction is a→b for ALIGNED, +X for HORIZONTAL, and +Y for VERTICAL.
- Positive offset is that direction rotated 90° counter-clockwise, measured from the
  midpoint of a and b. So a horizontal dimension with a positive offset sits above, and a
  vertical one sits to the left.
- An ALIGNED dimension whose points coincide uses +X as its direction.

The engine owns the measured value (`queries.dimension_value`); this module only places the
drawing, the way `painter.py` draws a rectangle from its corner, width, and height.
"""

import math
from dataclasses import dataclass

from caliper.contracts.document import DistanceOrientation, Point2

Vector = tuple[float, float]


def direction(orientation: DistanceOrientation, a: Point2, b: Point2) -> Vector:
    match orientation:
        case DistanceOrientation.HORIZONTAL:
            return (1.0, 0.0)
        case DistanceOrientation.VERTICAL:
            return (0.0, 1.0)
        case DistanceOrientation.ALIGNED:
            length = math.hypot(b.x - a.x, b.y - a.y)
            return (1.0, 0.0) if length == 0 else ((b.x - a.x) / length, (b.y - a.y) / length)


def normal(d: Vector) -> Vector:
    """`d` rotated 90° counter-clockwise."""
    return (-d[1], d[0])


def midpoint(a: Point2, b: Point2) -> Point2:
    return Point2(x=(a.x + b.x) / 2, y=(a.y + b.y) / 2)


def offset_for(orientation: DistanceOrientation, a: Point2, b: Point2, placement: Point2) -> float:
    """The offset that puts the dimension line through `placement`."""
    nx, ny = normal(direction(orientation, a, b))
    mid = midpoint(a, b)
    return (placement.x - mid.x) * nx + (placement.y - mid.y) * ny


def choose_orientation(a: Point2, b: Point2, placement: Point2) -> DistanceOrientation:
    """Placing above or below the pair measures horizontally, beside it vertically, and
    diagonally out from both ends measures the true distance, as in SolidWorks and Fusion."""
    if a.x == b.x or a.y == b.y:
        return DistanceOrientation.ALIGNED
    within_x = min(a.x, b.x) < placement.x < max(a.x, b.x)
    within_y = min(a.y, b.y) < placement.y < max(a.y, b.y)
    if within_x and not within_y:
        return DistanceOrientation.HORIZONTAL
    if within_y and not within_x:
        return DistanceOrientation.VERTICAL
    return DistanceOrientation.ALIGNED


@dataclass(frozen=True, slots=True)
class Layout:
    start: Point2
    """Where the dimension line meets the extension line from `a`."""
    end: Point2
    """Where it meets the extension line from `b`."""
    along: Vector
    """Unit vector from `start` to `end` (the measured direction if they coincide)."""
    out_a: Vector
    """Unit vector from `a` toward `start`, for the extension line's overshoot."""
    out_b: Vector


def layout(orientation: DistanceOrientation, a: Point2, b: Point2, offset: float) -> Layout:
    d = direction(orientation, a, b)
    nx, ny = normal(d)
    mid = midpoint(a, b)

    def onto_line(p: Point2) -> Point2:
        shift = (mid.x - p.x) * nx + (mid.y - p.y) * ny + offset
        return Point2(x=p.x + nx * shift, y=p.y + ny * shift)

    start, end = onto_line(a), onto_line(b)
    length = math.hypot(end.x - start.x, end.y - start.y)
    along = d if length == 0 else ((end.x - start.x) / length, (end.y - start.y) / length)
    return Layout(start, end, along, _unit(a, start, (nx, ny)), _unit(b, end, (nx, ny)))


def _unit(p: Point2, q: Point2, fallback: Vector) -> Vector:
    length = math.hypot(q.x - p.x, q.y - p.y)
    return fallback if length == 0 else ((q.x - p.x) / length, (q.y - p.y) / length)
