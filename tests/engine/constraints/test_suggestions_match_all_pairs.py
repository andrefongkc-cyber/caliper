"""Suggestions search nearby geometry through the grid now. The answers mustn't change.

The all-pairs search they replaced is kept below, verbatim apart from its name, as the
reference: for random sketches, scopes, and tolerances, the two must agree exactly, deviations
to the last bit included. Coordinates sit on a coarse lattice with small offsets, so points
nearly meet, circles nearly touch, and lines are nearly parallel often enough to matter.
"""

import math
from collections.abc import Iterable, Iterator, Sequence
from itertools import combinations
from types import MappingProxyType

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Command,
    CreateArc,
    CreateCircle,
    CreateConstraint,
    CreateLine,
    CreatePoint,
    CreateRectangle,
)
from caliper.contracts.document import (
    CURVE_FEATURES,
    POINT_FEATURES,
    Arc,
    Circle,
    Constraint,
    ConstraintType,
    Document,
    EntityId,
    Feature,
    Line,
    Point,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.queries import Suggestion
from caliper.engine.commands.bus import Bus
from caliper.engine.constraints.dimensions import frame
from caliper.engine.constraints.relations import Match, match
from caliper.engine.constraints.sketch import System, _redundancy, clusters
from caliper.engine.constraints.suggest import suggest

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
_SIDES = (Feature.BOTTOM, Feature.RIGHT, Feature.TOP, Feature.LEFT)
_TYPE_ORDER = {t: i for i, t in enumerate(ConstraintType)}


# --- The reference: every pair, and a whole new document's clusters per question ------------


def _all_clusters_implied(document: Document, constraint: Constraint, id: EntityId) -> bool:
    with_it = Document(
        entities=MappingProxyType({**document.entities, id: constraint}), next_id=document.next_id
    )
    for cluster in clusters(with_it):
        if id in cluster.relations:
            system = System.build(with_it, cluster)
            return _redundancy(system, system.values, frozenset({id})) is not None
    return False


def all_pairs_suggest(
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
        if {r.entity for r in refs} & constrained and _all_clusters_implied(
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


# --- Random sketches ------------------------------------------------------------------------

lattice = st.builds(
    lambda i, j, di, dj: Point2(x=5.0 * i + di, y=5.0 * j + dj),
    st.integers(-3, 3),
    st.integers(-3, 3),
    st.sampled_from([0.0, 0.0, 0.1, -0.2, 0.4]),
    st.sampled_from([0.0, 0.0, 0.1, -0.2, 0.4]),
)


@st.composite
def geometry(draw: st.DrawFn) -> Command:
    match draw(st.sampled_from(["point", "line", "line", "circle", "arc", "rectangle"])):
        case "point":
            return CreatePoint(position=draw(lattice))
        case "line":
            start, end = draw(lattice), draw(lattice)
            if start == end:
                end = Point2(x=end.x + 5.0, y=end.y)
            return CreateLine(start=start, end=end)
        case "circle":
            return CreateCircle(center=draw(lattice), radius=draw(st.sampled_from([2.5, 5.0, 4.9])))
        case "arc":
            return CreateArc(
                center=draw(lattice),
                radius=draw(st.sampled_from([2.5, 5.0])),
                start_angle=draw(st.sampled_from([0.0, 45.0, 90.0, 180.0])),
                sweep_angle=draw(st.sampled_from([90.0, 180.0, 270.0])),
            )
        case _:
            return CreateRectangle(
                corner=draw(lattice),
                width=draw(st.sampled_from([5.0, 10.0])),
                height=draw(st.sampled_from([5.0, 9.9])),
            )


@st.composite
def sketches(draw: st.DrawFn) -> Document:
    """Up to 10 shapes, then a few constraints on them, so some suggestions already exist or
    are implied and must be skipped."""
    bus = Bus(kernel=None)
    for command in draw(st.lists(geometry(), min_size=1, max_size=10)):
        bus.execute(command)
    for _ in range(draw(st.integers(0, 3))):
        refs = [
            Ref(entity=id, feature=feature)
            for id, entity in sorted(bus.document.entities.items())
            if isinstance(entity, _GEOMETRY)
            for feature in sorted(POINT_FEATURES[type(entity)] | CURVE_FEATURES[type(entity)])
        ]
        chosen = draw(st.lists(st.sampled_from(refs), min_size=1, max_size=2, unique=True))
        fits = [
            o.type
            for o in bus.queries.applicable_constraints(chosen)
            if o.error is None and isinstance(o.type, ConstraintType)
        ]
        if fits:
            bus.execute(CreateConstraint(type=draw(st.sampled_from(fits)), refs=tuple(chosen)))
    return bus.document


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    document=sketches(),
    data=st.data(),
    tolerance=st.sampled_from([0.0, 0.25, 0.5, 1.0, 3.0]),
    angle_tolerance=st.sampled_from([0.0, 1.0, 5.0]),
)
def test_suggestions_match_the_all_pairs_search(
    document: Document, data: st.DataObject, tolerance: float, angle_tolerance: float
) -> None:
    geometry_ids = sorted(i for i, e in document.entities.items() if isinstance(e, _GEOMETRY))
    ids = data.draw(st.lists(st.sampled_from(geometry_ids), max_size=3, unique=True))
    expected = all_pairs_suggest(document, ids, tolerance, angle_tolerance)
    assert suggest(document, ids, tolerance, angle_tolerance) == expected


def test_the_reference_finds_every_kind_the_new_search_must_match() -> None:
    # Guards the property test above against sketches too sparse to suggest anything.
    bus = Bus(kernel=None)
    bus.execute(CreateLine(start=Point2(x=0.0, y=0.0), end=Point2(x=10.0, y=0.1)))  # e1
    bus.execute(CreateLine(start=Point2(x=10.2, y=0.0), end=Point2(x=10.0, y=10.0)))  # e2
    bus.execute(CreateCircle(center=Point2(x=5.0, y=5.2), radius=5.0))  # e3
    bus.execute(CreateCircle(center=Point2(x=15.1, y=5.0), radius=5.0))  # e4
    bus.execute(CreatePoint(position=Point2(x=5.1, y=0.0)))  # e5
    kinds = {s.type for s in all_pairs_suggest(bus.document, [], 0.5, 2.0)}
    assert kinds >= {
        ConstraintType.HORIZONTAL,
        ConstraintType.VERTICAL,
        ConstraintType.COINCIDENT,
        ConstraintType.MIDPOINT,
        ConstraintType.PERPENDICULAR,
        ConstraintType.TANGENT,
    }
    assert suggest(bus.document, [], 0.5, 2.0) == all_pairs_suggest(bus.document, [], 0.5, 2.0)
    for id in sorted(bus.document.entities):
        assert suggest(bus.document, [id], 0.5, 2.0) == all_pairs_suggest(
            bus.document, [id], 0.5, 2.0
        )
