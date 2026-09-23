"""Constraint inference: relationships the geometry nearly has, offered, never applied.

`suggest` finds them; nothing here changes a document. A suggestion becomes a constraint only
when someone sends its `CreateConstraint`. `Suggestions` holds the per-session choices a
sketcher needs around that (switch inference off, reject one so it isn't offered again,
accept one), for the shell and an agent alike. It is session state: never saved, never undone.
"""

import math
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from caliper.contracts.commands import CreateConstraint
from caliper.contracts.document import (
    Arc,
    Circle,
    Constraint,
    ConstraintType,
    Document,
    EntityId,
    Feature,
    Line,
    Point,
    Rectangle,
    Ref,
)
from caliper.contracts.queries import Suggestion
from caliper.engine.constraints.dimensions import frame
from caliper.engine.constraints.relations import Match, match
from caliper.engine.constraints.sketch import clusters, implied
from caliper.engine.spatial import around, grid

_GEOMETRY = (Point, Line, Circle, Arc, Rectangle)
_ENDS = {
    Point: (Feature.POINT,),
    Line: (Feature.START, Feature.END),
    Circle: (Feature.CENTER,),
    Arc: (Feature.CENTER, Feature.START, Feature.END),
    Rectangle: (
        Feature.BOTTOM_LEFT,
        Feature.BOTTOM_RIGHT,
        Feature.TOP_RIGHT,
        Feature.TOP_LEFT,
    ),
}
"""Points a coincidence is suggested for. Midpoints get a Midpoint suggestion instead."""
_SIDES = (Feature.BOTTOM, Feature.RIGHT, Feature.TOP, Feature.LEFT)
_TYPE_ORDER = {t: i for i, t in enumerate(ConstraintType)}


def suggest(
    document: Document, ids: Sequence[EntityId], tolerance: float, angle_tolerance: float
) -> tuple[Suggestion, ...]:
    if not (math.isfinite(tolerance) and tolerance >= 0):
        return ()
    if not (math.isfinite(angle_tolerance) and 0 <= angle_tolerance < 90):
        return ()
    geometry = sorted(i for i, e in document.entities.items() if isinstance(e, _GEOMETRY))
    scope = set(ids) & set(geometry) if ids else set(geometry)
    sine = math.sin(math.radians(angle_tolerance))
    near = _Near(document, tolerance)
    candidates = [
        *_levels(document, scope, angle_tolerance),
        *_coincidences(document, near, scope, tolerance),
        *_pairs_of_lines(document, geometry, scope, sine),
        *_tangencies(document, near, scope, tolerance),
    ]
    existing = {
        (e.type, frozenset(e.refs)) for e in document.entities.values() if isinstance(e, Constraint)
    }
    joins = {g: c for c in clusters(document) for g in c.geometry}
    free = _free_id(document)
    seen: set[tuple[ConstraintType, frozenset[Ref]]] = set()
    found: list[Suggestion] = []
    for type_, refs, deviation in candidates:
        key = (type_, frozenset(refs))
        if key in existing or key in seen:
            continue
        seen.add(key)
        fitted = match(document, type_, refs)
        if not isinstance(fitted, Match):
            continue
        if {r.entity for r in refs} & joins.keys() and implied(
            document, Constraint(type=type_, refs=fitted.refs), free, joins
        ):
            continue
        found.append(Suggestion(type=type_, refs=fitted.refs, deviation=deviation + 0.0))
    found.sort(key=lambda s: (s.deviation, _TYPE_ORDER[s.type], [str(r) for r in s.refs]))
    return tuple(found)


type Candidate = tuple[ConstraintType, tuple[Ref, ...], float]


class _Near:
    """Geometry within reach of a point or an entity, from the document's grid, and each
    entity's points, worked out once. Only narrows a search: every pair is still tested."""

    def __init__(self, document: Document, tolerance: float) -> None:
        self._document = document
        self._grid = grid(document)
        self.tolerance = tolerance
        self._points: dict[EntityId, list[tuple[Ref, float, float]]] = {}

    def point(self, x: float, y: float) -> list[EntityId]:
        return self._grid.near(x, y, self.tolerance)

    def entity(self, id: EntityId) -> list[EntityId]:
        return self._grid.overlapping(around(self._grid.boxes[id], self.tolerance))

    def points(self, id: EntityId) -> list[tuple[Ref, float, float]]:
        if id not in self._points:
            self._points[id] = list(_points(self._document, [id]))
        return self._points[id]


def _levels(document: Document, scope: Iterable[EntityId], limit: float) -> Iterator[Candidate]:
    for id in sorted(scope):
        line = document.entities[id]
        if not isinstance(line, Line):
            continue
        dx, dy = line.end.x - line.start.x, line.end.y - line.start.y
        tilt = math.degrees(math.atan2(abs(dy), abs(dx)))
        curve = (Ref(entity=id, feature=Feature.CURVE),)
        if tilt <= limit:
            yield ConstraintType.HORIZONTAL, curve, tilt
        if 90.0 - tilt <= limit:
            yield ConstraintType.VERTICAL, curve, 90.0 - tilt


