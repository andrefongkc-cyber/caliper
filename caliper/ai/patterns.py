"""Mirror and linear pattern: repeating geometry in one tool call.

Both are made of Caliper's own commands, the ones a model could send one by one: a create
command for each copy, then the constraints and dimensions that tie each copy to its original,
so the copies follow when the original or the layout changes. Nothing reaches the document
except through the bus, so the user reviews, accepts, and undoes the result like any other
change. They save the model one call per copy and per constraint: the 002 plate's star took
about 70 calls for its second half, and its hole grid about 60.

What ties a mirrored copy to its original, about a line (the axis):
- a point: symmetric (the point and its copy about the axis);
- a line: its ends symmetric;
- a circle: symmetric circles (centres mirrored, radii equal);
- an arc: its ends symmetric, start to end (a mirror reverses an arc's direction), and its
  centre level with a construction point mirrored from the original's centre, horizontally or
  vertically, whichever the arc's shape needs. Symmetric arcs would fix the radius again after
  the ends have, which Caliper rejects as redundant;
- a rectangle: two opposite corners symmetric. Rectangles are always axis-aligned, so only
  about a horizontal or vertical axis.
Geometry that is its own mirror image (on the axis) is skipped.

What ties a linear pattern's copy to the original: its anchor (a point, a circle's centre, a
line's start, a rectangle's bottom-left corner) is joined to the previous copy's by a
construction line equal and parallel to the pattern's first such line, which carries the
spacing as a driving dimension and is horizontal or vertical when the direction is; and it is
the same size (equal radius, equal and parallel line, equal sides). Arcs aren't patterned:
no constraint ties a copy's angles to its original's.
"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from caliper.ai.model import ToolSpec
from caliper.contracts.commands import (
    Applied,
    Command,
    CreateArc,
    CreateCircle,
    CreateConstraint,
    CreateDistanceDimension,
    CreateLine,
    CreatePoint,
    CreateRectangle,
)
from caliper.contracts.document import (
    Arc,
    Circle,
    ConstraintType,
    DistanceOrientation,
    Document,
    EntityId,
    Feature,
    Line,
    Point,
    Point2,
    Rectangle,
    Ref,
)
from caliper.engine.io.canonical import JSON

Run = Callable[[Command], Applied]
"""Executes one command on the workspace's bus; raises when the bus rejects it."""

MAX_COPIES = 100
"""The most copies one call makes. Each copy is several commands, each solved, on the UI
thread: a runaway count would freeze the window."""

LABEL_OFFSET = 10.0
"""How far a pattern's spacing dimension sits from its line, at most (mm): just outside the
row, as docs/cad-practices.md asks."""

SAME = 1e-9
"""Relative to the geometry's size: a copy this close to its original is the original."""

_IDS = {"type": "array", "items": {"type": "string"}, "minItems": 1}

MIRROR = ToolSpec(
    name="mirror_entities",
    description=(
        "Mirror geometry about a line: points, lines, circles, arcs, and rectangles (a "
        "rectangle only about a horizontal or vertical line). Each copy is tied to its "
        "original by symmetric constraints, so it follows the original and the line, and needs "
        "no constraints or dimensions of its own. Draw and constrain one half of a symmetric "
        "feature, end it on the line, then mirror it: the halves meet on the line by "
        "themselves. Geometry on the line is skipped. One call, and one step to undo."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ids": {**_IDS, "description": "The geometry to mirror."},
            "axis": {"type": "string", "description": "The line to mirror about."},
        },
        "required": ["ids", "axis"],
        "additionalProperties": False,
    },
)

PATTERN = ToolSpec(
    name="linear_pattern",
    description=(
        "Repeat geometry in a row, or a grid with count2: points, lines, circles, and "
        "rectangles. Counts include the original. Each copy is the same size as the original "
        "(equal radius, equal and parallel line, equal sides) and is joined to the previous "
        "one by a construction line equal and parallel to the first, which gets a driving "
        "dimension of the spacing and is horizontal or vertical when the direction is. So one "
        "dimension respaces the pattern and the original's size drives every copy: dimension "
        "the original, then pattern it. Angles are degrees from +x. One call, and one step to "
        "undo."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ids": {**_IDS, "description": "The geometry to repeat."},
            "count": {"type": "integer", "minimum": 1, "description": "Along the first direction."},
            "spacing": {"type": "number", "exclusiveMinimum": 0},
            "angle": {"type": "number", "description": "The first direction. Default 0."},
            "count2": {"type": "integer", "minimum": 1, "description": "Along the second."},
            "spacing2": {"type": "number", "exclusiveMinimum": 0},
            "angle2": {"type": "number", "description": "Default angle + 90."},
        },
        "required": ["ids", "count", "spacing"],
        "additionalProperties": False,
    },
)


