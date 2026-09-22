"""What each constraint type and dimension means, as equations over features.

Every ConstraintType has a `Spec`: the selections it accepts (`Rule`s, matched by the kind
of each reference) and, per selection, the equations that must be zero. Applicability,
canonical reference order, the solver's equations, and which reference moves all come from
this one table. Adding a type is one more entry, not another branch somewhere else.

Dimensions share the same machinery: `measure` gives what any dimension measures, and a
driving dimension's equation is its measurement minus its value.

Equations are dimensionless or in mm (angles as sines, radians, or degrees where noted).
Choices that depend on configuration, such as which side of a line a tangent circle sits
on, are read from `Setting.first`, the geometry when the solve began, so a solve never
flips a shape inside out.
"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import permutations

from caliper.contracts.document import (
    POINT_FEATURES,
    Arc,
    Circle,
    ConstraintType,
    DistanceDimension,
    DistanceOrientation,
    Document,
    Feature,
    Line,
    RadialDimension,
    RadialMeasure,
    Rectangle,
    Ref,
)
from caliper.engine.constraints.ad import Dual, atan2, hypot
from caliper.engine.constraints.model import Frame, P, Round, Straight

# --- Reference kinds --------------------------------------------------------------------


class RefKind(StrEnum):
    POINT = "point"
    LINE = "line"
    """A straight curve: a line, or a rectangle side."""
    CIRCLE = "circle"
    ARC = "arc"


def ref_kind(document: Document, ref: Ref) -> RefKind:
    """The kind of a reference that has already been validated."""
    entity = document.entities[ref.entity]
    if ref.feature in POINT_FEATURES[type(entity)]:  # type: ignore[index]
        return RefKind.POINT
    if isinstance(entity, Circle):
        return RefKind.CIRCLE
    if isinstance(entity, Arc):
        return RefKind.ARC
    return RefKind.LINE


POINT = frozenset({RefKind.POINT})
LINE = frozenset({RefKind.LINE})
ROUND = frozenset({RefKind.CIRCLE, RefKind.ARC})
ARC = frozenset({RefKind.ARC})
CURVE = LINE | ROUND
ANY = POINT | CURVE


@dataclass(frozen=True, slots=True)
class Setting:
    first: Frame
    """The geometry when the solve began. Fixes signs and branches."""
    anchor: Frame
    """The geometry before the command. Where Fix holds things."""


type Equations = Callable[[Frame, tuple[Ref, ...], Setting], list[Dual]]
type Check = Callable[[Document, tuple[Ref, ...]], str | None]


@dataclass(frozen=True, slots=True)
class Rule:
    kinds: tuple[frozenset[RefKind], ...]
    """The kind each reference must be, in canonical order."""
    equations: Equations | None
    """None for a selection that is recognised but can never hold; see `impossible`."""
    mover: int = -1
    """Which reference moves when the constraint is added (index into canonical order)."""
    check: Check | None = None
    """Extra conditions on the actual references; returns why they don't apply."""
    impossible: str = ""


@dataclass(frozen=True, slots=True)
class Spec:
    needs: str
    """What the type accepts, for messages: "one line, or two points"."""
    rules: tuple[Rule, ...]
    unsupported: str = ""
    """Set when the type can't be used at all yet, and why."""


# --- Vector helpers (on Duals) ------------------------------------------------------------


def _sub(p: P, q: P) -> P:
    return p[0] - q[0], p[1] - q[1]


def _cross(u: P, v: P) -> Dual:
    return u[0] * v[1] - u[1] * v[0]


def _dot(u: P, v: P) -> Dual:
    return u[0] * v[0] + u[1] * v[1]


def _length(u: P) -> Dual:
    return hypot(u[0], u[1])


def _direction(line: Straight) -> P:
    return _sub(line.b, line.a)


def _offset(p: P, line: Straight) -> Dual:
    """Signed distance from `p` to the line's infinite extension, positive on its left."""
    d = _direction(line)
    return _cross(d, _sub(p, line.a)) / _length(d)


def _sine(a: Straight, b: Straight) -> Dual:
    da, db = _direction(a), _direction(b)
    return _cross(da, db) / (_length(da) * _length(db))


def _cosine(a: Straight, b: Straight) -> Dual:
    da, db = _direction(a), _direction(b)
    return _dot(da, db) / (_length(da) * _length(db))


def _sign(value: float) -> float:
    return -1.0 if value < 0 else 1.0


def _round(frame: Frame, ref: Ref) -> Round:
    curve = frame.curve(ref)
    assert isinstance(curve, Round)
    return curve


