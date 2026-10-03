"""The part around the sketches (ADR 0011): its features, which sketch an entity is in, and
the rule that 2D work stays inside one sketch.

A sketch is a feature, in `Document.features`, never an entity. Geometry names its sketch;
a dimension or constraint is in the sketch of the geometry it refers to, all of which is in
one sketch; a check belongs to the part.
"""

from collections.abc import Iterable
from dataclasses import replace
from types import MappingProxyType

from caliper.contracts.commands import (
    Command,
    CreateArc,
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreatePoint,
    CreateRectangle,
    CreateSketch,
)
from caliper.contracts.document import (
    AngleDimension,
    Arc,
    Circle,
    Constraint,
    DistanceDimension,
    Document,
    EntityId,
    Line,
    PartFeature,
    Plane,
    Point,
    RadialDimension,
    Rectangle,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.kernel import Frame
from caliper.contracts.queries import Point3

_O, _X, _Y, _Z = (
    Point3(x=0.0, y=0.0, z=0.0),
    Point3(x=1.0, y=0.0, z=0.0),
    Point3(x=0.0, y=1.0, z=0.0),
    Point3(x=0.0, y=0.0, z=1.0),
)
FRAMES: dict[Plane, Frame] = {
    Plane.XY: Frame(origin=_O, x=_X, y=_Y),  # normal +Z
    Plane.XZ: Frame(origin=_O, x=_X, y=_Z),  # normal -Y
    Plane.YZ: Frame(origin=_O, x=_Y, y=_Z),  # normal +X
}
"""Where each plane's sketch coordinates sit in the part (ADR 0011's table)."""


def frame(plane: Plane) -> Frame:
    return FRAMES[plane]


def normal(on: Frame) -> Point3:
    """x cross y: the way an extrude on `on` goes."""
    x, y = on.x, on.y
    return Point3(x=x.y * y.z - x.z * y.y, y=x.z * y.x - x.x * y.z, z=x.x * y.y - x.y * y.x)


def moved(on: Frame, w: float) -> Frame:
    """`on` moved `w` along its normal: where a reversed extrude starts, so the kernel sweeps
    the same profile back over the stretch it covers (ADR 0016)."""
    o, n = on.origin, normal(on)
    origin = Point3(x=o.x + w * n.x, y=o.y + w * n.y, z=o.z + w * n.z)
    return replace(on, origin=origin)


def feature(document: Document, id: EntityId) -> PartFeature | None:
    """The feature with `id`, or None. A part has a handful, so a scan is fine."""
    for each in document.features:
        if each.id == id:
            return each
    return None


def sketches(document: Document) -> tuple[EntityId, ...]:
    """The part's sketches, in feature order."""
    return tuple(each.id for each in document.features if isinstance(each, Sketch))


IN_A_SKETCH = (CreatePoint, CreateLine, CreateCircle, CreateArc, CreateRectangle, CreateExtrude)
"""The commands that draw in a sketch, or read one, and take `sketch` (None: the only one)."""


def in_sketch(document: Document, command: Command, editing: EntityId | None) -> Command:
    """`command` in `editing`, the sketch the user is editing, when it names no sketch and the
    part has more than one: what the window and the AI both send. With one sketch it goes as it
    came, and the engine finds the only sketch, as for every V1 file. A sketch the part no
    longer has is ignored, so the command is refused with `sketch.required`, as if no sketch
    were being edited."""
    found = sketches(document)
    if (
        isinstance(command, IN_A_SKETCH)
        and command.sketch is None
        and editing in found
        and len(found) > 1
    ):
        return replace(command, sketch=editing)
    return command


DRAWING = (CreatePoint, CreateLine, CreateCircle, CreateArc, CreateRectangle)
"""The commands that draw geometry in a sketch."""


def no_sketch() -> Document:
    """A part with nothing in it, not even a sketch: what the app's 3D tab starts from (ADR
    0015), its planes to sketch on. `Document.empty()` stays the new part everywhere else."""
    return Document(entities=MappingProxyType({}), next_id=1, features=())


def first_sketch(document: Document, command: Command) -> CreateSketch | None:
    """The sketch to make before `command`, when it draws in a part with no sketch and names
    none: one on XY, the Top plane, where a new part's sketch always was. None otherwise, so
    drawing in a part that has sketches goes as it came."""
    if isinstance(command, DRAWING) and command.sketch is None and not sketches(document):
        return CreateSketch(plane=Plane.XY)
    return None


def taken(document: Document, id: EntityId) -> bool:
    """Whether `id` names an entity or a feature: ids are unique across both."""
    return id in document.entities or feature(document, id) is not None


def sketch_of(document: Document, id: EntityId) -> EntityId | None:
    """The sketch an entity is in, or None for a check, a feature, or an unknown id."""
    match document.entities.get(id):
        case Point(sketch=sketch) | Line(sketch=sketch) | Circle(sketch=sketch):
            return sketch
        case Arc(sketch=sketch) | Rectangle(sketch=sketch):
            return sketch
        case DistanceDimension(a=ref) | AngleDimension(a=ref):
            return _geometry_sketch(document, ref.entity)
        case RadialDimension(target=target):
            return _geometry_sketch(document, target)
        case Constraint(refs=refs) if refs:
            return _geometry_sketch(document, refs[0].entity)
    return None


def _geometry_sketch(document: Document, id: EntityId) -> EntityId | None:
    match document.entities.get(id):
        case Point(sketch=sketch) | Line(sketch=sketch) | Circle(sketch=sketch):
            return sketch
        case Arc(sketch=sketch) | Rectangle(sketch=sketch):
            return sketch
    return None


def one_sketch(
    document: Document, ids: Iterable[EntityId], *, field: str, what: str
) -> Error | None:
    """`sketch.mixed` when the entities among `ids` are in more than one sketch. Ids in no
    sketch (checks, features, unknown ids) are left to the caller's own checks. `what` says
    what they are for, as in "a constraint's references"."""
    found = sorted({s for id in ids if (s := sketch_of(document, id)) is not None})
    if len(found) < 2:
        return None
    return Error(
        code=ErrorCode.SKETCH_MIXED,
        message=f"{what} must be in one sketch, but these are in {', '.join(found)}",
        field=field,
        ids=tuple(EntityId(s) for s in found),
    )


def resolve_sketch(
    document: Document, requested: EntityId | None, errors: list[Error]
) -> EntityId | None:
    """The sketch a create command draws in: `requested`, or the part's only sketch.

    None, with `sketch.required` in `errors`, when nothing was requested and the part doesn't
    have exactly one sketch. A requested sketch is returned as given; whether it exists is
    checked with the entity, like every other reference."""
    if requested is not None:
        return requested
    found = sketches(document)
    if len(found) == 1:
        return found[0]
    message = (
        "the part has no sketch to draw in; create one first"
        if not found
        else f"the part has {len(found)} sketches ({', '.join(found)}); say which in `sketch`"
    )
    errors.append(Error(code=ErrorCode.SKETCH_REQUIRED, message=message, field="sketch"))
    return None