class PatternError(Exception):
    """A mirror or pattern that can't be made as asked. Nothing is left of it."""

    def __init__(self, content: JSON) -> None:
        super().__init__(str(content))
        self.content = content


@dataclass
class Repeated:
    """What a mirror or pattern made, for the tool's result."""

    label: str
    copies: dict[str, JSON] = field(default_factory=dict)
    """Each original's copy (mirror) or copies in order (pattern)."""
    construction: list[JSON] = field(default_factory=list)
    """Layout geometry made to hold the copies."""
    constraints: list[JSON] = field(default_factory=list)
    dimensions: list[JSON] = field(default_factory=list)
    skipped: dict[str, JSON] = field(default_factory=dict)
    note: str | None = None


class _Builder:
    def __init__(self, run: Run, made: Repeated) -> None:
        self._run = run
        self.made = made

    def create(self, command: Command) -> EntityId:
        return self._run(command).created_ids[0]

    def layout(self, command: Command) -> EntityId:
        id = self.create(command)
        self.made.construction.append(id)
        return id

    def constrain(self, type: ConstraintType, *refs: Ref) -> None:
        self.made.constraints.append(self.create(CreateConstraint(type=type, refs=refs)))


def _ref(id: EntityId, feature: Feature) -> Ref:
    return Ref(entity=id, feature=feature)


# --- Mirror -----------------------------------------------------------------------------


