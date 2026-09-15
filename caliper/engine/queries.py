"""Queries over one Document snapshot (contracts/queries.py).

Phase 0.5 slice: `bounding_box` and `entity_at_point` for every geometry type, which is
what the milestone shell needs. The other methods raise NotImplementedError until they
land in V1 (docs/workplan/core.md).

2D geometry is analytic; the kernel is only for B-rep work.
"""

import math
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from caliper.contracts.document import (
    Arc,
    Circle,
    Document,
    EntityId,
    Geometry,
    Line,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import (
    AreaProperties,
    BoundingBox,
    CheckResult,
    Distance,
    Expectation,
)

if TYPE_CHECKING:
    from caliper.contracts.queries import Queries

_GEOMETRY = (Line, Circle, Arc, Rectangle)


class DocumentQueries:
    def __init__(self, document: Document) -> None:
        self._document = document

    def bounding_box(self, ids: Sequence[EntityId] = ()) -> BoundingBox | Error:
        if ids:
            selected: list[Geometry] = []
            for id in ids:
                entity = self._document.entities.get(id)
                if entity is None:
                    return Error(
                        code=ErrorCode.ENTITY_NOT_FOUND, message=f"no entity {id!r}", field="ids"
                    )
                if not isinstance(entity, _GEOMETRY):
                    return Error(
                        code=ErrorCode.ENTITY_WRONG_KIND,
                        message=f"{id!r} is a {entity.kind}; bounding boxes cover geometry",
                        field="ids",
                    )
                selected.append(entity)
        else:
            selected = [e for e in self._document.entities.values() if isinstance(e, _GEOMETRY)]
            if not selected:
                return Error(code=ErrorCode.SELECTION_EMPTY, message="the document has no geometry")
        boxes = [_bounds(entity) for entity in selected]
        return BoundingBox(
            x_min=min(b.x_min for b in boxes),
            y_min=min(b.y_min for b in boxes),
            x_max=max(b.x_max for b in boxes),
            y_max=max(b.y_max for b in boxes),
        )

    def entity_at_point(self, point: Point2, tolerance: float) -> EntityId | None:
        """Distance is measured to an entity's outline, so a rectangle's interior is a miss.

        The contract has no Error return here, so invalid input (a non-finite point, or a
        negative or non-finite tolerance) matches nothing.
        """
        if not (math.isfinite(point.x) and math.isfinite(point.y)):
            return None
        if not (math.isfinite(tolerance) and tolerance >= 0):
            return None
        hits = (
            (_distance(entity, point), id)
            for id, entity in self._document.entities.items()
            if isinstance(entity, _GEOMETRY)
        )
        nearest = min((hit for hit in hits if hit[0] <= tolerance), default=None)
        return None if nearest is None else nearest[1]

    # --- Not in the Phase 0.5 slice -----------------------------------------------------

    def feature_point(self, ref: Ref) -> Point2 | Error:
        raise NotImplementedError("feature_point lands in V1 (docs/workplan/core.md)")

    def measure_distance(self, a: Ref, b: Ref) -> Distance | Error:
        raise NotImplementedError("measure_distance lands in V1 (docs/workplan/core.md)")

    def entities_in_box(self, box: BoundingBox, *, crossing: bool) -> tuple[EntityId, ...]:
        raise NotImplementedError("entities_in_box lands in V1 (docs/workplan/core.md)")

    def nearest_feature(self, point: Point2, tolerance: float) -> Ref | None:
        raise NotImplementedError("nearest_feature lands in V1 (docs/workplan/core.md)")

    def dimension_value(self, id: EntityId) -> float | Error:
        raise NotImplementedError("dimension_value lands in V1 (docs/workplan/core.md)")

    def area_properties(self, ids: Sequence[EntityId]) -> AreaProperties | Error:
        raise NotImplementedError("area_properties lands in V1 (docs/workplan/core.md)")

    def check(self, expectation: Expectation) -> CheckResult:
        raise NotImplementedError("check lands in V1 (docs/workplan/core.md)")


# --- Geometry ---------------------------------------------------------------------------


def _bounds(entity: Geometry) -> BoundingBox:
    match entity:
        case Line(start=a, end=b):
            return _box((a.x, b.x), (a.y, b.y))
        case Circle(center=c, radius=r):
            return BoundingBox(x_min=c.x - r, y_min=c.y - r, x_max=c.x + r, y_max=c.y + r)
        case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
            # Extremes are the endpoints plus any axis crossing inside the sweep.
            angles = [start, start + sweep]
            angles += [a for a in (0.0, 90.0, 180.0, 270.0) if (a - start) % 360.0 <= sweep]
            units = [_unit(a) for a in angles]
            return _box((c.x + r * u for u, _ in units), (c.y + r * v for _, v in units))
        case Rectangle(corner=c, width=w, height=h):
            return BoundingBox(x_min=c.x, y_min=c.y, x_max=c.x + w, y_max=c.y + h)


def _distance(entity: Geometry, p: Point2) -> float:
    """Shortest distance from `p` to the entity's outline."""
    match entity:
        case Line(start=a, end=b):
            return _segment_distance(p, a, b)
        case Circle(center=c, radius=r):
            return abs(math.hypot(p.x - c.x, p.y - c.y) - r)
        case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
            angle = math.degrees(math.atan2(p.y - c.y, p.x - c.x))
            if (angle - start) % 360.0 <= sweep:
                return abs(math.hypot(p.x - c.x, p.y - c.y) - r)
            ends = (_unit(start), _unit(start + sweep))
            return min(math.hypot(p.x - (c.x + r * u), p.y - (c.y + r * v)) for u, v in ends)
        case Rectangle(corner=c, width=w, height=h):
            dx = max(c.x - p.x, p.x - (c.x + w))
            dy = max(c.y - p.y, p.y - (c.y + h))
            if dx <= 0 and dy <= 0:  # inside: nearest edge
                return -max(dx, dy)
            return math.hypot(max(dx, 0.0), max(dy, 0.0))


def _segment_distance(p: Point2, a: Point2, b: Point2) -> float:
    ex, ey = b.x - a.x, b.y - a.y
    length_squared = ex * ex + ey * ey
    t = 0.0 if length_squared == 0 else ((p.x - a.x) * ex + (p.y - a.y) * ey) / length_squared
    t = min(1.0, max(0.0, t))
    return math.hypot(p.x - (a.x + t * ex), p.y - (a.y + t * ey))


def _unit(degrees: float) -> tuple[float, float]:
    """(cos, sin), exact at multiples of 90° so axis-aligned bounds come out exact."""
    quarter = degrees / 90.0
    if quarter.is_integer():
        return ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))[int(quarter) % 4]
    radians = math.radians(degrees)
    return math.cos(radians), math.sin(radians)


def _box(xs: Iterable[float], ys: Iterable[float]) -> BoundingBox:
    xs, ys = list(xs), list(ys)
    return BoundingBox(x_min=min(xs), y_min=min(ys), x_max=max(xs), y_max=max(ys))


if TYPE_CHECKING:
    _conforms: Queries = DocumentQueries(Document.empty())