def _points(document: Document, ids: Iterable[EntityId]) -> Iterator[tuple[Ref, float, float]]:
    for id in ids:
        entity = document.entities[id]
        f = frame(document, [id])
        for feature in _ENDS[type(entity)]:
            ref = Ref(entity=id, feature=feature)
            x, y = f.point(ref)
            yield ref, x.v, y.v


def _coincidences(
    document: Document, near: _Near, scope: set[EntityId], tolerance: float
) -> Iterator[Candidate]:
    """Pairs with at least one entity in scope: points that meet, and points on or at the
    middle of a curve. A pair tested once from each side is reported once."""
    pairs: set[tuple[Ref, Ref]] = set()
    for id in sorted(scope):
        for a, ax, ay in near.points(id):
            for other in near.point(ax, ay):
                if other == id:
                    continue
                for b, bx, by in near.points(other):
                    # The two belong to different entities: the lower id first, as in a list of
                    # every point by id, so the gap is the same number whichever side found it.
                    (p, px, py), (q, qx, qy) = sorted(
                        ((a, ax, ay), (b, bx, by)), key=lambda point: point[0].entity
                    )
                    if (p, q) in pairs:
                        continue
                    pairs.add((p, q))
                    if (gap := math.hypot(qx - px, qy - py)) <= tolerance:
                        yield ConstraintType.COINCIDENT, (p, q), gap
    tested: set[tuple[Ref, EntityId]] = set()
    for id in sorted(scope):
        for ref, x, y in near.points(id):  # this entity's points against curves near them
            for other in near.point(x, y):
                if other != id and (ref, other) not in tested:
                    tested.add((ref, other))
                    yield from _on_curve(document, near, ref, x, y, other, tolerance)
        if not isinstance(document.entities[id], Line | Circle | Arc):
            continue
        for other in near.entity(id):  # this curve against the points near it
            if other == id:
                continue
            for ref, x, y in near.points(other):
                if (ref, id) not in tested:
                    tested.add((ref, id))
                    yield from _on_curve(document, near, ref, x, y, id, tolerance)


def _on_curve(
    document: Document,
    near: _Near,
    ref: Ref,
    x: float,
    y: float,
    id: EntityId,
    tolerance: float,
) -> Iterator[Candidate]:
    """A point at the middle of line `id`, or on curve `id` away from its ends."""
    entity = document.entities[id]
    if isinstance(entity, Line):
        mid = ((entity.start.x + entity.end.x) / 2, (entity.start.y + entity.end.y) / 2)
        if (gap := math.hypot(x - mid[0], y - mid[1])) <= tolerance:
            yield ConstraintType.MIDPOINT, (Ref(entity=id, feature=Feature.CURVE), ref), gap
            return
    reach = _to_curve(entity, x, y)
    near_end = any(math.hypot(x - px, y - py) <= tolerance for _, px, py in near.points(id))
    if reach is not None and reach <= tolerance and not near_end:
        on = Ref(entity=id, feature=Feature.CURVE)
        yield ConstraintType.COINCIDENT, (on, ref), reach


def _to_curve(entity: object, x: float, y: float) -> float | None:
    """Distance from a point to a line segment, circle, or arc; None for other geometry."""
    match entity:
        case Line(start=a, end=b):
            ex, ey = b.x - a.x, b.y - a.y
            t = max(0.0, min(1.0, ((x - a.x) * ex + (y - a.y) * ey) / (ex * ex + ey * ey)))
            return math.hypot(x - (a.x + t * ex), y - (a.y + t * ey))
        case Circle(center=c, radius=r):
            return abs(math.hypot(x - c.x, y - c.y) - r)
        case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
            angle = math.degrees(math.atan2(y - c.y, x - c.x))
            if (angle - start) % 360.0 <= sweep:
                return abs(math.hypot(x - c.x, y - c.y) - r)
    return None


def _straights(document: Document, ids: Iterable[EntityId]) -> Iterator[tuple[Ref, float, float]]:
    """Lines and rectangle sides, with their direction."""
    for id in ids:
        entity = document.entities[id]
        if isinstance(entity, Line):
            dx, dy = entity.end.x - entity.start.x, entity.end.y - entity.start.y
            yield Ref(entity=id, feature=Feature.CURVE), dx, dy
        elif isinstance(entity, Rectangle):
            directions = ((1.0, 0.0), (0.0, 1.0), (1.0, 0.0), (0.0, 1.0))
            for side, (dx, dy) in zip(_SIDES, directions, strict=True):
                yield Ref(entity=id, feature=side), dx, dy


