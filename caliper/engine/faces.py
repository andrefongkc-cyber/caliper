"""Where a sketch on a face sits (ADR 0016), worked out from the extrude that made the face.

A face is named by what made it (ADR 0014): its extrude's `start` and `end` caps, and the
`side` swept from each line or from each side of a rectangle. Where it is follows from the
extrude's inputs alone (its sketch's plane, its depth and direction, the profile's lines), so
placing a sketch never needs a kernel (ADR 0013), and a sketch on a face follows edits to
them. What this can't see is a later cut taking the face away: the sketch stays where the face
was (ADR 0016's limit).

A face's axes come from its plane alone (`canonical`): x level, y up the face, the normal out
of the part. Those are exactly the three planes' own axes (ADR 0011), so a sketch on a top
face lines up with the sketch beneath it, and the camera that faces any of them needs no roll.

Frames are interned: equal frames are the same object, so caches keyed by identity (the
prisms in `features`) keep finding what they made.
"""

import math
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass

from caliper.contracts.document import (
    FACE_PATTERN,
    Arc,
    Circle,
    Document,
    EntityId,
    Extrude,
    ExtrudeOperation,
    FaceRef,
    Geometry,
    Line,
    Plane,
    Point2,
    Rectangle,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.kernel import Loop
from caliper.contracts.queries import Frame, Point3
from caliper.engine import part, profiles
from caliper.engine.document.recent import ByIdentity, Recent

FACE = re.compile(FACE_PATTERN)
RECTANGLE_SIDES = ("bottom", "right", "top", "left")
"""A rectangle's sides as `profiles.traversed` walks them: counter-clockwise from its corner."""
LEVEL = 1e-12
"""A normal this close to vertical is vertical: the face is level, and its x is +X."""


# --- Names --------------------------------------------------------------------------------


def parse(name: object) -> tuple[str, EntityId | None, str | None] | None:
    """`("start" | "end" | "side", the side's entity, a rectangle's side)`, or None for
    anything that isn't a face's name."""
    if not isinstance(name, str) or not FACE.fullmatch(name):
        return None
    if name in ("start", "end"):
        return name, None, None
    entity, _, side = name.removeprefix("side ").partition(".")
    return "side", EntityId(entity), side or None


# --- Frames -------------------------------------------------------------------------------

_INTERNED: OrderedDict[Frame, Frame] = OrderedDict()
_LOCK = threading.Lock()
INTERNED_KEPT = 1024


def interned(frame: Frame) -> Frame:
    """The one object for frames equal to `frame`."""
    with _LOCK:
        kept = _INTERNED.get(frame)
        if kept is None:
            _INTERNED[frame] = kept = frame
            while len(_INTERNED) > INTERNED_KEPT:
                _INTERNED.popitem(last=False)
        else:
            _INTERNED.move_to_end(frame)
        return kept


for _plane in Plane:
    interned(part.frame(_plane))


def canonical(outward: Point3, through: Point3) -> Frame:
    """The frame of the plane through `through` facing `outward`: x level (+X when the plane
    is level), y = normal x x, up the face, and the origin the plane's point nearest the
    part's origin."""
    m = _unit(outward)
    level = math.hypot(m.x, m.y)
    if level <= LEVEL:  # vertical: exactly, so y is exactly up the face
        m = Point3(x=0.0, y=0.0, z=math.copysign(1.0, m.z))
        x = Point3(x=1.0, y=0.0, z=0.0)
    else:
        x = Point3(x=-m.y / level, y=m.x / level, z=0.0)
    s = _dot(through, m)
    origin = Point3(x=s * m.x, y=s * m.y, z=s * m.z)
    return interned(Frame(origin=origin, x=x, y=_cross(m, x)))


def sketch_frames(document: Document) -> Mapping[EntityId, Frame | Error]:
    """Each sketch's frame, or why it can't be placed, in one pass over the features: a face
    is always an earlier feature's, so its extrude's sketch is placed first."""
    cached = _FRAMES.get(document)
    if cached is not None:
        return cached
    found: dict[EntityId, Frame | Error] = {}
    for position, feature in enumerate(document.features):
        if isinstance(feature, Sketch):
            found[feature.id] = _placed(document, feature.plane, position, found)
    _FRAMES.put(document, found)
    return found


def frame(document: Document, plane: Plane | FaceRef, position: int | None = None) -> Frame | Error:
    """Where `plane` is, for a sketch at `position` in the features (the end by default)."""
    at = len(document.features) if position is None else position
    return _placed(document, plane, at, sketch_frames(document))


def extrude_frame(document: Document, extrude: Extrude) -> Frame | Error:
    """The frame the kernel sweeps `extrude` from: its sketch's, moved back by the depth when
    it's reversed, so the same profile covers the stretch against the normal."""
    on = sketch_frames(document).get(extrude.sketch)
    if on is None:
        return Error(
            code=ErrorCode.ENTITY_NOT_FOUND,
            message=f"{extrude.id} reads sketch {extrude.sketch}, which the part doesn't have",
            field="sketch",
        )
    if isinstance(on, Error):
        return _failed(extrude.sketch, extrude.id)
    return interned(part.moved(on, -extrude.depth)) if extrude.reversed else on


def default_reversed(document: Document, sketch: EntityId, operation: object) -> bool:
    """Which way an extrude goes when the command leaves it to the engine: a cut from a sketch
    on a face goes into the part, and anything else along the normal."""
    found = part.feature(document, sketch)
    return (
        operation == ExtrudeOperation.REMOVE
        and isinstance(found, Sketch)
        and isinstance(found.plane, FaceRef)
    )


def names(document: Document, id: EntityId) -> tuple[FaceRef, ...] | Error:
    """The flat faces of extrude `id`: its caps, then the sides of its lines and rectangles in
    the profile's order."""
    feature = part.feature(document, id)
    if not isinstance(feature, Extrude):
        if feature is None and id not in document.entities:
            return Error(code=ErrorCode.ENTITY_NOT_FOUND, message=f"no feature {id!r}", field="id")
        kind = feature.kind if feature is not None else document.entities[id].kind
        return Error(
            code=ErrorCode.ENTITY_WRONG_KIND,
            message=f"{id!r} is a {kind}; only an extrude's faces are named",
            field="id",
        )
    sides = _sides(document, feature)
    if isinstance(sides, Error):
        return sides
    flat = [name for name, side in sides.items() if not isinstance(side, Error)]
    return tuple(FaceRef(feature=id, face=name) for name in ("start", "end", *flat))


# --- What an extrude sweeps ---------------------------------------------------------------


def profile(document: Document, extrude: Extrude) -> profiles.Profile | Error:
    """The closed profile an extrude sweeps, or why its geometry isn't one."""
    picked = chosen(document, extrude)
    return picked if isinstance(picked, Error) else profiles.find(picked)


def chosen(document: Document, extrude: Extrude) -> list[tuple[EntityId, Geometry]] | Error:
    """The geometry an extrude sweeps, in id order: `ids`, or the sketch's own."""
    sketch = part.feature(document, extrude.sketch)
    if not isinstance(sketch, Sketch):
        return Error(
            code=ErrorCode.ENTITY_NOT_FOUND,
            message=f"{extrude.id} reads sketch {extrude.sketch}, which the part doesn't have",
            field="sketch",
        )
    picked: list[tuple[EntityId, Geometry]] = []
    if extrude.ids:
        for id in extrude.ids:
            entity = document.entities.get(id)
            if entity is None:
                return _error(ErrorCode.ENTITY_NOT_FOUND, f"no entity {id!r}", id)
            if not isinstance(entity, Geometry):
                return _error(
                    ErrorCode.ENTITY_WRONG_KIND,
                    f"{id!r} is a {entity.kind}; a profile is geometry",
                    id,
                )
            if entity.sketch != sketch.id:
                message = f"{id!r} is in sketch {entity.sketch}, not {sketch.id}"
                return _error(ErrorCode.SKETCH_MIXED, message, id)
            if entity.construction:
                message = f"{id!r} is construction geometry, which isn't part of a profile"
                return _error(ErrorCode.PROFILE_CONSTRUCTION, message, id)
            picked.append((id, entity))
    else:
        picked = sorted(
            (id, e)
            for id, e in document.entities.items()
            if isinstance(e, Geometry) and e.sketch == sketch.id and not e.construction
        )
        if not picked:
            return Error(
                code=ErrorCode.SELECTION_EMPTY,
                message=f"sketch {sketch.id} has nothing to extrude: draw a closed profile in it",
                field="sketch",
            )
    return picked


# --- Placing a face -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Side:
    """A side face in its extrude's sketch: through `a`, facing `out` (a unit direction away
    from what the extrude itself sweeps)."""

    a: Point2
    out: Point2


type _Sides = Mapping[str, _Side | Error]

_FRAMES: Recent[Mapping[EntityId, Frame | Error]] = Recent(8)
_SIDES: ByIdentity[_Sides | Error] = ByIdentity(64)
"""Each extrude's sides, by the extrude and its profile's geometry, the very objects."""


def _placed(
    document: Document,
    plane: Plane | FaceRef,
    position: int,
    frames: Mapping[EntityId, Frame | Error],
) -> Frame | Error:
    if isinstance(plane, Plane):
        return part.frame(plane)
    target = part.feature(document, plane.feature)
    if target is None:
        message = f"the part has no feature {plane.feature!r} for this sketch's face"
        return Error(code=ErrorCode.ENTITY_NOT_FOUND, message=message, field="plane")
    if not isinstance(target, Extrude):
        return Error(
            code=ErrorCode.ENTITY_WRONG_KIND,
            message=f"{plane.feature!r} is a {target.kind}; only an extrude's faces are named",
            field="plane",
        )
    if next(i for i, f in enumerate(document.features) if f is target) >= position:
        return Error(
            code=ErrorCode.DEPENDENCY_CYCLE,
            message=f"{plane.feature!r} comes at or after this sketch: a sketch sits only on "
            "a face of a feature before it",
            field="plane",
            ids=(plane.feature,),
        )
    on = frames.get(target.sketch)
    if on is None or isinstance(on, Error):
        return _failed(target.sketch, target.id)
    return _face(document, plane, target, on)


def _face(document: Document, ref: FaceRef, extrude: Extrude, on: Frame) -> Frame | Error:
    """Where `ref` is: ADR 0016's table. The extrude sweeps direction d; its caps face -d and
    +d, its sides away from the profile's inside, and a cut's faces the other way."""
    parsed = parse(ref.face)
    if parsed is None:
        return _not_found(document, extrude, ref.face)
    kind, _, _ = parsed
    n = part.normal(on)
    d = _scaled(n, -1.0 if extrude.reversed else 1.0)
    out = -1.0 if extrude.operation is ExtrudeOperation.REMOVE else 1.0
    if kind == "start":
        return canonical(_scaled(d, -out), on.origin)
    if kind == "end":
        return canonical(_scaled(d, out), _plus(on.origin, _scaled(d, extrude.depth)))
    sides = _sides(document, extrude)
    if isinstance(sides, Error):
        return Error(
            code=ErrorCode.FACE_NOT_FOUND,
            message=f"{extrude.id} has no {ref.face!r} now: {sides.message}",
            field="plane",
            ids=sides.ids,
        )
    side = sides.get(ref.face)
    if side is None:
        return _not_found(document, extrude, ref.face)
    if isinstance(side, Error):
        return side
    facing = _plus(_scaled(on.x, side.out.x * out), _scaled(on.y, side.out.y * out))
    through = _plus(on.origin, _plus(_scaled(on.x, side.a.x), _scaled(on.y, side.a.y)))
    return canonical(facing, through)


def _sides(document: Document, extrude: Extrude) -> _Sides | Error:
    """Each side face of what `extrude` sweeps, by name, from its profile."""
    picked = chosen(document, extrude)
    if isinstance(picked, Error):
        return picked
    key = (extrude, *(entity for _, entity in picked))
    cached = _SIDES.get(key)
    if cached is not None:
        return cached
    found = profiles.find(picked)
    made = found if isinstance(found, Error) else _named(found, picked)
    _SIDES.put(key, made)
    return made


def _named(found: profiles.Profile, picked: list[tuple[EntityId, Geometry]]) -> _Sides:
    owners = {id(entity): eid for eid, entity in picked}
    sides: dict[str, _Side | Error] = {}
    for loop, outer in ((found.outer, True), *((hole, False) for hole in found.holes)):
        # Material is on the left of an outer loop run counter-clockwise, and on the right of
        # a hole run that way: out is to the right in the first case, to the left otherwise.
        right = outer == (profiles.signed_area(profiles.traversed(loop)) > 0)
        flags = loop.reversed or (False,) * len(loop.edges)
        for entity, backwards in zip(loop.edges, flags, strict=True):
            owner = owners.get(id(entity))
            if owner is None:
                continue
            walked = profiles.traversed(Loop(edges=(entity,), reversed=(backwards,)))
            match entity:
                case Line():
                    sides[f"side {owner}"] = _side(walked[0], right)
                case Rectangle():
                    for name, edge in zip(RECTANGLE_SIDES, walked, strict=True):
                        sides[f"side {owner}.{name}"] = _side(edge, right)
                case Arc() | Circle():
                    sides[f"side {owner}"] = Error(
                        code=ErrorCode.FACE_NOT_PLANAR,
                        message=f"the side swept from {owner} ({entity.kind}) is curved: a "
                        "sketch needs a flat face",
                        field="plane",
                        ids=(owner,),
                    )
    return sides


def _side(edge: profiles.Edge, right: bool) -> _Side:
    dx, dy = edge.b.x - edge.a.x, edge.b.y - edge.a.y
    length = math.hypot(dx, dy)
    out = Point2(x=dy / length, y=-dx / length) if right else Point2(x=-dy / length, y=dx / length)
    return _Side(a=edge.a, out=out)


def _not_found(document: Document, extrude: Extrude, face: str) -> Error:
    listed = names(document, extrude.id)
    known = (
        ", ".join(ref.face for ref in listed)
        if not isinstance(listed, Error)
        else "start, end, and the sides of its lines and rectangles"
    )
    return Error(
        code=ErrorCode.FACE_NOT_FOUND,
        message=f"{extrude.id} has no flat face {face!r}; its faces are {known}",
        field="plane",
        ids=(extrude.id,),
    )


def _failed(sketch: EntityId, reader: EntityId) -> Error:
    return Error(
        code=ErrorCode.FEATURE_FAILED,
        message=f"{sketch} can't be placed, so {reader}'s faces can't be found",
        ids=(sketch,),
    )


def _error(code: ErrorCode, message: str, id: EntityId) -> Error:
    return Error(code=code, message=message, field="ids", ids=(id,))


def _unit(v: Point3) -> Point3:
    size = math.sqrt(_dot(v, v))
    return Point3(x=v.x / size, y=v.y / size, z=v.z / size)


def _dot(a: Point3, b: Point3) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def _cross(a: Point3, b: Point3) -> Point3:
    return Point3(x=a.y * b.z - a.z * b.y, y=a.z * b.x - a.x * b.z, z=a.x * b.y - a.y * b.x)


def _scaled(v: Point3, k: float) -> Point3:
    return Point3(x=v.x * k, y=v.y * k, z=v.z * k)


def _plus(a: Point3, b: Point3) -> Point3:
    return Point3(x=a.x + b.x, y=a.y + b.y, z=a.z + b.z)


def forget() -> None:
    """Drop everything cached, for tests that count work from nothing."""
    _FRAMES.clear()
    _SIDES.clear()