def mirror(document: Document, arguments: Mapping[str, object], run: Run) -> Repeated:
    ids = _ids(arguments.get("ids"))
    axis_id = arguments.get("axis")
    if not isinstance(axis_id, str):
        raise PatternError({"error": "axis must be the id of a line"})
    axis = document.entities.get(EntityId(axis_id))
    if not isinstance(axis, Line):
        what = "not in the sketch" if axis is None else f"a {axis.kind}"
        raise PatternError({"error": f"axis must be a line; {axis_id} is {what}"})
    a, b = axis.start, axis.end
    length = math.hypot(b.x - a.x, b.y - a.y)
    if length == 0:
        raise PatternError({"error": f"axis {axis_id} has no length"})
    ux, uy = (b.x - a.x) / length, (b.y - a.y) / length
    level = abs(uy) <= SAME
    plumb = abs(ux) <= SAME

    def reflect(p: Point2) -> Point2:
        dx, dy = p.x - a.x, p.y - a.y
        along = dx * ux + dy * uy
        return Point2(x=a.x + 2 * along * ux - dx, y=a.y + 2 * along * uy - dy)

    turn = math.degrees(math.atan2(uy, ux))  # a direction at angle t mirrors to 2 * turn - t
    line = _ref(EntityId(axis_id), Feature.CURVE)
    made = Repeated(label="Mirror")
    chosen: list[tuple[EntityId, Point | Line | Circle | Arc | Rectangle]] = []
    for id in ids:
        entity = document.entities.get(id)
        if entity is None:
            raise PatternError({"error": f"no entity {id!r}"})
        if id == axis_id:
            made.skipped[id] = "the axis itself"
        elif not isinstance(entity, Point | Line | Circle | Arc | Rectangle):
            made.skipped[id] = f"a {entity.kind}: the copies are tied by symmetry instead"
        elif _on_axis(entity, reflect):
            made.skipped[id] = "on the axis: it is its own mirror image"
        elif isinstance(entity, Rectangle) and not (level or plumb):
            raise PatternError(
                {
                    "error": f"{id} is a rectangle, which is always axis-aligned, so it can only "
                    "be mirrored about a horizontal or vertical line; draw it as four lines"
                }
            )
        else:
            chosen.append((id, entity))
    if not chosen:
        raise PatternError({"error": "nothing to mirror", "skipped": dict(made.skipped)})

    build = _Builder(run, made)
    for id, entity in chosen:
        match entity:
            case Point():
                copy = build.create(
                    CreatePoint(position=reflect(entity.position), construction=entity.construction)
                )
                build.constrain(
                    ConstraintType.SYMMETRIC,
                    _ref(id, Feature.POINT),
                    _ref(copy, Feature.POINT),
                    line,
                )
            case Line():
                copy = build.create(
                    CreateLine(
                        start=reflect(entity.start),
                        end=reflect(entity.end),
                        construction=entity.construction,
                    )
                )
                for end in (Feature.START, Feature.END):
                    build.constrain(ConstraintType.SYMMETRIC, _ref(id, end), _ref(copy, end), line)
            case Circle():
                copy = build.create(
                    CreateCircle(
                        center=reflect(entity.center),
                        radius=entity.radius,
                        construction=entity.construction,
                    )
                )
                build.constrain(
                    ConstraintType.SYMMETRIC,
                    _ref(id, Feature.CURVE),
                    _ref(copy, Feature.CURVE),
                    line,
                )
            case Arc():
                center = reflect(entity.center)
                start = (2 * turn - entity.start_angle - entity.sweep_angle) % 360.0
                copy = build.create(
                    CreateArc(
                        center=center,
                        radius=entity.radius,
                        start_angle=start,
                        sweep_angle=entity.sweep_angle,
                        construction=entity.construction,
                    )
                )
                # Mirrored, the original's start is the copy's end.
                build.constrain(
                    ConstraintType.SYMMETRIC, _ref(id, Feature.START), _ref(copy, Feature.END), line
                )
                build.constrain(
                    ConstraintType.SYMMETRIC, _ref(id, Feature.END), _ref(copy, Feature.START), line
                )
                # The ends leave the centre free along the chord's perpendicular bisector:
                # hold it level with the mirrored centre across that direction.
                held = build.layout(CreatePoint(position=center, construction=True))
                build.constrain(
                    ConstraintType.SYMMETRIC,
                    _ref(id, Feature.CENTER),
                    _ref(held, Feature.POINT),
                    line,
                )
                chord = _arc_chord(entity)
                across = (-chord[1], chord[0])  # the bisector's direction, mirrored or not
                level_with = (
                    ConstraintType.VERTICAL  # the same x: the bisector runs sideways
                    if abs(across[0]) >= abs(across[1])
                    else ConstraintType.HORIZONTAL
                )
                build.constrain(level_with, _ref(held, Feature.POINT), _ref(copy, Feature.CENTER))
            case Rectangle():
                corners = [
                    reflect(Point2(x=entity.corner.x + dx, y=entity.corner.y + dy))
                    for dx in (0.0, entity.width)
                    for dy in (0.0, entity.height)
                ]
                corner = Point2(x=min(p.x for p in corners), y=min(p.y for p in corners))
                copy = build.create(
                    CreateRectangle(
                        corner=corner,
                        width=entity.width,
                        height=entity.height,
                        construction=entity.construction,
                    )
                )
                # Opposite corners, each to the corner it lands on.
                pairs = (
                    (
                        (Feature.BOTTOM_LEFT, Feature.BOTTOM_RIGHT),
                        (Feature.TOP_RIGHT, Feature.TOP_LEFT),
                    )
                    if plumb
                    else (
                        (Feature.BOTTOM_LEFT, Feature.TOP_LEFT),
                        (Feature.TOP_RIGHT, Feature.BOTTOM_RIGHT),
                    )
                )
                for mine, theirs in pairs:
                    build.constrain(
                        ConstraintType.SYMMETRIC, _ref(id, mine), _ref(copy, theirs), line
                    )
        made.copies[id] = copy
    made.label = f"Mirror {len(made.copies)} {'Entity' if len(made.copies) == 1 else 'Entities'}"
    return made


def _arc_chord(arc: Arc) -> tuple[float, float]:
    a = math.radians(arc.start_angle)
    b = math.radians(arc.start_angle + arc.sweep_angle)
    return (arc.radius * (math.cos(b) - math.cos(a)), arc.radius * (math.sin(b) - math.sin(a)))


def _on_axis(
    entity: Point | Line | Circle | Arc | Rectangle, reflect: Callable[[Point2], Point2]
) -> bool:
    """Whether `entity` is its own mirror image."""
    match entity:
        case Point():
            return _same(reflect(entity.position), entity.position)
        case Line():
            s, e = reflect(entity.start), reflect(entity.end)
            return (_same(s, entity.start) and _same(e, entity.end)) or (
                _same(s, entity.end) and _same(e, entity.start)
            )
        case Circle():
            return _same(reflect(entity.center), entity.center)
        case Arc():
            ends = _arc_ends(entity)
            return _same(reflect(entity.center), entity.center) and _same(reflect(ends[0]), ends[1])
        case Rectangle():
            x, y, w, h = entity.corner.x, entity.corner.y, entity.width, entity.height
            corners = [Point2(x=x + dx, y=y + dy) for dx in (0.0, w) for dy in (0.0, h)]
            return all(any(_same(reflect(p), q) for q in corners) for p in corners)


