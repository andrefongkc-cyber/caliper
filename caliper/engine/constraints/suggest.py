"""Constraint inference: relationships the geometry nearly has, offered, never applied.

`suggest` finds them; nothing here changes a document. A suggestion becomes a constraint only
when someone sends its `CreateConstraint`. `Suggestions` holds the per-session choices a
sketcher needs around that (switch inference off, reject one so it isn't offered again,
accept one), for the shell and an agent alike. It is session state: never saved, never undone.
"""

import math
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import combinations

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
    candidates = [
        *_levels(document, scope, angle_tolerance),
        *_coincidences(document, geometry, scope, tolerance),
        *_pairs_of_lines(document, geometry, scope, sine),
        *_tangencies(document, geometry, scope, tolerance),
    ]
    existing = {
        (e.type, frozenset(e.refs)) for e in document.entities.values() if isinstance(e, Constraint)
    }
    constrained = {g for c in clusters(document) for g in c.geometry}
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
        if {r.entity for r in refs} & constrained and implied(
            document, Constraint(type=type_, refs=fitted.refs), _free_id(document)
        ):
            continue
        found.append(Suggestion(type=type_, refs=fitted.refs, deviation=deviation + 0.0))
    found.sort(key=lambda s: (s.deviation, _TYPE_ORDER[s.type], [str(r) for r in s.refs]))
    return tuple(found)


type Candidate = tuple[ConstraintType, tuple[Ref, ...], float]


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
    document: Document, geometry: Sequence[EntityId], scope: set[EntityId], tolerance: float
) -> Iterator[Candidate]:
    points = list(_points(document, geometry))
    for (a, ax, ay), (b, bx, by) in combinations(points, 2):
        if a.entity == b.entity or not {a.entity, b.entity} & scope:
            continue
        if (gap := math.hypot(bx - ax, by - ay)) <= tolerance:
            yield ConstraintType.COINCIDENT, (a, b), gap
    for ref, x, y in points:
        for id in geometry:
            if id == ref.entity or not {id, ref.entity} & scope:
                continue
            entity = document.entities[id]
            if isinstance(entity, Line):
                mid = ((entity.start.x + entity.end.x) / 2, (entity.start.y + entity.end.y) / 2)
                if (gap := math.hypot(x - mid[0], y - mid[1])) <= tolerance:
                    yield ConstraintType.MIDPOINT, (Ref(entity=id, feature=Feature.CURVE), ref), gap
                    continue
            reach = _to_curve(entity, x, y)
            near_end = any(
                math.hypot(x - px, y - py) <= tolerance for _, px, py in _points(document, [id])
            )
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
    for (a, ax, ay), (b, bx, by) in combinations(list(_straights(document, geometry)), 2):
        if a.entity == b.entity or not {a.entity, b.entity} & scope:
            continue
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        cross, dot = (ax * by - ay * bx) / (la * lb), (ax * bx + ay * by) / (la * lb)
        if abs(cross) <= sine:
            yield ConstraintType.PARALLEL, (a, b), math.degrees(math.asin(min(1.0, abs(cross))))
        if abs(dot) <= sine:
            yield ConstraintType.PERPENDICULAR, (a, b), math.degrees(math.asin(min(1.0, abs(dot))))


def _tangencies(
    document: Document, geometry: Sequence[EntityId], scope: set[EntityId], tolerance: float
) -> Iterator[Candidate]:
    rounds = [i for i in geometry if isinstance(document.entities[i], Circle | Arc)]
    for id in rounds:
        curve = document.entities[id]
        assert isinstance(curve, Circle | Arc)
        c, r = curve.center, curve.radius
        ref = Ref(entity=id, feature=Feature.CURVE)
        for line_id in geometry:
            line = document.entities[line_id]
            if not isinstance(line, Line) or not {id, line_id} & scope:
                continue
            ex, ey = line.end.x - line.start.x, line.end.y - line.start.y
            length = math.hypot(ex, ey)
            t = ((c.x - line.start.x) * ex + (c.y - line.start.y) * ey) / (length * length)
            if not 0.0 <= t <= 1.0:
                continue  # touching the extension only
            gap = abs(abs(ex * (c.y - line.start.y) - ey * (c.x - line.start.x)) / length - r)
            if gap <= tolerance:
                yield ConstraintType.TANGENT, (Ref(entity=line_id, feature=Feature.CURVE), ref), gap
        for other in rounds:
            if other <= id or not {id, other} & scope:
                continue
            second = document.entities[other]
            assert isinstance(second, Circle | Arc)
            apart = math.hypot(second.center.x - c.x, second.center.y - c.y)
            if apart == 0.0:
                continue
            gap = min(abs(apart - (r + second.radius)), abs(apart - abs(r - second.radius)))
            if gap <= tolerance:
                yield ConstraintType.TANGENT, (ref, Ref(entity=other, feature=Feature.CURVE)), gap


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
