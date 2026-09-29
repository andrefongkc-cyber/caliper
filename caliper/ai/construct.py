"""Drawing in fewer calls: an arc through three points, and an outline of joined segments.

Both are made of Caliper's own commands, the ones a model could send one by one: `CreateArc`,
its centre worked out from the three points (known issue AI-9: tracing a drawing meant working
out every arc's centre and angles outside Caliper), and `CreateLine` and `CreateArc` joined end
to end by coincident constraints (AI-10: a traced outline took one call per joint on top of one
per segment). Like mirror and the patterns (`caliper.ai.patterns`), each call is kept or
undone as a whole.

An arc's direction: Caliper's arcs run counter-clockwise from their start. An arc through
three points that runs clockwise from the first to the last is stored the other way round, so
its `start` is the point given last; the result says so, and an outline joins the right ends.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

from caliper.ai.model import ToolSpec
from caliper.ai.patterns import PatternError, Repeated, Run
from caliper.contracts.commands import CreateArc, CreateConstraint, CreateLine
from caliper.contracts.document import (
    ConstraintType,
    Document,
    EntityId,
    Feature,
    Point2,
    Ref,
)

SAME = 1e-9
"""Relative to the points' size: points this close are one point; three points this close to a
line have no circle through them."""

_POINT = {
    "type": "object",
    "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
    "required": ["x", "y"],
    "additionalProperties": False,
}
_CONSTRUCTION = {
    "type": "boolean",
    "description": "True for layout geometry, as for the create tools. Default false.",
}

ARC_THROUGH = ToolSpec(
    name="create_arc_through_points",
    description=(
        "An arc from start, through a point on it, to end: Caliper works out its centre, "
        "radius, and angles. Arcs run counter-clockwise, so one that runs clockwise from start "
        "to end is stored from end to start; the result's `start` and `end` say where each of "
        "the arc's ends is. One call, and one step to undo."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "start": _POINT,
            "through": {**_POINT, "description": "Any point on the arc between its ends."},
            "end": _POINT,
            "construction": _CONSTRUCTION,
        },
        "required": ["start", "through", "end"],
        "additionalProperties": False,
    },
)

OUTLINE = ToolSpec(
    name="create_outline",
    description=(
        "A chain of lines and arcs through points, each segment's end joined to the next "
        "one's start by a coincident constraint: a traced outline in one call. A point with "
        "`through` ends an arc from the point before, through it; any other point ends a line. "
        "`tangent` on a point makes the two segments meeting there tangent (one of them must "
        "be an arc). `closed` joins the last point back to the first, with a line, or an arc "
        "through `close_through`. The result lists the segments in order. One call, and one "
        "step to undo."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "points": {
                "type": "array",
                "minItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "through": {
                            **_POINT,
                            "description": "Makes the segment ending here an arc through it.",
                        },
                        "tangent": {
                            "type": "boolean",
                            "description": "The segments meeting here are tangent.",
                        },
                    },
                    "required": ["x", "y"],
                    "additionalProperties": False,
                },
            },
            "closed": {"type": "boolean", "description": "Join the last point to the first."},
            "close_through": {
                **_POINT,
                "description": "With closed: the closing segment is an arc through this point.",
            },
            "construction": _CONSTRUCTION,
        },
        "required": ["points"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True, slots=True)
class Through:
    """An arc through three points, as `CreateArc` stores it."""

    center: Point2
    radius: float
    start_angle: float
    sweep_angle: float
    reversed: bool
    """It runs clockwise from the first point to the last, so it's stored from the last."""


def arc_through(start: Point2, through: Point2, end: Point2) -> Through | None:
    """The arc from `start` through `through` to `end`, or None if they're on one line or
    two of them are the same point."""
    size = max(1.0, *(abs(v) for p in (start, through, end) for v in (p.x, p.y)))
    (ax, ay), (bx, by), (cx, cy) = (start.x, start.y), (through.x, through.y), (end.x, end.y)
    if (
        min(
            math.dist((ax, ay), (bx, by)),
            math.dist((bx, by), (cx, cy)),
            math.dist((ax, ay), (cx, cy)),
        )
        <= SAME * size
    ):
        return None
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) <= SAME * size * size:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    radius = math.hypot(ax - ux, ay - uy)

    def angle(x: float, y: float) -> float:
        return math.degrees(math.atan2(y - uy, x - ux)) % 360.0

    first, middle, last = angle(ax, ay), angle(bx, by), angle(cx, cy)
    sweep = (last - first) % 360.0
    if (middle - first) % 360.0 <= sweep:  # counter-clockwise from start passes through
        return Through(Point2(x=ux, y=uy), radius, first, sweep, reversed=False)
    return Through(Point2(x=ux, y=uy), radius, last, 360.0 - sweep, reversed=True)