def _arc_ends(arc: Arc) -> tuple[Point2, Point2]:
    def at(degrees: float) -> Point2:
        t = math.radians(degrees)
        return Point2(
            x=arc.center.x + arc.radius * math.cos(t), y=arc.center.y + arc.radius * math.sin(t)
        )

    return at(arc.start_angle), at(arc.start_angle + arc.sweep_angle)


def _same(p: Point2, q: Point2) -> bool:
    size = max(1.0, abs(p.x), abs(p.y), abs(q.x), abs(q.y))
    return math.hypot(p.x - q.x, p.y - q.y) <= SAME * size


# --- Linear pattern ---------------------------------------------------------------------


_ANCHORS = {
    Point: Feature.POINT,
    Circle: Feature.CENTER,
    Line: Feature.START,
    Rectangle: Feature.BOTTOM_LEFT,
}
"""The point of each kind of geometry that a pattern's construction lines join."""


def linear_pattern(document: Document, arguments: Mapping[str, object], run: Run) -> Repeated:
    ids = _ids(arguments.get("ids"))
    count = _count(arguments, "count", None)
    count2 = _count(arguments, "count2", 1)
    spacing = _spacing(arguments, "spacing")
    angle = _angle(arguments, "angle", 0.0)
    spacing2 = _spacing(arguments, "spacing2") if count2 > 1 else 0.0
    angle2 = _angle(arguments, "angle2", angle + 90.0)
    if count * count2 < 2:
        raise PatternError({"error": "count and count2 make no copies: one must be 2 or more"})
    first, second = _unit(angle), _unit(angle2)
    if count2 > 1 and abs(first[0] * second[1] - first[1] * second[0]) <= SAME:
        raise PatternError({"error": "angle and angle2 are parallel: the grid would be a row"})
    made = Repeated(label="Linear Pattern")
    seeds: list[tuple[EntityId, Point | Line | Circle | Rectangle]] = []
    for id in ids:
        entity = document.entities.get(id)
        if entity is None:
            raise PatternError({"error": f"no entity {id!r}"})
        if isinstance(entity, Arc):
            raise PatternError(
                {
                    "error": f"{id} is an arc, which can't be patterned: no constraint ties a "
                    "copy's angles to its original's. Pattern the lines and circles around it, "
                    "or mirror it"
                }
            )
        if isinstance(entity, Point | Line | Circle | Rectangle):
            seeds.append((id, entity))
        else:
            made.skipped[id] = f"a {entity.kind}: each copy is tied to its original instead"
    if not seeds:
        raise PatternError({"error": "nothing to pattern", "skipped": dict(made.skipped)})
    copies = (count * count2 - 1) * len(seeds)
    if copies > MAX_COPIES:
        raise PatternError(
            {"error": f"that's {copies} copies; one call makes at most {MAX_COPIES}. Split it"}
        )

    steps = {
        1: (first[0] * spacing, first[1] * spacing),
        2: (second[0] * spacing2, second[1] * spacing2),
    }
    # Each spacing's dimension goes on the side away from the rest of the grid.
    side = 1.0 if count2 == 1 or first[0] * second[1] - first[1] * second[0] > 0 else -1.0
    offsets = {1: -side * min(LABEL_OFFSET, spacing / 2), 2: side * min(LABEL_OFFSET, spacing2 / 2)}
    spacings = {1: spacing, 2: spacing2}
    directions = {1: _level(angle), 2: _level(angle2)}
    # Each copy after its predecessor: along the first row, then up each column.
    places = [(i, 0) for i in range(1, count)] + [
        (i, j) for i in range(count) for j in range(1, count2)
    ]
    build = _Builder(run, made)
    masters: dict[int, EntityId] = {}
    for id, seed in seeds:
        anchor = _ANCHORS[type(seed)]
        at: dict[tuple[int, int], EntityId] = {(0, 0): id}
        for i, j in places:
            shift = (
                i * steps[1][0] + j * steps[2][0],
                i * steps[1][1] + j * steps[2][1],
            )
            copy = build.create(_moved(seed, shift))
            before = at[(i - 1, j)] if j == 0 else at[(i, j - 1)]
            way = 1 if j == 0 else 2
            origin = _anchor_point(seed, (shift[0] - steps[way][0], shift[1] - steps[way][1]))
            joint = build.layout(
                CreateLine(start=origin, end=_anchor_point(seed, shift), construction=True)
            )
            build.constrain(
                ConstraintType.COINCIDENT, _ref(before, anchor), _ref(joint, Feature.START)
            )
            build.constrain(ConstraintType.COINCIDENT, _ref(copy, anchor), _ref(joint, Feature.END))
            master = masters.get(way)
            if master is None:
                masters[way] = joint
                if (held := directions[way]) is not None:
                    build.constrain(held, _ref(joint, Feature.CURVE))
                made.dimensions.append(
                    build.create(
                        CreateDistanceDimension(
                            a=_ref(joint, Feature.START),
                            b=_ref(joint, Feature.END),
                            orientation=DistanceOrientation.ALIGNED,
                            offset=offsets[way],
                            value=spacings[way],
                        )
                    )
                )
            else:
                build.constrain(
                    ConstraintType.EQUAL, _ref(master, Feature.CURVE), _ref(joint, Feature.CURVE)
                )
                build.constrain(
                    ConstraintType.PARALLEL, _ref(master, Feature.CURVE), _ref(joint, Feature.CURVE)
                )
            _same_size(build, id, seed, copy)
            at[(i, j)] = copy
        made.copies[id] = [at[place] for place in places]
    turning = [masters[way] for way in (1, 2) if way in masters and directions[way] is None]
    if turning:
        made.note = (
            f"The pattern's direction isn't held: {' and '.join(turning)} can turn. Hold it with "
            "an angle dimension to a line, or make it parallel to an edge."
        )
    return made


