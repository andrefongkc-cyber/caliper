"""Queries over one Document snapshot (contracts/queries.py).

2D geometry is analytic. The kernel is only for B-rep work: turning a closed profile into
a face for `area_properties`.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from caliper.contracts.document import (
    Arc,
    Circle,
    DistanceDimension,
    DistanceOrientation,
    Document,
    Entity,
    EntityId,
    Feature,
    Geometry,
    Line,
    Point2,
    RadialDimension,
    RadialMeasure,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.kernel import Kernel, KernelError
from caliper.contracts.queries import (
    AreaProperties,
    BoundingBox,
    CheckResult,
    Distance,
    Expectation,
    Metric,
)
from caliper.engine.commands.validation import (
    feature_errors,
    normalize_enum,
    normalize_float,
    normalize_id,
    normalize_ref,
)

if TYPE_CHECKING:
    from caliper.contracts.queries import Queries

_GEOMETRY = (Line, Circle, Arc, Rectangle)


class DocumentQueries:
    def __init__(self, document: Document, kernel: Kernel | None = None) -> None:
        """Without a kernel, `area_properties` reports KERNEL_UNAVAILABLE."""
        self._document = document
        self._kernel = kernel

    def bounding_box(self, ids: Sequence[EntityId] = ()) -> BoundingBox | Error:
        if ids:
            found = self._geometry(ids, "bounding boxes cover geometry")
            if isinstance(found, Error):
                return found
            selected = found
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

    def feature_point(self, ref: Ref) -> Point2 | Error:
        return self._point(ref, "ref")

    def nearest_feature(self, point: Point2, tolerance: float) -> Ref | None:
        """Distance is measured to feature points, not outlines. Ties go to the lower entity id.

        Like `entity_at_point`, invalid input matches nothing.
        """
        if not (math.isfinite(point.x) and math.isfinite(point.y)):
            return None
        if not (math.isfinite(tolerance) and tolerance >= 0):
            return None
        candidates = (
            (math.hypot(at.x - point.x, at.y - point.y), id, feature)
            for id, entity in self._document.entities.items()
            for feature, at in _features(entity).items()
        )
        nearest = min((c for c in candidates if c[0] <= tolerance), default=None)
        return None if nearest is None else Ref(entity=nearest[1], feature=nearest[2])

    def measure_distance(self, a: Ref, b: Ref) -> Distance | Error:
        return self._distance(a, b, fields=("a", "b"))

    def entities_in_box(self, box: BoundingBox, *, crossing: bool) -> tuple[EntityId, ...]:
        """Window selection, or crossing selection when `crossing`, measured on outlines.

        A crossing box touching no part of an outline misses, even inside a rectangle. An
        invalid box (not finite, or min above max) matches nothing.
        """
        if not _valid_box(box):
            return ()
        return tuple(
            sorted(
                id
                for id, entity in self._document.entities.items()
                if isinstance(entity, _GEOMETRY)
                and (_touches(entity, box) if crossing else _contains(box, _bounds(entity)))
            )
        )

    def dimension_value(self, id: EntityId) -> float | Error:
        errors: list[Error] = []
        id = normalize_id(id, "id", errors)
        if errors:
            return errors[0]
        match self._document.entities.get(id):
            case None:
                return Error(
                    code=ErrorCode.ENTITY_NOT_FOUND, message=f"no entity {id!r}", field="id"
                )
            case DistanceDimension(a=a, b=b, orientation=orientation):
                distance = self._distance(a, b, fields=("a", "b"))
                if isinstance(distance, Error):
                    return distance
                match orientation:
                    case DistanceOrientation.ALIGNED:
                        return distance.value
                    case DistanceOrientation.HORIZONTAL:
                        return abs(distance.dx)
                    case DistanceOrientation.VERTICAL:
                        return abs(distance.dy)
            case RadialDimension(target=target, measure=measure):
                curve = self._document.entities.get(target)
                if not isinstance(curve, Circle | Arc):
                    return Error(
                        code=ErrorCode.ENTITY_WRONG_KIND,
                        message=f"{target!r} is not a circle or arc",
                        field="target",
                    )
                return 2 * curve.radius if measure is RadialMeasure.DIAMETER else curve.radius
            case entity:
                return Error(
                    code=ErrorCode.ENTITY_WRONG_KIND,
                    message=f"{id!r} is a {entity.kind}; only dimensions have a value",
                    field="id",
                )

    def area_properties(self, ids: Sequence[EntityId]) -> AreaProperties | Error:
        if not ids:
            return Error(
                code=ErrorCode.SELECTION_EMPTY, message="select one closed profile", field="ids"
            )
        found = self._geometry(ids, "area needs a closed profile")
        if isinstance(found, Error):
            return found
        if self._kernel is None:
            return Error(
                code=ErrorCode.KERNEL_UNAVAILABLE,
                message="area properties need a geometry kernel, and none is configured",
            )
        try:
            return self._kernel.area_properties(self._kernel.make_face(found))
        except KernelError as e:
            return Error(code=e.code, message=str(e), field="ids")

    def check(self, expectation: Expectation) -> CheckResult:
        actual = self._evaluate(expectation)
        if isinstance(actual, Error):
            return CheckResult(expectation=expectation, passed=False, actual=None, error=actual)
        passed = abs(actual - expectation.expected) <= expectation.tolerance
        return CheckResult(expectation=expectation, passed=passed, actual=actual)

    # --- Helpers ------------------------------------------------------------------------

    def _point(self, ref: Ref, field: str) -> Point2 | Error:
        errors: list[Error] = []
        ref = normalize_ref(ref, field, errors)
        errors = errors or feature_errors(ref, field, self._document)
        if errors:
            return errors[0]
        return _features(self._document.entities[ref.entity])[ref.feature]

    def _distance(self, a: Ref, b: Ref, *, fields: tuple[str, str]) -> Distance | Error:
        start = self._point(a, fields[0])
        if isinstance(start, Error):
            return start
        end = self._point(b, fields[1])
        if isinstance(end, Error):
            return end
        dx, dy = end.x - start.x, end.y - start.y
        return Distance(value=math.hypot(dx, dy), dx=dx, dy=dy)

    def _geometry(self, ids: Sequence[EntityId], purpose: str) -> list[Geometry] | Error:
        selected: list[Geometry] = []
        for id in ids:
            errors: list[Error] = []
            id = normalize_id(id, "ids", errors)
            if errors:
                return errors[0]
            entity = self._document.entities.get(id)
            if entity is None:
                return Error(
                    code=ErrorCode.ENTITY_NOT_FOUND, message=f"no entity {id!r}", field="ids"
                )
            if not isinstance(entity, _GEOMETRY):
                return Error(
                    code=ErrorCode.ENTITY_WRONG_KIND,
                    message=f"{id!r} is a {entity.kind}; {purpose}",
                    field="ids",
                )
            selected.append(entity)
        return selected

    def _evaluate(self, expectation: Expectation) -> float | Error:
        errors: list[Error] = []
        metric = normalize_enum(Metric, expectation.metric, "metric", errors)
        normalize_float(expectation.expected, "expected", errors)
        tolerance = normalize_float(expectation.tolerance, "tolerance", errors)
        if errors:
            return errors[0]
        if tolerance < 0:
            return Error(
                code=ErrorCode.VALUE_OUT_OF_RANGE,
                message="tolerance must be 0 or more",
                field="tolerance",
            )
        match metric:
            case Metric.DISTANCE | Metric.DISTANCE_X | Metric.DISTANCE_Y:
                if len(expectation.refs) != 2:
                    return Error(
                        code=ErrorCode.VALUE_OUT_OF_RANGE,
                        message=f"{metric} needs exactly two refs",
                        field="refs",
                    )
                a, b = expectation.refs
                distance = self._distance(a, b, fields=("refs[0]", "refs[1]"))
                if isinstance(distance, Error):
                    return distance
                if metric is Metric.DISTANCE_X:
                    return abs(distance.dx)
                if metric is Metric.DISTANCE_Y:
                    return abs(distance.dy)
                return distance.value
            case Metric.BBOX_WIDTH | Metric.BBOX_HEIGHT:
                box = self.bounding_box(expectation.ids)
                if isinstance(box, Error):
                    return box
                return box.width if metric is Metric.BBOX_WIDTH else box.height
            case Metric.AREA:
                properties = self.area_properties(expectation.ids)
                return properties if isinstance(properties, Error) else properties.area
            case Metric.DIMENSION_VALUE:
                if len(expectation.ids) != 1:
                    return Error(
                        code=ErrorCode.VALUE_OUT_OF_RANGE,
                        message="dimension_value needs exactly one id",
                        field="ids",
                    )
                value = self.dimension_value(expectation.ids[0])
                if isinstance(value, Error) and value.field == "id":
                    return Error(code=value.code, message=value.message, field="ids")
                return value


# --- Geometry ---------------------------------------------------------------------------


def _features(entity: Entity) -> Mapping[Feature, Point2]:
    """Every point feature of an entity, keyed as in POINT_FEATURES. Annotations have none."""
    match entity:
        case Line(start=a, end=b):
            mid = Point2(x=(a.x + b.x) / 2, y=(a.y + b.y) / 2)
            return {Feature.START: a, Feature.END: b, Feature.MID: mid}
        case Circle(center=c):
            return {Feature.CENTER: c}
        case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):

            def on_arc(degrees: float) -> Point2:
                u, v = _unit(degrees)
                return Point2(x=c.x + r * u, y=c.y + r * v)

            return {
                Feature.CENTER: c,
                Feature.START: on_arc(start),
                Feature.END: on_arc(start + sweep),
                Feature.MID: on_arc(start + sweep / 2),
            }
        case Rectangle(corner=c, width=w, height=h):
            return {
                Feature.BOTTOM_LEFT: c,
                Feature.BOTTOM_RIGHT: Point2(x=c.x + w, y=c.y),
                Feature.TOP_RIGHT: Point2(x=c.x + w, y=c.y + h),
                Feature.TOP_LEFT: Point2(x=c.x, y=c.y + h),
                Feature.CENTER: Point2(x=c.x + w / 2, y=c.y + h / 2),
            }
        case _:
            return {}


def _valid_box(box: object) -> bool:
    if not isinstance(box, BoundingBox):
        return False
    edges = (box.x_min, box.y_min, box.x_max, box.y_max)
    return (
        all(math.isfinite(e) for e in edges) and box.x_min <= box.x_max and box.y_min <= box.y_max
    )


def _contains(outer: BoundingBox, inner: BoundingBox) -> bool:
    return (
        outer.x_min <= inner.x_min
        and outer.y_min <= inner.y_min
        and inner.x_max <= outer.x_max
        and inner.y_max <= outer.y_max
    )


def _inside(p: Point2, box: BoundingBox) -> bool:
    return box.x_min <= p.x <= box.x_max and box.y_min <= p.y <= box.y_max


def _touches(entity: Geometry, box: BoundingBox) -> bool:
    """Whether any point of the entity's outline lies in the closed box."""
    match entity:
        case Line(start=a, end=b):
            return _segment_touches(a, b, box)
        case Circle(center=c, radius=r):
            near = math.hypot(
                max(box.x_min - c.x, 0.0, c.x - box.x_max),
                max(box.y_min - c.y, 0.0, c.y - box.y_max),
            )
            far = math.hypot(
                max(abs(c.x - box.x_min), abs(c.x - box.x_max)),
                max(abs(c.y - box.y_min), abs(c.y - box.y_max)),
            )
            return near <= r <= far
        case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
            ends = _features(entity)
            if _inside(ends[Feature.START], box) or _inside(ends[Feature.END], box):
                return True
            # Both ends are outside, so the arc reaches the box only by crossing its boundary.
            return any(
                (math.degrees(math.atan2(p.y - c.y, p.x - c.x)) - start) % 360.0 <= sweep
                for p in _circle_meets_box_edges(c, r, box)
            )
        case Rectangle(corner=c, width=w, height=h):
            overlaps = (
                c.x <= box.x_max
                and box.x_min <= c.x + w
                and c.y <= box.y_max
                and box.y_min <= c.y + h
            )
            strictly_within = (
                c.x < box.x_min and box.x_max < c.x + w and c.y < box.y_min and box.y_max < c.y + h
            )
            return overlaps and not strictly_within