def arc_through_points(document: Document, arguments: Mapping[str, object], run: Run) -> Repeated:
    start, through, end = (
        _point(arguments.get(name), name) for name in ("start", "through", "end")
    )
    arc = arc_through(start, through, end)
    if arc is None:
        raise PatternError(
            {"error": "no arc goes through these points: they're on one line, or two are the same"}
        )
    made = Repeated(label="Create Arc")
    id = _create_arc(run, arc, _flag(arguments, "construction"))
    made.created.append(id)
    ends = (end, start) if arc.reversed else (start, end)
    made.note = (
        f"{id} runs counter-clockwise from ({_xy(ends[0])}) to ({_xy(ends[1])}): its start is "
        + (
            "the end you gave, since the arc runs clockwise from start to end"
            if arc.reversed
            else "the start you gave"
        )
    )
    return made


def outline(document: Document, arguments: Mapping[str, object], run: Run) -> Repeated:
    raw = arguments.get("points")
    if not isinstance(raw, list) or len(raw) < 2:
        raise PatternError({"error": "points must be a list of at least two points"})
    points = [_point(p, f"points[{n}]") for n, p in enumerate(raw)]
    closed = _flag(arguments, "closed")
    construction = _flag(arguments, "construction")
    # Each segment: from point k-1 to point k, a line or an arc through `through`.
    plans: list[tuple[Point2, Point2, Point2 | None]] = []
    for k in range(1, len(points)):
        spec = raw[k]
        assert isinstance(spec, Mapping)
        through = (
            None
            if spec.get("through") is None
            else _point(spec.get("through"), f"points[{k}].through")
        )
        plans.append((points[k - 1], points[k], through))
    closing = arguments.get("close_through")
    if closing is not None and not closed:
        raise PatternError({"error": "close_through needs closed: true"})
    if closed:
        through = None if closing is None else _point(closing, "close_through")
        if (
            len(points) < 3
            and through is None
            and all(isinstance(p, Mapping) and p.get("through") is None for p in raw)
        ):
            raise PatternError({"error": "a closed outline of lines needs at least three points"})
        plans.append((points[-1], points[0], through))
    for n, (a, b, _) in enumerate(plans):
        if math.dist((a.x, a.y), (b.x, b.y)) <= SAME * max(
            1.0, abs(a.x), abs(a.y), abs(b.x), abs(b.y)
        ):
            raise PatternError({"error": f"segment {n} starts and ends at the same point"})
    joints = list(range(1, len(plans))) + ([0] if closed else [])  # joint k: at point k
    flagged = [k for k, p in enumerate(raw) if isinstance(p, Mapping) and p.get("tangent") is True]
    if any(k not in joints for k in flagged):
        raise PatternError({"error": "tangent is for a point where two segments meet"})
    for k in flagged:  # the segment ending at point k, and the one starting there
        if plans[k - 1][2] is None and plans[k][2] is None:
            raise PatternError(
                {"error": f"points[{k}]: two lines meet there; tangency needs an arc"}
            )
    made = Repeated(label="Create Outline")
    # Each segment as made: its id, whether it's an arc, and its features at the from and to
    # points (an arc stored the other way round has them swapped).
    segments: list[tuple[EntityId, bool, Feature, Feature]] = []
    for n, (a, b, through) in enumerate(plans):
        if through is None:
            id = run(CreateLine(start=a, end=b, construction=construction)).created_ids[0]
            segments.append((id, False, Feature.START, Feature.END))
        else:
            arc = arc_through(a, through, b)
            if arc is None:
                raise PatternError({"error": f"segment {n}: no arc goes through its three points"})
            id = _create_arc(run, arc, construction)
            ends = (Feature.END, Feature.START) if arc.reversed else (Feature.START, Feature.END)
            segments.append((id, True, *ends))
        made.created.append(id)
    for k in joints:
        before, after = segments[k - 1], segments[k]
        made.constraints.append(
            run(
                CreateConstraint(
                    type=ConstraintType.COINCIDENT,
                    refs=(_ref(before[0], before[3]), _ref(after[0], after[2])),
                )
            ).created_ids[0]
        )
    for k in flagged:
        before, after = segments[k - 1], segments[k]
        made.constraints.append(
            run(
                CreateConstraint(
                    type=ConstraintType.TANGENT,
                    refs=(_ref(before[0], Feature.CURVE), _ref(after[0], Feature.CURVE)),
                )
            ).created_ids[0]
        )
    return made


def _create_arc(run: Run, arc: Through, construction: bool) -> EntityId:
    return run(
        CreateArc(
            center=arc.center,
            radius=arc.radius,
            start_angle=arc.start_angle,
            sweep_angle=arc.sweep_angle,
            construction=construction,
        )
    ).created_ids[0]


def _ref(id: EntityId, feature: Feature) -> Ref:
    return Ref(entity=id, feature=feature)


def _point(value: object, name: str) -> Point2:
    if not isinstance(value, Mapping):
        raise PatternError({"error": f"{name} must be a point, {{x, y}}"})
    x, y = value.get("x"), value.get("y")
    if not all(
        isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)
    ):
        raise PatternError({"error": f"{name} needs finite x and y"})
    return Point2(x=float(x), y=float(y))  # type: ignore[arg-type]


def _flag(arguments: Mapping[str, object], name: str) -> bool:
    value = arguments.get(name, False)
    if not isinstance(value, bool):
        raise PatternError({"error": f"{name} must be true or false"})
    return value


def _xy(p: Point2) -> str:
    return f"{p.x:g}, {p.y:g}"