def _pairs_of_lines(
    document: Document, geometry: Sequence[EntityId], scope: set[EntityId], sine: float
) -> Iterator[Candidate]:
    """Parallel and perpendicular don't depend on distance, so every straight is a partner,
    but only for straights in scope."""
    straights = list(_straights(document, geometry))
    mine = [ref.entity in scope for ref, _, _ in straights]
    for i in (i for i, yes in enumerate(mine) if yes):
        # A pair of two straights in scope is taken once, from its first straight.
        for j in (j for j in range(len(straights)) if j != i and not (mine[j] and j < i)):
            (a, ax, ay), (b, bx, by) = straights[min(i, j)], straights[max(i, j)]
            if a.entity == b.entity:
                continue
            la, lb = math.hypot(ax, ay), math.hypot(bx, by)
            cross, dot = (ax * by - ay * bx) / (la * lb), (ax * bx + ay * by) / (la * lb)
            if abs(cross) <= sine:
                angle = math.degrees(math.asin(min(1.0, abs(cross))))
                yield ConstraintType.PARALLEL, (a, b), angle
            if abs(dot) <= sine:
                angle = math.degrees(math.asin(min(1.0, abs(dot))))
                yield ConstraintType.PERPENDICULAR, (a, b), angle


def _tangencies(
    document: Document, near: _Near, scope: set[EntityId], tolerance: float
) -> Iterator[Candidate]:
    """A line and a circle or arc, or two circles or arcs, at least one of them in scope."""
    pairs: set[tuple[EntityId, EntityId]] = set()
    for id in sorted(scope):
        for other in near.entity(id):
            first, second = sorted((id, other))
            if other == id or (first, second) in pairs:
                continue
            pairs.add((first, second))
            a, b = document.entities[first], document.entities[second]
            if isinstance(a, Circle | Arc) and isinstance(b, Line):
                yield from _line_tangent(second, b, first, a, tolerance)
            elif isinstance(a, Line) and isinstance(b, Circle | Arc):
                yield from _line_tangent(first, a, second, b, tolerance)
            elif isinstance(a, Circle | Arc) and isinstance(b, Circle | Arc):
                apart = math.hypot(b.center.x - a.center.x, b.center.y - a.center.y)
                if apart == 0.0:
                    continue
                gap = min(abs(apart - (a.radius + b.radius)), abs(apart - abs(a.radius - b.radius)))
                if gap <= tolerance:
                    curves = (
                        Ref(entity=first, feature=Feature.CURVE),
                        Ref(entity=second, feature=Feature.CURVE),
                    )
                    yield ConstraintType.TANGENT, curves, gap


def _line_tangent(
    line_id: EntityId, line: Line, id: EntityId, curve: Circle | Arc, tolerance: float
) -> Iterator[Candidate]:
    c, r = curve.center, curve.radius
    ex, ey = line.end.x - line.start.x, line.end.y - line.start.y
    length = math.hypot(ex, ey)
    t = ((c.x - line.start.x) * ex + (c.y - line.start.y) * ey) / (length * length)
    if not 0.0 <= t <= 1.0:
        return  # touching the extension only
    gap = abs(abs(ex * (c.y - line.start.y) - ey * (c.x - line.start.x)) / length - r)
    if gap <= tolerance:
        refs = (Ref(entity=line_id, feature=Feature.CURVE), Ref(entity=id, feature=Feature.CURVE))
        yield ConstraintType.TANGENT, refs, gap


def _free_id(document: Document) -> EntityId:
    number = document.next_id
    while EntityId(f"e{number}") in document.entities:
        number += 1
    return EntityId(f"e{number}")


# --- Session choices ----------------------------------------------------------------------


@dataclass
class Suggestions:
    """What a sketching session has decided about suggestions. Never saved, never undone.

    Ignoring one needs nothing: it simply isn't accepted. Rejecting one hides it until the
    session ends; switching inference off hides them all.
    """

    enabled: bool = True
    _rejected: set[tuple[ConstraintType, frozenset[Ref]]] = field(default_factory=set)

    def visible(self, suggestions: Iterable[Suggestion]) -> tuple[Suggestion, ...]:
        if not self.enabled:
            return ()
        return tuple(s for s in suggestions if (s.type, frozenset(s.refs)) not in self._rejected)

    def reject(self, suggestion: Suggestion) -> None:
        self._rejected.add((suggestion.type, frozenset(suggestion.refs)))

    def accept(self, suggestion: Suggestion) -> CreateConstraint:
        """The command that makes it a real constraint. Send it to the bus like any other."""
        return CreateConstraint(type=suggestion.type, refs=suggestion.refs)

    def forget_rejections(self) -> None:
        self._rejected.clear()