def _on_curve(p: P, frame: Frame, ref: Ref) -> Dual:
    curve = frame.curve(ref)
    if isinstance(curve, Straight):
        return _offset(p, curve)
    return _length(_sub(p, curve.center)) - curve.radius


def _center(frame: Frame, ref: Ref) -> P:
    """A circle or arc's centre, or a point itself."""
    if ref.feature is Feature.CURVE and frame.kind(ref.entity) in (Circle, Arc):
        return _round(frame, ref).center
    return frame.point(ref)


# --- Equations per constraint type --------------------------------------------------------


def _coincident_points(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    (px, py), (qx, qy) = f.point(r[0]), f.point(r[1])
    return [qx - px, qy - py]


def _point_on_curve(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    return [_on_curve(f.point(r[1]), f, r[0])]


def _collinear(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    a, b = f.straight(r[0]), f.straight(r[1])
    return [_offset(b.a, a), _offset(b.b, a)]


def _same_circle(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    a, b = _round(f, r[0]), _round(f, r[1])
    return [b.center[0] - a.center[0], b.center[1] - a.center[1], b.radius - a.radius]


def _concentric(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    (ax, ay), (bx, by) = _center(f, r[0]), _center(f, r[1])
    return [bx - ax, by - ay]


def _level(axis: int) -> Equations:
    def equations(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
        if len(r) == 1:
            line = f.straight(r[0])
            return [line.b[axis] - line.a[axis]]
        return [f.point(r[1])[axis] - f.point(r[0])[axis]]

    return equations


def _parallel(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    return [_sine(f.straight(r[0]), f.straight(r[1]))]


def _perpendicular(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    return [_cosine(f.straight(r[0]), f.straight(r[1]))]


def _tangent_line(f: Frame, r: tuple[Ref, ...], at: Setting) -> list[Dual]:
    line_ref, round_ref = (r[1], r[0]) if isinstance(f.curve(r[0]), Round) else (r[0], r[1])
    side = _sign(_offset(_round(at.first, round_ref).center, at.first.straight(line_ref)).v)
    circle = _round(f, round_ref)
    return [side * _offset(circle.center, f.straight(line_ref)) - circle.radius]


def _tangent_circles(f: Frame, r: tuple[Ref, ...], at: Setting) -> list[Dual]:
    a0, b0 = _round(at.first, r[0]), _round(at.first, r[1])
    gap = _length(_sub(b0.center, a0.center)).v
    outside = abs(gap - (a0.radius.v + b0.radius.v))
    inside = abs(gap - abs(a0.radius.v - b0.radius.v))
    a, b = _round(f, r[0]), _round(f, r[1])
    distance = _length(_sub(b.center, a.center))
    if outside <= inside:
        return [distance - (a.radius + b.radius)]
    return [distance - _sign(a0.radius.v - b0.radius.v) * (a.radius - b.radius)]


def _equal_lengths(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    a, b = f.straight(r[0]), f.straight(r[1])
    return [_length(_direction(b)) - _length(_direction(a))]


def _equal_radii(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    return [_round(f, r[1]).radius - _round(f, r[0]).radius]


def _midpoint(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    curve = r[0]
    if curve.feature is Feature.CURVE:  # a line or arc: its own MID feature
        mid = f.point(Ref(entity=curve.entity, feature=Feature.MID))
    else:  # a rectangle side
        mid = _side_mid(f.straight(curve))
    px, py = f.point(r[1])
    return [px - mid[0], py - mid[1]]


def _side_mid(side: Straight) -> P:
    return (side.a[0] + side.b[0]) / 2, (side.a[1] + side.b[1]) / 2


def _mirror(p: P, q: P, axis: Straight) -> list[Dual]:
    """`q` is `p` reflected in `axis`: their midpoint on it, their chord square to it."""
    d = _direction(axis)
    mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    return [_offset(mid, axis), _dot(d, _sub(q, p)) / _length(d)]


def _symmetric_points(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    return _mirror(f.point(r[0]), f.point(r[1]), f.straight(r[2]))


def _symmetric_circles(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    a, b = _round(f, r[0]), _round(f, r[1])
    return [*_mirror(a.center, b.center, f.straight(r[2])), b.radius - a.radius]


def _fix(f: Frame, r: tuple[Ref, ...], at: Setting) -> list[Dual]:
    held = [v.v for v in at.anchor.defining(r[0])]
    return [now - then for now, then in zip(f.defining(r[0]), held, strict=True)]


def _normal(f: Frame, r: tuple[Ref, ...], _: Setting) -> list[Dual]:
    return [_offset(_round(f, r[0]).center, f.straight(r[1]))]


_ENDS = (Feature.START, Feature.END)


def _junction(frame: Frame, r: tuple[Ref, ...]) -> tuple[Feature, Feature]:
    """The pair of ends, one per curve, that are closest: where the two curves join."""
    pairs = [(fa, fb) for fa in _ENDS for fb in _ENDS]

    def gap(pair: tuple[Feature, Feature]) -> float:
        a = frame.point(Ref(entity=r[0].entity, feature=pair[0]))
        b = frame.point(Ref(entity=r[1].entity, feature=pair[1]))
        return math.hypot(b[0].v - a[0].v, b[1].v - a[1].v)

    return min(pairs, key=gap)


def _curvature_lines(f: Frame, r: tuple[Ref, ...], at: Setting) -> list[Dual]:
    ends = _junction(at.first, r)
    (ax, ay) = f.point(Ref(entity=r[0].entity, feature=ends[0]))
    (bx, by) = f.point(Ref(entity=r[1].entity, feature=ends[1]))
    return [bx - ax, by - ay, _sine(f.straight(r[0]), f.straight(r[1]))]


def _curvature_arcs(f: Frame, r: tuple[Ref, ...], at: Setting) -> list[Dual]:
    ends = _junction(at.first, r)

    def angle(frame: Frame, ref: Ref, end: Feature) -> Dual:
        arc = _round(frame, ref)
        assert arc.start is not None
        assert arc.sweep is not None
        return arc.start + arc.sweep if end is Feature.END else arc.start

    # Whole turns between the two end angles are fixed at the start, so the equation stays
    # smooth instead of wrapping at ±180°.
    turns = round((angle(at.first, r[1], ends[1]) - angle(at.first, r[0], ends[0])).v / 360.0)
    joined = angle(f, r[1], ends[1]) - angle(f, r[0], ends[0]) - 360.0 * turns
    return [*_same_circle(f, r, at), joined]


# --- Checks -----------------------------------------------------------------------------


def _not_rectangle_side(document: Document, refs: tuple[Ref, ...]) -> str | None:
    if isinstance(document.entities[refs[0].entity], Rectangle):
        return "a rectangle's sides are always horizontal or vertical already"
    return None


def _whole_lines(document: Document, refs: tuple[Ref, ...]) -> str | None:
    if any(not isinstance(document.entities[r.entity], Line) for r in refs):
        return "curvature joins the ends of lines or arcs; a rectangle side has no free end"
    if refs[0].entity == refs[1].entity:
        return "curvature joins two different curves"
    return None


def _different(document: Document, refs: tuple[Ref, ...]) -> str | None:
    return "curvature joins two different curves" if refs[0].entity == refs[1].entity else None


# --- The table --------------------------------------------------------------------------

SPECS: Mapping[ConstraintType, Spec] = {
    ConstraintType.COINCIDENT: Spec(
        "two points, a point and a curve, two lines, or two circles or arcs",
        (
            Rule((POINT, POINT), _coincident_points),
            Rule((CURVE, POINT), _point_on_curve),
            Rule((LINE, LINE), _collinear),
            Rule((ROUND, ROUND), _same_circle),
        ),
    ),
    ConstraintType.CONCENTRIC: Spec(
        "two circles or arcs, or a circle or arc and a point",
        (
            Rule((ROUND, ROUND), _concentric),
            Rule((ROUND, POINT), _concentric),
            Rule((POINT, ROUND), _concentric),
        ),
    ),
    ConstraintType.HORIZONTAL: Spec(
        "one line, or two points",
        (Rule((LINE,), _level(1), check=_not_rectangle_side), Rule((POINT, POINT), _level(1))),
    ),
    ConstraintType.VERTICAL: Spec(
        "one line, or two points",
        (Rule((LINE,), _level(0), check=_not_rectangle_side), Rule((POINT, POINT), _level(0))),
    ),
    ConstraintType.PARALLEL: Spec("two lines", (Rule((LINE, LINE), _parallel),)),
    ConstraintType.PERPENDICULAR: Spec("two lines", (Rule((LINE, LINE), _perpendicular),)),
    ConstraintType.TANGENT: Spec(
        "a line and a circle or arc, or two circles or arcs",
        (
            Rule((LINE, ROUND), _tangent_line),
            Rule((ROUND, LINE), _tangent_line),
            Rule((ROUND, ROUND), _tangent_circles),
        ),
    ),
    ConstraintType.EQUAL: Spec(
        "two lines, or two circles or arcs",
        (Rule((LINE, LINE), _equal_lengths), Rule((ROUND, ROUND), _equal_radii)),
    ),
    ConstraintType.MIDPOINT: Spec(
        "a line or arc and a point",
        (Rule((LINE | ARC, POINT), _midpoint),),
    ),
    ConstraintType.SYMMETRIC: Spec(
        "two points, or two circles or arcs, and the line to mirror them about",
        (
            Rule((POINT, POINT, LINE), _symmetric_points, mover=1),
            Rule((ROUND, ROUND, LINE), _symmetric_circles, mover=1),
        ),
    ),
    ConstraintType.FIX: Spec("one point or curve", (Rule((ANY,), _fix, mover=0),)),
    ConstraintType.NORMAL: Spec("a circle or arc and a line", (Rule((ROUND, LINE), _normal),)),
    ConstraintType.PIERCE: Spec(
        "a point and a 3D curve",
        (),
        unsupported="pierce places a point where a 3D curve crosses the sketch plane; "
        "3D curves referenced from outside the sketch don't exist yet",
    ),
    ConstraintType.CURVATURE: Spec(
        "two lines or two arcs that meet end to end",
        (
            Rule((LINE, LINE), _curvature_lines, check=_whole_lines),
            Rule((ARC, ARC), _curvature_arcs, check=_different),
            Rule(
                (LINE, ARC),
                None,
                impossible="a line's curvature is 0 and an arc's never is, so they can't "
                "match; this becomes useful with splines",
            ),
            Rule((ARC, LINE), None, impossible="a line's curvature is 0 and an arc's never is"),
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class Match:
    rule: Rule
    refs: tuple[Ref, ...]
    """In canonical order."""


def match(document: Document, type: ConstraintType, refs: tuple[Ref, ...]) -> Match | str:
    """The rule `refs` fit, with the references in canonical order, or why none does.

    The references' own order is kept when it fits, so for symmetric selections the last
    one given is the one that moves.
    """
    spec = SPECS[type]
    if spec.unsupported:
        return spec.unsupported
    kinds = [ref_kind(document, r) for r in refs]
    # Orders outermost, so the order given wins over any reordering, whichever rule fits.
    for order in permutations(range(len(refs))):
        for rule in spec.rules:
            if len(rule.kinds) != len(refs):
                continue
            if all(kinds[i] in allowed for i, allowed in zip(order, rule.kinds, strict=True)):
                ordered = tuple(refs[i] for i in order)
                if rule.equations is None:
                    return rule.impossible
                if rule.check is not None and (reason := rule.check(document, ordered)):
                    return reason
                return Match(rule, ordered)
    given = ", ".join(k.value for k in kinds) or "nothing"
    return f"{type.value} needs {spec.needs}; got {given}"


# --- Dimensions -------------------------------------------------------------------------


def measure(
    f: Frame, document: Document, dimension: DistanceDimension | RadialDimension, at: Setting
) -> Dual:
    """What a distance or radial dimension measures, in mm. Angles: `measure_angle`."""
    if isinstance(dimension, RadialDimension):
        radius = _round(f, Ref(entity=dimension.target, feature=Feature.CURVE)).radius
        return 2.0 * radius if dimension.measure is RadialMeasure.DIAMETER else radius
    a, b = dimension.a, dimension.b
    kind_a, kind_b = ref_kind(document, a), ref_kind(document, b)
    if kind_a is RefKind.POINT and kind_b is RefKind.POINT:
        p, q = f.point(a), f.point(b)
        if dimension.orientation is DistanceOrientation.ALIGNED:
            return _length(_sub(q, p))
        axis = 0 if dimension.orientation is DistanceOrientation.HORIZONTAL else 1
        p0, q0 = at.first.point(a), at.first.point(b)
        return _sign(q0[axis].v - p0[axis].v) * (q[axis] - p[axis])
    if kind_a is RefKind.POINT or kind_b is RefKind.POINT:
        point, line = (a, b) if kind_a is RefKind.POINT else (b, a)
        side = _sign(_offset(at.first.point(point), at.first.straight(line)).v)
        return side * _offset(f.point(point), f.straight(line))
    side = _sign(_offset(_side_mid(at.first.straight(b)), at.first.straight(a)).v)
    return side * _offset(_side_mid(f.straight(b)), f.straight(a))


def measure_angle(f: Frame, a: Ref, b: Ref, supplementary: bool, at: Setting) -> Dual:
    """Degrees between two straight curves' directions, or 180 minus that."""
    da, db = _direction(f.straight(a)), _direction(f.straight(b))
    first_a, first_b = _direction(at.first.straight(a)), _direction(at.first.straight(b))
    turn = _sign(_cross(first_a, first_b).v)
    angle = turn * atan2(_cross(da, db), _dot(da, db)) * (180.0 / math.pi)
    return 180.0 - angle if supplementary else angle
