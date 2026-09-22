"""Where a dimension's lines sit, from its stored inputs. Qt-free.

One placement rule for every orientation (the shell's since PR #5):

- The measured direction is a→b for ALIGNED, +X for HORIZONTAL, and +Y for VERTICAL.
- Positive offset is that direction rotated 90° counter-clockwise, measured from the
  midpoint of a and b. So a horizontal dimension with a positive offset sits above, and a
  vertical one sits to the left.
- An ALIGNED dimension whose points coincide uses +X as its direction.

**This differs from the frozen contract for HORIZONTAL and VERTICAL.** The contract's
docstring on `DistanceDimension.offset` (PR #22) says the offset is measured perpendicular
to a→b whatever the orientation, and the engine's `CreateDimension` computes it that way.
That can't be inverted: every point along a horizontal dimension line is a different
distance from a slanted a→b, so the placement is lost. Both rules agree for ALIGNED, and
for HORIZONTAL (VERTICAL) whenever a→b runs left to right (upward). The shell keeps the
rule every V1 file was written with; `engine_placement` bridges the difference until the
contract changes (docs/workplan/shell.md, P7).

A dimension may refer to a straight curve instead of a point. It then attaches at the foot
of the perpendicular from the other end (or, between two curves, from the other curve's
midpoint), which is where the engine measures from. The engine owns every measured value
(`queries.dimension_value`); this module only places the drawing, the way `painter.py`
draws a rectangle from its corner, width, and height.
"""

import math
from dataclasses import dataclass

from caliper.contracts.document import DistanceOrientation, Point2

Vector = tuple[float, float]

_DEGENERATE = 1e-9
"""Below this, a direction component counts as zero."""


@dataclass(frozen=True, slots=True)
class Segment:
    """A straight curve a dimension refers to: a line, or a rectangle side."""

    start: Point2
    end: Point2

    @property
    def mid(self) -> Point2:
        return midpoint(self.start, self.end)


Anchor = Point2 | Segment
"""What a dimension reference resolves to on screen."""


def direction(orientation: DistanceOrientation, a: Point2, b: Point2) -> Vector:
    match orientation:
        case DistanceOrientation.HORIZONTAL:
            return (1.0, 0.0)
        case DistanceOrientation.VERTICAL:
            return (0.0, 1.0)
        case DistanceOrientation.ALIGNED:
            return _unit(a, b, (1.0, 0.0))


def normal(d: Vector) -> Vector:
    """`d` rotated 90° counter-clockwise."""
    return (-d[1], d[0])


def midpoint(a: Point2, b: Point2) -> Point2:
    return Point2(x=(a.x + b.x) / 2, y=(a.y + b.y) / 2)


def foot(segment: Segment, p: Point2) -> Point2:
    """The point on the segment's infinite line nearest `p`."""
    a, b = segment.start, segment.end
    dx, dy = b.x - a.x, b.y - a.y
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return a
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2
    return Point2(x=a.x + t * dx, y=a.y + t * dy)


def anchors(a: Anchor, b: Anchor) -> tuple[Point2, Point2]:
    """The two points a distance dimension's lines start from."""
    return _attach(a, b), _attach(b, a)


def _attach(this: Anchor, other: Anchor) -> Point2:
    if isinstance(this, Point2):
        return this
    target = other if isinstance(other, Point2) else other.mid
    return foot(this, target)


def offset_for(orientation: DistanceOrientation, a: Point2, b: Point2, placement: Point2) -> float:
    """The offset that puts the dimension line through `placement`."""
    nx, ny = normal(direction(orientation, a, b))
    mid = midpoint(a, b)
    return (placement.x - mid.x) * nx + (placement.y - mid.y) * ny


def engine_placement(
    orientation: DistanceOrientation, a: Point2, b: Point2, placement: Point2
) -> Point2:
    """Where to tell `CreateDimension` the label is, so the engine stores this module's offset.

    The engine measures the offset perpendicular to a→b (see the module docstring), so for a
    horizontal or vertical dimension the real placement would be stored as the wrong offset.
    The point `offset_for(...)` away from the midpoint along a→b's left normal gives the
    engine exactly that offset. Delete this when the contract adopts this module's rule: a
    test against the real engine (`test_dimension_layout.py`) fails on that day.
    """
    if orientation is DistanceOrientation.ALIGNED:
        return placement
    offset = offset_for(orientation, a, b, placement)
    ux, uy = direction(DistanceOrientation.ALIGNED, a, b)
    mid = midpoint(a, b)
    return Point2(x=mid.x - uy * offset, y=mid.y + ux * offset)


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


# --- Angles -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AngleLayout:
    center: Point2
    """Where the two lines, extended, cross."""
    radius: float
    start_angle: float
    """Degrees, counter-clockwise from +X, where the dimension arc starts."""
    sweep: float
    """Degrees, counter-clockwise, always in (0, 180)."""
    label: Point2
    """The middle of the arc."""


def angle_layout(a: Segment, b: Segment, offset: float, supplementary: bool) -> AngleLayout | None:
    """The arc of an angle dimension, in the sector the engine chose. None for parallel lines.

    The engine measures between the two lines' directions (start to end). A positive offset
    puts the arc between those directions; a negative one in the opposite sector of the
    same angle. `supplementary` swaps to the other pair of sectors: between a's direction
    and b's reversed one when the offset is positive, and the other way round when not.
    """
    da, db = _unit(a.start, a.end, (0.0, 0.0)), _unit(b.start, b.end, (0.0, 0.0))
    det = da[0] * db[1] - da[1] * db[0]
    if abs(det) < _DEGENERATE:
        return None
    # Where the infinite lines cross: a.start + t * da.
    t = ((b.start.x - a.start.x) * db[1] - (b.start.y - a.start.y) * db[0]) / det
    center = Point2(x=a.start.x + t * da[0], y=a.start.y + t * da[1])
    sa = 1.0 if offset >= 0 else -1.0
    sb = -sa if supplementary else sa
    first = math.degrees(math.atan2(sa * da[1], sa * da[0])) % 360.0
    second = math.degrees(math.atan2(sb * db[1], sb * db[0])) % 360.0
    sweep = (second - first) % 360.0
    if sweep > 180.0:
        first, sweep = second, 360.0 - sweep
    radius = abs(offset)
    middle = math.radians(first + sweep / 2)
    label = Point2(x=center.x + radius * math.cos(middle), y=center.y + radius * math.sin(middle))
    return AngleLayout(center, radius, first, sweep, label)


def _unit(p: Point2, q: Point2, fallback: Vector) -> Vector:
    length = math.hypot(q.x - p.x, q.y - p.y)
    return fallback if length == 0 else ((q.x - p.x) / length, (q.y - p.y) / length)