def _segment_touches(a: Point2, b: Point2, box: BoundingBox) -> bool:
    """Liang-Barsky clipping: does the segment keep any part inside the closed box?"""
    low, high = 0.0, 1.0
    dx, dy = b.x - a.x, b.y - a.y
    for p, q in (
        (-dx, a.x - box.x_min),
        (dx, box.x_max - a.x),
        (-dy, a.y - box.y_min),
        (dy, box.y_max - a.y),
    ):
        if p == 0:
            if q < 0:
                return False
        elif p < 0:
            low = max(low, q / p)
        else:
            high = min(high, q / p)
        if low > high:
            return False
    return True


def _circle_meets_box_edges(c: Point2, r: float, box: BoundingBox) -> Iterable[Point2]:
    for x in (box.x_min, box.x_max):
        if (reach := r * r - (x - c.x) ** 2) >= 0:
            for y in (c.y - math.sqrt(reach), c.y + math.sqrt(reach)):
                if box.y_min <= y <= box.y_max:
                    yield Point2(x=x, y=y)
    for y in (box.y_min, box.y_max):
        if (reach := r * r - (y - c.y) ** 2) >= 0:
            for x in (c.x - math.sqrt(reach), c.x + math.sqrt(reach)):
                if box.x_min <= x <= box.x_max:
                    yield Point2(x=x, y=y)


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
