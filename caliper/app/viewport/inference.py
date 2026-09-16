"""Alignment guides: snap to the x or y of feature points the user has recently hovered.

Queries find the feature near the pointer, not every feature on screen, so the canvas
"acquires" a point when the pointer passes over it (as Fusion and SketchUp do). Moving in
line with an acquired point then shows a dashed guide and snaps to its x or y. Comparing
coordinates like this is a UI convenience, like grid snapping, not geometry.
"""

from collections.abc import Sequence

from caliper.contracts.document import Point2

MAX_ACQUIRED = 4

Guide = tuple[Point2, Point2]


def acquire(points: Sequence[Point2], point: Point2) -> list[Point2]:
    """Most recent first, without duplicates, at most MAX_ACQUIRED."""
    return [point, *(p for p in points if p != point)][:MAX_ACQUIRED]


def align(
    raw: Point2, base: Point2, acquired: Sequence[Point2], tolerance: float
) -> tuple[Point2, tuple[Guide, ...]]:
    """Snap `base` to the nearest acquired x and y within `tolerance` of the raw pointer.

    `base` is the point before alignment (grid-snapped or raw); an axis with no acquired
    point in reach keeps its value from `base`. Returns the point and a guide per snapped
    axis, from the acquired point to the result.
    """

    def nearest(axis: str) -> Point2 | None:
        close = [p for p in acquired if abs(getattr(p, axis) - getattr(raw, axis)) <= tolerance]
        return min(close, key=lambda p: abs(getattr(p, axis) - getattr(raw, axis)), default=None)

    by_x, by_y = nearest("x"), nearest("y")
    point = Point2(
        x=by_x.x if by_x is not None else base.x,
        y=by_y.y if by_y is not None else base.y,
    )
    guides = tuple(
        (source, point) for source in (by_x, by_y) if source is not None and source != point
    )
    return point, guides
