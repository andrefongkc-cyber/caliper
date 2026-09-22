"""The dimension system: one place that names, measures, and infers all seven kinds.

A dimension is stored as one of three entity types (distance, radial, angle), but everything
a caller sees goes through here: which of the seven kinds it is (`classify`), what it
measures (`measured`), which kinds a selection allows (`options`), and which one a selection
plus a placement means (`infer`), the way a CAD sketcher picks length, horizontal, vertical,
radius, diameter, or angle from what you clicked and where you put the label.
"""

import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from caliper.contracts.document import (
    AngleDimension,
    Arc,
    Circle,
    DistanceDimension,
    DistanceOrientation,
    Document,
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
from caliper.contracts.queries import DimensionType
from caliper.engine.constraints.model import PARAMS, Frame, Straight, read
from caliper.engine.constraints.relations import (
    RefKind,
    Setting,
    measure,
    measure_angle,
    ref_kind,
)

type Dimension = DistanceDimension | RadialDimension | AngleDimension

_ROUND = (RefKind.CIRCLE, RefKind.ARC)
_SIDE_ENDS: Mapping[Feature, tuple[Feature, Feature]] = {
    Feature.BOTTOM: (Feature.BOTTOM_LEFT, Feature.BOTTOM_RIGHT),
    Feature.TOP: (Feature.TOP_LEFT, Feature.TOP_RIGHT),
    Feature.LEFT: (Feature.BOTTOM_LEFT, Feature.TOP_LEFT),
    Feature.RIGHT: (Feature.BOTTOM_RIGHT, Feature.TOP_RIGHT),
}
_ADJACENT_CORNERS = frozenset(frozenset(ends) for ends in _SIDE_ENDS.values())


def frame(document: Document, ids: Sequence[EntityId]) -> Frame:
    """The given geometry as constants, for measuring."""
    kinds: dict[EntityId, type[Geometry]] = {}
    slots: dict[EntityId, list[int]] = {}
    values: list[float] = []
    for id in dict.fromkeys(ids):
        entity = document.entities[id]
        kind: type[Geometry] = type(entity)  # type: ignore[assignment]
        kinds[id] = kind
        slots[id] = []
        for path in PARAMS[kind]:
            slots[id].append(len(values))
            values.append(read(entity, path))  # type: ignore[arg-type]
    return Frame(kinds, slots, values)


def measured(document: Document, dimension: Dimension) -> float:
    """What the dimension measures on the stored geometry (mm, or degrees for angles)."""
    if isinstance(dimension, RadialDimension):
        ids = [dimension.target]
    else:
        ids = [dimension.a.entity, dimension.b.entity]
    f = frame(document, ids)
    at = Setting(first=f, anchor=f)
    if isinstance(dimension, AngleDimension):
        value = measure_angle(f, dimension.a, dimension.b, dimension.supplementary, at).v
    else:
        value = measure(f, document, dimension, at).v
    return value + 0.0  # never -0.0


def classify(document: Document, dimension: Dimension) -> DimensionType:
    match dimension:
        case RadialDimension(measure=RadialMeasure.DIAMETER):
            return DimensionType.DIAMETER
        case RadialDimension():
            return DimensionType.RADIUS
        case AngleDimension():
            return DimensionType.ANGLE
        case DistanceDimension(orientation=DistanceOrientation.HORIZONTAL):
            return DimensionType.HORIZONTAL_DISTANCE
        case DistanceDimension(orientation=DistanceOrientation.VERTICAL):
            return DimensionType.VERTICAL_DISTANCE
        case DistanceDimension(a=a, b=b) if a.entity == b.entity:
            entity = document.entities[a.entity]
            ends = frozenset({a.feature, b.feature})
            if isinstance(entity, Line) and ends == {Feature.START, Feature.END}:
                return DimensionType.LENGTH
            if isinstance(entity, Rectangle) and ends in _ADJACENT_CORNERS:
                return DimensionType.LENGTH
    return DimensionType.DISTANCE


def options(document: Document, refs: Sequence[Ref]) -> Mapping[DimensionType, Error | None]:
    """For each kind, None if `refs` can take it, else why not. References must be valid."""
    allowed = _allowed(document, tuple(refs))
    reason = _describe(document, refs)
    return MappingProxyType(
        {
            kind: None
            if kind in allowed
            else Error(
                code=ErrorCode.CONSTRAINT_NOT_APPLICABLE,
                message=f"a {kind.value.replace('_', ' ')} dimension doesn't fit {reason}",
                field="refs",
            )
            for kind in DimensionType
        }
    )


def infer(
    document: Document,
    refs: Sequence[Ref],
    placement: Point2,
    kind: DimensionType | None = None,
) -> tuple[DimensionType, Dimension] | Error:
    """The kind of dimension `refs` placed at `placement` means, and the entity to store.

    The entity is driven (no value); the caller sets one to make it driving. References must
    be valid. `kind` asks for a particular kind instead of inferring one.
    """
    refs = tuple(refs)
    allowed = _allowed(document, refs)
    if not allowed:
        return Error(
            code=ErrorCode.CONSTRAINT_NOT_APPLICABLE,
            message=f"nothing to dimension in {_describe(document, refs)}; select a line, two "
            "points, a point and a line, two lines, or a circle or arc",
            field="refs",
        )
    if kind is None:
        kind = _preferred(document, refs, placement, allowed)
    elif kind not in allowed:
        choices = ", ".join(k.value for k in DimensionType if k in allowed)
        return Error(
            code=ErrorCode.CONSTRAINT_NOT_APPLICABLE,
            message=f"a {kind.value.replace('_', ' ')} dimension doesn't fit "
            f"{_describe(document, refs)}; it can take: {choices}",
            field="type",
        )
    return kind, _entity(document, refs, placement, kind)


# --- Which kinds a selection allows -------------------------------------------------------


def _allowed(document: Document, refs: tuple[Ref, ...]) -> frozenset[DimensionType]:
    kinds = tuple(ref_kind(document, r) for r in refs)
    distances = frozenset(
        {
            DimensionType.DISTANCE,
            DimensionType.HORIZONTAL_DISTANCE,
            DimensionType.VERTICAL_DISTANCE,
        }
    )
    match kinds:
        case (RefKind.LINE,):
            return frozenset(
                {
                    DimensionType.LENGTH,
                    DimensionType.HORIZONTAL_DISTANCE,
                    DimensionType.VERTICAL_DISTANCE,
                }
            )
        case (RefKind.CIRCLE | RefKind.ARC,):
            return frozenset({DimensionType.RADIUS, DimensionType.DIAMETER})
        case (RefKind.LINE, RefKind.LINE):
            if _parallel(document, refs[0], refs[1]):
                return frozenset({DimensionType.DISTANCE})
            return frozenset({DimensionType.ANGLE})
        case (a, b) if a is RefKind.LINE or b is RefKind.LINE:
            return frozenset({DimensionType.DISTANCE})
        case (_, _) if refs[0] != refs[1]:
            return distances  # points, and circles or arcs by their centres
    return frozenset()


def _parallel(document: Document, a: Ref, b: Ref) -> bool:
    f = frame(document, [a.entity, b.entity])
    la, lb = f.straight(a), f.straight(b)
    ax, ay = la.b[0].v - la.a[0].v, la.b[1].v - la.a[1].v
    bx, by = lb.b[0].v - lb.a[0].v, lb.b[1].v - lb.a[1].v
    return abs(ax * by - ay * bx) <= 1e-9 * math.hypot(ax, ay) * math.hypot(bx, by)


def _describe(document: Document, refs: Sequence[Ref]) -> str:
    if not refs:
        return "an empty selection"
    return "a selection of " + ", ".join(ref_kind(document, r).value for r in refs)


# --- Picking a kind from placement --------------------------------------------------------


def _preferred(
    document: Document, refs: tuple[Ref, ...], placement: Point2, allowed: frozenset[DimensionType]
) -> DimensionType:
    if len(allowed) == 1:
        return next(iter(allowed))
    if DimensionType.RADIUS in allowed:
        curve = document.entities[refs[0].entity]
        assert isinstance(curve, Circle | Arc)
        inside = math.hypot(placement.x - curve.center.x, placement.y - curve.center.y) < (
            curve.radius
        )
        if isinstance(curve, Arc) or inside:
            return DimensionType.RADIUS
        return DimensionType.DIAMETER
    p, q = _points(document, refs)
    aligned = DimensionType.LENGTH if DimensionType.LENGTH in allowed else DimensionType.DISTANCE
    if p.x == q.x or p.y == q.y:
        return aligned
    within_x = min(p.x, q.x) <= placement.x <= max(p.x, q.x)
    within_y = min(p.y, q.y) <= placement.y <= max(p.y, q.y)
    if within_x and not within_y:
        return DimensionType.HORIZONTAL_DISTANCE  # above or below: measures across
    if within_y and not within_x:
        return DimensionType.VERTICAL_DISTANCE  # beside: measures up
    return aligned


def _points(document: Document, refs: tuple[Ref, ...]) -> tuple[Point2, Point2]:
    """The two locations a point-to-point style dimension measures between."""
    ends = _point_refs(document, refs)
    f = frame(document, [r.entity for r in ends])
    a, b = f.point(ends[0]), f.point(ends[1])
    return Point2(x=a[0].v, y=a[1].v), Point2(x=b[0].v, y=b[1].v)


def _point_refs(document: Document, refs: tuple[Ref, ...]) -> tuple[Ref, Ref]:
    """A line's two ends, or each reference with circles and arcs replaced by their centre."""
    if len(refs) == 1:
        return _ends(document, refs[0])
    a, b = (_as_point(document, r) for r in refs)
    return a, b


def _ends(document: Document, ref: Ref) -> tuple[Ref, Ref]:
    if isinstance(document.entities[ref.entity], Line):
        return Ref(entity=ref.entity, feature=Feature.START), Ref(
            entity=ref.entity, feature=Feature.END
        )
    first, second = _SIDE_ENDS[ref.feature]
    return Ref(entity=ref.entity, feature=first), Ref(entity=ref.entity, feature=second)


def _as_point(document: Document, ref: Ref) -> Ref:
    if ref_kind(document, ref) in _ROUND:
        return Ref(entity=ref.entity, feature=Feature.CENTER)
    return ref


# --- Building the entity ------------------------------------------------------------------


def _entity(
    document: Document, refs: tuple[Ref, ...], placement: Point2, kind: DimensionType
) -> Dimension:
    match kind:
        case DimensionType.RADIUS | DimensionType.DIAMETER:
            curve = document.entities[refs[0].entity]
            assert isinstance(curve, Circle | Arc)
            angle = math.degrees(
                math.atan2(placement.y - curve.center.y, placement.x - curve.center.x)
            )
            return RadialDimension(
                target=refs[0].entity,
                measure=RadialMeasure.RADIUS
                if kind is DimensionType.RADIUS
                else RadialMeasure.DIAMETER,
                label_angle=angle % 360.0 + 0.0,
            )
        case DimensionType.ANGLE:
            supplementary, offset = _angle_placement(document, refs, placement)
            return AngleDimension(a=refs[0], b=refs[1], supplementary=supplementary, offset=offset)
    orientation = {
        DimensionType.HORIZONTAL_DISTANCE: DistanceOrientation.HORIZONTAL,
        DimensionType.VERTICAL_DISTANCE: DistanceOrientation.VERTICAL,
    }.get(kind, DistanceOrientation.ALIGNED)
    # A distance to a line keeps the line itself as the reference (perpendicular distance);
    # everything else measures between two points.
    a, b = _point_refs(document, refs)
    if len(refs) == 2 and any(ref_kind(document, r) is RefKind.LINE for r in refs):
        a, b = _as_point(document, refs[0]), _as_point(document, refs[1])
    return DistanceDimension(
        a=a, b=b, orientation=orientation, offset=_offset(document, a, b, placement)
    )


def _offset(document: Document, a: Ref, b: Ref, placement: Point2) -> float:
    """Signed distance from the a→b direction to the placement, positive on its left."""
    p, q = _anchor(document, a, b), _anchor(document, b, a)
    dx, dy = q.x - p.x, q.y - p.y
    length = math.hypot(dx, dy)
    if length == 0.0:
        return 0.0
    return (dx * (placement.y - p.y) - dy * (placement.x - p.x)) / length + 0.0


def _anchor(document: Document, ref: Ref, other: Ref) -> Point2:
    """Where a dimension attaches to `ref`: the point itself, or the foot on a line."""
    f = frame(document, [ref.entity, other.entity])
    if ref_kind(document, ref) is RefKind.POINT:
        x, y = f.point(ref)
        return Point2(x=x.v, y=y.v)
    line = f.straight(ref)
    if ref_kind(document, other) is RefKind.POINT:
        target = f.point(other)
        tx, ty = target[0].v, target[1].v
    else:
        side = f.straight(other)
        tx, ty = (side.a[0].v + side.b[0].v) / 2, (side.a[1].v + side.b[1].v) / 2
    return _foot(line, tx, ty)


def _foot(line: Straight, x: float, y: float) -> Point2:
    ax, ay = line.a[0].v, line.a[1].v
    dx, dy = line.b[0].v - ax, line.b[1].v - ay
    t = ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)
    return Point2(x=ax + t * dx, y=ay + t * dy)


def _angle_placement(
    document: Document, refs: tuple[Ref, ...], placement: Point2
) -> tuple[bool, float]:
    """Which pair of sectors the label is in, and its signed distance from the crossing."""
    f = frame(document, [refs[0].entity, refs[1].entity])
    la, lb = f.straight(refs[0]), f.straight(refs[1])
    ax, ay = la.a[0].v, la.a[1].v
    dax, day = la.b[0].v - ax, la.b[1].v - ay
    bx, by = lb.a[0].v, lb.a[1].v
    dbx, dby = lb.b[0].v - bx, lb.b[1].v - by
    det = dax * dby - day * dbx
    # Where the two infinite lines cross.
    t = ((bx - ax) * dby - (by - ay) * dbx) / det
    cx, cy = ax + t * dax, ay + t * day
    ux, uy = placement.x - cx, placement.y - cy
    # The placement as a mix of the two directions: same signs is the drawn angle's sectors.
    alpha = (ux * dby - uy * dbx) / det
    beta = (dax * uy - day * ux) / det
    distance = math.hypot(ux, uy)
    return alpha * beta < 0, (distance if alpha >= 0 else -distance) + 0.0
