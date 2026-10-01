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
the same size (equal radius, equal and parallel line, equal sides). An arc's two ends are
joined that way, and its centre through a construction point joined the same way, the copy's
centre level with it across the chord (the way mirror holds an arc: a copied radius and chord
can't hold a semicircle, whose chord is its diameter).

What places a circular pattern's copies, around a centre point: each point of the originals
(a point, a line's ends, a circle's centre) and its copies make an orbit, the same point turned
one step at a time. The orbit's copies sit on a construction circle through the original,
concentric with the centre, and are joined by construction chords, all equal. Round a full
circle that fixes the step, so the count alone sets the spacing; a partial pattern's step is
one angle dimension between two radial construction lines. A copy's point that lands where
another point already is (the ends of a star's edges, which meet at the next copy's corner) is
joined to it with a coincident constraint instead, so outlines close, and each orbit is placed
once. Circles are the same size as the original. An arc's ends have orbits, and its centre
one through a construction point per copy, the copy's centre level with it across the chord.
Rectangles aren't turned: a rectangle is always axis-aligned. The originals should be fully
constrained first, so that their points are exactly a step apart where they should meet.
"""

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from caliper.ai.model import ToolSpec
from caliper.contracts.commands import (
    Applied,
    Command,
    CreateAngleDimension,
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
from caliper.engine import part
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
        "Repeat geometry in a row, or a grid with count2: points, lines, circles, arcs, and "
        "rectangles (a slot's lines and arcs together, say). Counts include the original. "
        "Each copy is the same size as the original (equal radius, equal and parallel line, "
        "equal sides; an arc by its ends and centre) and is joined to the previous "
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


CIRCULAR = ToolSpec(
    name="circular_pattern",
    description=(
        "Repeat geometry around a centre point: points, lines, circles, and arcs (a gear's "
        "teeth, a star's points). count includes the "
        "original; the copies are evenly spaced over angle, 360 by default (a full circle), "
        "counter-clockwise, or clockwise for a negative angle. Copies of each point sit on a "
        "construction circle, joined by equal construction chords, so a full circle's spacing "
        "comes from the count and a partial one has one angle dimension. Points that land on "
        "the same spot, such as the joints of a star's edges, are joined, so outlines close. "
        "Constrain the original fully first (a star: one point's two edges, their inner ends "
        "exactly one step apart), then pattern it. One call, and one step to undo."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ids": {**_IDS, "description": "The geometry to repeat."},
            "center": {
                "description": "The point to turn about: a point's id, or a point feature.",
                "anyOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "properties": {
                            "entity": {"type": "string"},
                            "feature": {"type": "string", "enum": [f.value for f in Feature]},
                        },
                        "required": ["entity", "feature"],
                        "additionalProperties": False,
                    },
                ],
            },
            "count": {"type": "integer", "minimum": 2},
            "angle": {"type": "number", "description": "The whole pattern's angle. Default 360."},
        },
        "required": ["ids", "center", "count"],
        "additionalProperties": False,
    },
)

JOIN = 1e-6
"""Relative to the geometry's size: a copy's point this close to another point is that point,
and is joined to it. Solved geometry is exact to far better; this allows for round-off in the
turn."""
NEAR = 1e-3
"""Relative to the geometry's size: points this close, but not within `JOIN`, almost meet, and
the result says so: the original isn't exactly one step from itself."""


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
    created: list[JSON] = field(default_factory=list)
    """New geometry that copies nothing (`caliper.ai.construct`), in order."""
    construction: list[JSON] = field(default_factory=list)
    """Layout geometry made to hold the copies."""
    constraints: list[JSON] = field(default_factory=list)
    dimensions: list[JSON] = field(default_factory=list)
    skipped: dict[str, JSON] = field(default_factory=dict)
    note: str | None = None


_DRAWS = (CreatePoint, CreateLine, CreateCircle, CreateArc, CreateRectangle)


class _Builder:
    """Runs what a repeat makes. Copies and layout geometry go in the originals' sketch
    (ADR 0011), which a part with several sketches needs said."""

    def __init__(self, run: Run, made: Repeated, sketch: EntityId | None) -> None:
        self._run = run
        self.made = made
        self._sketch = sketch

    def create(self, command: Command) -> EntityId:
        if isinstance(command, _DRAWS) and command.sketch is None:
            command = replace(command, sketch=self._sketch)
        return self._run(command).created_ids[0]

    def layout(self, command: Command) -> EntityId:
        id = self.create(command)
        self.made.construction.append(id)
        return id

    def constrain(self, type: ConstraintType, *refs: Ref) -> None:
        self.made.constraints.append(self.create(CreateConstraint(type=type, refs=refs)))


def _ref(id: EntityId, feature: Feature) -> Ref:
    return Ref(entity=id, feature=feature)


def _one_sketch(document: Document, ids: Iterable[EntityId]) -> EntityId | None:
    """The sketch everything a repeat reads is in: a repeat stays inside one sketch."""
    ids = list(ids)
    found = sorted({s for id in ids if (s := part.sketch_of(document, id)) is not None})
    if len(found) > 1:
        raise PatternError(
            {
                "error": f"{', '.join(ids)} are in more than one sketch ({', '.join(found)}); "
                "repeat one sketch's geometry at a time"
            }
        )
    return EntityId(found[0]) if found else None


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

    build = _Builder(run, made, _one_sketch(document, [EntityId(axis_id), *(i for i, _ in chosen)]))
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
                # Across the copy's chord, which a slanted axis turns from the original's.
                mirrored = Arc(
                    center=center,
                    radius=entity.radius,
                    start_angle=start,
                    sweep_angle=entity.sweep_angle,
                )
                build.constrain(
                    _across(mirrored), _ref(held, Feature.POINT), _ref(copy, Feature.CENTER)
                )
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
    seeds: list[tuple[EntityId, Point | Line | Circle | Arc | Rectangle]] = []
    for id in ids:
        entity = document.entities.get(id)
        if entity is None:
            raise PatternError({"error": f"no entity {id!r}"})
        if isinstance(entity, Point | Line | Circle | Arc | Rectangle):
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
    build = _Builder(run, made, _one_sketch(document, (id for id, _ in seeds)))
    masters: dict[int, EntityId] = {}

    def link(before: Ref, after: Ref, start: Point2, end: Point2, way: int) -> None:
        """A construction line from a point of the copy before to the same point of this
        one: the first in each direction carries the spacing, the rest are equal and parallel
        to it."""
        joint = build.layout(CreateLine(start=start, end=end, construction=True))
        build.constrain(ConstraintType.COINCIDENT, before, _ref(joint, Feature.START))
        build.constrain(ConstraintType.COINCIDENT, after, _ref(joint, Feature.END))
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

    for id, seed in seeds:
        at: dict[tuple[int, int], EntityId] = {(0, 0): id}
        centres: dict[tuple[int, int], Ref] = {(0, 0): _ref(id, Feature.CENTER)}
        for i, j in places:
            shift = (
                i * steps[1][0] + j * steps[2][0],
                i * steps[1][1] + j * steps[2][1],
            )
            copy = build.create(_moved(seed, shift))
            way = 1 if j == 0 else 2
            before = (i - 1, j) if j == 0 else (i, j - 1)
            back = (shift[0] - steps[way][0], shift[1] - steps[way][1])
            for feature in _CHAINED[type(seed)]:
                link(
                    _ref(at[before], feature),
                    _ref(copy, feature),
                    _shifted(_point(seed, feature), back),
                    _shifted(_point(seed, feature), shift),
                    way,
                )
            if isinstance(seed, Arc):
                # Its ends leave the centre free along the chord's bisector: a construction
                # point follows the original's centre, and the copy's is level with it across.
                where = _shifted(seed.center, shift)
                held = build.layout(CreatePoint(position=where, construction=True))
                link(
                    centres[before],
                    _ref(held, Feature.POINT),
                    _shifted(seed.center, back),
                    where,
                    way,
                )
                build.constrain(
                    _across(seed), _ref(held, Feature.POINT), _ref(copy, Feature.CENTER)
                )
                centres[(i, j)] = _ref(held, Feature.POINT)
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


_CHAINED: Mapping[type, tuple[Feature, ...]] = {
    Point: (Feature.POINT,),
    Circle: (Feature.CENTER,),
    Line: (Feature.START,),
    Rectangle: (Feature.BOTTOM_LEFT,),
    Arc: (Feature.START, Feature.END),
}
"""The points of each kind of geometry that a linear pattern's construction lines join. An
arc's centre is joined through a construction point (see `linear_pattern`)."""


def _shifted(p: Point2, by: tuple[float, float]) -> Point2:
    return Point2(x=p.x + by[0], y=p.y + by[1])


def _across(arc: Arc) -> ConstraintType:
    """What holds an arc's centre, once its ends are placed, to a point it should be level
    with: the centre is free along the chord's perpendicular bisector, so the same x where
    that runs sideways, the same y where it runs up and down."""
    chord = _arc_chord(arc)
    bisector = (-chord[1], chord[0])
    return (
        ConstraintType.VERTICAL
        if abs(bisector[0]) >= abs(bisector[1])
        else ConstraintType.HORIZONTAL
    )


def _moved(seed: Point | Line | Circle | Arc | Rectangle, by: tuple[float, float]) -> Command:
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
        case Arc():
            return CreateArc(
                center=move(seed.center),
                radius=seed.radius,
                start_angle=seed.start_angle,
                sweep_angle=seed.sweep_angle,
                construction=seed.construction,
            )


def _same_size(
    build: _Builder, id: EntityId, seed: Point | Line | Circle | Arc | Rectangle, copy: EntityId
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
        case Point() | Arc():
            pass  # an arc's ends and centre, placed, fix its size


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


# --- Circular pattern -------------------------------------------------------------------


_TURNED: Mapping[type, tuple[Feature, ...]] = {
    Point: (Feature.POINT,),
    Line: (Feature.START, Feature.END),
    Circle: (Feature.CENTER,),
    Arc: (Feature.START, Feature.END),
}
"""The points that place each kind of geometry a circular pattern turns. An arc's centre is
placed through a construction point (see `circular_pattern`)."""


def _orbits(seed: Point | Line | Circle | Arc) -> tuple[Feature, ...]:
    """The points of a seed that have orbits: its turned points, and an arc's centre."""
    return (*_TURNED[type(seed)], *((Feature.CENTER,) if isinstance(seed, Arc) else ()))


def circular_pattern(document: Document, arguments: Mapping[str, object], run: Run) -> Repeated:
    ids = _ids(arguments.get("ids"))
    center, c = _center(document, arguments.get("center"))
    count = _count(arguments, "count", None)
    total = _angle(arguments, "angle", 360.0)
    if count < 2:
        raise PatternError({"error": "count must be 2 or more: it includes the original"})
    if total == 0 or abs(total) > 360:
        raise PatternError({"error": "angle must be more than 0 and at most 360 either way"})
    full = abs(total) == 360
    step = total / count if full else total / (count - 1)
    if not full and abs(step) >= 180:
        raise PatternError(
            {
                "error": f"the copies would be {abs(step):g}° apart; a partial pattern's must be "
                "under 180°. Use more copies or a smaller angle"
            }
        )
    made = Repeated(label="Circular Pattern")

    def turn(p: Point2, times: int) -> Point2:
        t = math.radians(step * times)
        dx, dy = p.x - c.x, p.y - c.y
        return Point2(
            x=c.x + dx * math.cos(t) - dy * math.sin(t), y=c.y + dx * math.sin(t) + dy * math.cos(t)
        )

    seeds: list[tuple[EntityId, Point | Line | Circle | Arc]] = []
    for id in ids:
        entity = document.entities.get(id)
        if entity is None:
            raise PatternError({"error": f"no entity {id!r}"})
        if isinstance(entity, Rectangle):
            raise PatternError(
                {
                    "error": f"{id} is a rectangle, which is always axis-aligned, so it can't "
                    "be turned; draw it as four lines"
                }
            )
        if not isinstance(entity, Point | Line | Circle | Arc):
            made.skipped[id] = f"a {entity.kind}: each copy is placed instead"
        elif all(
            _same(turn(_point(entity, f), 1), _point(entity, f)) for f in _TURNED[type(entity)]
        ):
            made.skipped[id] = "at the centre: it is its own copy"
        else:
            seeds.append((id, entity))
    if not seeds:
        raise PatternError({"error": "nothing to pattern", "skipped": dict(made.skipped)})
    copies = (count - 1) * len(seeds)
    if copies > MAX_COPIES:
        raise PatternError(
            {"error": f"that's {copies} copies; one call makes at most {MAX_COPIES}. Split it"}
        )
    size = max(
        [1.0, abs(c.x), abs(c.y)]
        + [abs(v) for _, e in seeds for f in _orbits(e) for v in _xy(_point(e, f))]
    )

    # Every point's owner: the first point placed at its spot, original or copy.
    owners: list[tuple[Point2, Ref, bool]] = []  # (where, which, an original's)
    near: set[str] = set()

    def owner(at: Point2) -> tuple[Ref, bool] | None:
        for where, ref, original in owners:
            gap = math.hypot(where.x - at.x, where.y - at.y)
            if gap <= JOIN * size:
                return ref, original
            if gap <= NEAR * size:
                near.add(f"{ref.entity}.{ref.feature.value}")
        return None

    for id, seed in seeds:
        for feature in _orbits(seed):
            if owner(_point(seed, feature)) is None:
                owners.append((_point(seed, feature), _ref(id, feature), True))

    ids_read = [center.entity, *(id for id, _ in seeds)]
    build = _Builder(run, made, _one_sketch(document, ids_read))
    joins: list[tuple[Ref, Ref]] = []
    at_copy: dict[EntityId, list[EntityId]] = {id: [] for id, _ in seeds}
    levels: list[tuple[Ref, EntityId, ConstraintType]] = []  # an arc copy's centre, across
    for times in range(1, count):
        for id, seed in seeds:
            turned = _turned(seed, times, turn, step)
            copy = build.create(turned)
            at_copy[id].append(copy)
            points = [(turn(_point(seed, f), times), _ref(copy, f)) for f in _TURNED[type(seed)]]
            if isinstance(seed, Arc):
                # Its ends leave the centre free along the chord's bisector: a construction
                # point takes the centre's place in its orbit, and the copy's centre is level
                # with it across.
                assert isinstance(turned, CreateArc)
                held = build.layout(CreatePoint(position=turned.center, construction=True))
                points.append((turned.center, _ref(held, Feature.POINT)))
                copied = Arc(
                    center=turned.center,
                    radius=turned.radius,
                    start_angle=turned.start_angle,
                    sweep_angle=turned.sweep_angle,
                )
                levels.append((_ref(held, Feature.POINT), copy, _across(copied)))
            for where, mine in points:
                found = owner(where)
                if found is None:
                    owners.append((where, mine, False))
                else:
                    joins.append((found[0], mine))
    made.copies = {id: list(copies_) for id, copies_ in at_copy.items()}

    # Each orbit placed once: its copies' owners on a circle, joined by equal chords.
    chained: set[Ref] = set()
    for _, seed in seeds:
        for feature in _orbits(seed):
            start = _point(seed, feature)
            if _same(turn(start, 1), start):
                continue  # at the centre: every copy of it is joined to it
            ring = [owner(turn(start, n)) for n in range(count)]
            assert all(r is not None for r in ring)
            vertices = [(r[0], r[1]) for r in ring if r is not None]
            if all(original or ref in chained for ref, original in vertices):
                continue
            _orbit(build, center, c, start, vertices, full=full, step=step)
            chained.update(ref for ref, _ in vertices)
    for placed_ref, joined in joins:
        build.constrain(ConstraintType.COINCIDENT, placed_ref, joined)
    for held_ref, copy, across in levels:
        build.constrain(across, held_ref, _ref(copy, Feature.CENTER))
    for id, seed in seeds:
        if isinstance(seed, Circle):
            for circle in at_copy[id]:
                build.constrain(
                    ConstraintType.EQUAL, _ref(id, Feature.CURVE), _ref(circle, Feature.CURVE)
                )
    if near:
        made.note = (
            "Some copies almost meet other points but aren't joined, near "
            f"{', '.join(sorted(near))}: the original isn't exactly one step from where its "
            "copies should meet. Constrain the original so it is, undo, and pattern it again."
        )
    return made


def _orbit(
    build: _Builder,
    center: Ref,
    c: Point2,
    start: Point2,
    vertices: list[tuple[Ref, bool]],
    *,
    full: bool,
    step: float,
) -> None:
    """Place one orbit's copies: `vertices` are its points turned 0, 1, ... steps, each with
    whether it's an original's (fixed by the user's constraints, never by these)."""
    n = len(vertices)
    fixed = [original for _, original in vertices]
    free = n - sum(fixed)
    if not free:
        return
    # The originals' points in the orbit must run on from the first, or the chords between
    # them would state the step twice.
    run_end = 0
    while run_end + 1 < n and fixed[run_end + 1]:
        run_end += 1
    wrapped = [i for i in range(run_end + 1, n) if fixed[i]]
    if wrapped and not (full and wrapped == list(range(wrapped[0], n))):
        raise PatternError(
            {
                "error": "the originals overlap their own copies: some of their points are "
                "several steps apart round the circle. Pattern one repeat of the feature"
            }
        )
    first_free = run_end + 1
    last_free = (wrapped[0] - 1) if wrapped else n - 1

    def at(i: int) -> Point2:
        t = math.radians(step * i)
        dx, dy = start.x - c.x, start.y - c.y
        return Point2(
            x=c.x + dx * math.cos(t) - dy * math.sin(t), y=c.y + dx * math.sin(t) + dy * math.cos(t)
        )

    def chord(i: int, j: int) -> EntityId:
        line = build.layout(CreateLine(start=at(i), end=at(j), construction=True))
        build.constrain(ConstraintType.COINCIDENT, vertices[i][0], _ref(line, Feature.START))
        build.constrain(ConstraintType.COINCIDENT, vertices[j][0], _ref(line, Feature.END))
        return line

    if full and n == 2:  # half a turn: the copy is opposite, the centre halfway
        across = chord(0, 1)
        build.constrain(ConstraintType.MIDPOINT, _ref(across, Feature.CURVE), center)
        return
    radius = math.hypot(start.x - c.x, start.y - c.y)
    ring = build.layout(CreateCircle(center=c, radius=radius, construction=True))
    build.constrain(ConstraintType.CONCENTRIC, _ref(ring, Feature.CURVE), center)
    build.constrain(ConstraintType.COINCIDENT, _ref(ring, Feature.CURVE), vertices[0][0])
    for i in range(first_free, last_free + 1):
        build.constrain(ConstraintType.COINCIDENT, _ref(ring, Feature.CURVE), vertices[i][0])
    # The chords that touch a copy: from the last original round to the next original (a full
    # circle) or to the end (a partial one).
    ends = [(i, i + 1) for i in range(run_end, last_free)]
    if full:
        ends.append((last_free, (last_free + 1) % n))
    chords = [chord(i, j) for i, j in ends]
    reference = chords[0]
    if not full and run_end > 0:  # the originals set the step: match the copies to it
        reference = chord(run_end - 1, run_end)
        others = chords
    else:
        others = chords[1:]
    for line in others:
        build.constrain(
            ConstraintType.EQUAL, _ref(reference, Feature.CURVE), _ref(line, Feature.CURVE)
        )
    if not full and run_end == 0:  # the step itself: one angle, between two radii
        radii = []
        for i in (0, 1):
            radial = build.layout(CreateLine(start=c, end=at(i), construction=True))
            build.constrain(ConstraintType.COINCIDENT, center, _ref(radial, Feature.START))
            build.constrain(ConstraintType.COINCIDENT, vertices[i][0], _ref(radial, Feature.END))
            radii.append(radial)
        build.made.dimensions.append(
            build.create(
                CreateAngleDimension(
                    a=_ref(radii[0], Feature.CURVE),
                    b=_ref(radii[1], Feature.CURVE),
                    offset=0.6 * radius,
                    value=abs(step),
                )
            )
        )


def _turned(
    seed: Point | Line | Circle | Arc,
    times: int,
    turn: Callable[[Point2, int], Point2],
    step: float,
) -> Command:
    turn_by = step * times

    def by(p: Point2) -> Point2:
        return turn(p, times)

    match seed:
        case Point():
            return CreatePoint(position=by(seed.position), construction=seed.construction)
        case Line():
            return CreateLine(
                start=by(seed.start), end=by(seed.end), construction=seed.construction
            )
        case Circle():
            return CreateCircle(
                center=by(seed.center), radius=seed.radius, construction=seed.construction
            )
        case Arc():
            return CreateArc(
                center=by(seed.center),
                radius=seed.radius,
                start_angle=(seed.start_angle + turn_by) % 360.0,
                sweep_angle=seed.sweep_angle,
                construction=seed.construction,
            )


def _point(entity: object, feature: Feature) -> Point2:
    """Where a point feature is, for the features patterns use."""
    match entity, feature:
        case Point(), Feature.POINT:
            return entity.position
        case Line(), Feature.START:
            return entity.start
        case Line(), Feature.END:
            return entity.end
        case Line(), Feature.MID:
            return Point2(
                x=(entity.start.x + entity.end.x) / 2, y=(entity.start.y + entity.end.y) / 2
            )
        case Circle() | Arc(), Feature.CENTER:
            return entity.center
        case Arc(), Feature.START | Feature.END | Feature.MID:
            ends = _arc_ends(entity)
            if feature is Feature.MID:
                t = math.radians(entity.start_angle + entity.sweep_angle / 2)
                return Point2(
                    x=entity.center.x + entity.radius * math.cos(t),
                    y=entity.center.y + entity.radius * math.sin(t),
                )
            return ends[0] if feature is Feature.START else ends[1]
        case Rectangle(), _ if feature in _CORNERS:
            dx, dy = _CORNERS[feature]
            return Point2(
                x=entity.corner.x + dx * entity.width, y=entity.corner.y + dy * entity.height
            )
    raise PatternError(
        {"error": f"{feature.value} is not a point of a {getattr(entity, 'kind', '?')}"}
    )


_CORNERS = {
    Feature.BOTTOM_LEFT: (0.0, 0.0),
    Feature.BOTTOM_RIGHT: (1.0, 0.0),
    Feature.TOP_RIGHT: (1.0, 1.0),
    Feature.TOP_LEFT: (0.0, 1.0),
    Feature.CENTER: (0.5, 0.5),
}


def _center(document: Document, value: object) -> tuple[Ref, Point2]:
    if isinstance(value, str):
        value = {"entity": value, "feature": Feature.POINT.value}
    if not isinstance(value, Mapping) or set(value) != {"entity", "feature"}:
        raise PatternError({"error": "center must be a point's id, or an entity and feature"})
    entity_id, feature = value["entity"], value["feature"]
    entity = document.entities.get(EntityId(str(entity_id)))
    if entity is None:
        raise PatternError({"error": f"no entity {entity_id!r}"})
    if feature not in {f.value for f in Feature}:
        raise PatternError({"error": f"center names no feature: {feature!r}"})
    ref = _ref(EntityId(str(entity_id)), Feature(feature))
    return ref, _point(entity, ref.feature)


def _xy(p: Point2) -> tuple[float, float]:
    return p.x, p.y


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