def _moved(seed: Point | Line | Circle | Rectangle, by: tuple[float, float]) -> Command:
    def move(p: Point2) -> Point2:
        return Point2(x=p.x + by[0], y=p.y + by[1])

    match seed:
        case Point():
            return CreatePoint(position=move(seed.position), construction=seed.construction)
        case Line():
            return CreateLine(
                start=move(seed.start), end=move(seed.end), construction=seed.construction
            )
        case Circle():
            return CreateCircle(
                center=move(seed.center), radius=seed.radius, construction=seed.construction
            )
        case Rectangle():
            return CreateRectangle(
                corner=move(seed.corner),
                width=seed.width,
                height=seed.height,
                construction=seed.construction,
            )


def _anchor_point(seed: Point | Line | Circle | Rectangle, by: tuple[float, float]) -> Point2:
    match seed:
        case Point():
            p = seed.position
        case Line():
            p = seed.start
        case Circle():
            p = seed.center
        case Rectangle():
            p = seed.corner
    return Point2(x=p.x + by[0], y=p.y + by[1])


def _same_size(
    build: _Builder, id: EntityId, seed: Point | Line | Circle | Rectangle, copy: EntityId
) -> None:
    match seed:
        case Circle():
            build.constrain(
                ConstraintType.EQUAL, _ref(id, Feature.CURVE), _ref(copy, Feature.CURVE)
            )
        case Line():
            build.constrain(
                ConstraintType.EQUAL, _ref(id, Feature.CURVE), _ref(copy, Feature.CURVE)
            )
            build.constrain(
                ConstraintType.PARALLEL, _ref(id, Feature.CURVE), _ref(copy, Feature.CURVE)
            )
        case Rectangle():
            for side in (Feature.BOTTOM, Feature.LEFT):
                build.constrain(ConstraintType.EQUAL, _ref(id, side), _ref(copy, side))
        case Point():
            pass


def _unit(degrees: float) -> tuple[float, float]:
    """The direction at `degrees` from +x, exact on the axes."""
    exact = {0.0: (1.0, 0.0), 90.0: (0.0, 1.0), 180.0: (-1.0, 0.0), 270.0: (0.0, -1.0)}
    turn = degrees % 360.0
    if turn in exact:
        return exact[turn]
    t = math.radians(degrees)
    return math.cos(t), math.sin(t)


def _level(degrees: float) -> ConstraintType | None:
    """The constraint that holds a line at `degrees`, if one does."""
    turn = degrees % 180.0
    if turn == 0.0:
        return ConstraintType.HORIZONTAL
    if turn == 90.0:
        return ConstraintType.VERTICAL
    return None


# --- Arguments --------------------------------------------------------------------------


def _ids(value: object) -> list[EntityId]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise PatternError({"error": "ids must be a list of entity ids"})
    return [EntityId(v) for v in dict.fromkeys(value)]  # once each, in order


def _count(arguments: Mapping[str, object], name: str, default: int | None) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PatternError({"error": f"{name} must be a whole number, 1 or more"})
    return value


def _spacing(arguments: Mapping[str, object], name: str) -> float:
    value = arguments.get(name)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise PatternError({"error": f"{name} must be a distance greater than 0"})
    return float(value)


def _angle(arguments: Mapping[str, object], name: str, default: float) -> float:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise PatternError({"error": f"{name} must be an angle in degrees"})
    return float(value)
