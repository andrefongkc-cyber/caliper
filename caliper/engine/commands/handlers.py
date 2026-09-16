"""What each command does to a Document. Pure functions: a Document in, a Document out."""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import assert_never

from caliper.contracts.commands import (
    Command,
    CreateArc,
    CreateCircle,
    CreateDistanceDimension,
    CreateLine,
    CreateRadialDimension,
    CreateRectangle,
    DeleteEntities,
    FilletCorner,
    ModifyEntity,
    MoveEntities,
)
from caliper.contracts.document import (
    Arc,
    Circle,
    DistanceDimension,
    Document,
    Entity,
    EntityId,
    Geometry,
    Line,
    Point2,
    RadialDimension,
    Rectangle,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.engine.commands.validation import (
    build_entity,
    field_types,
    normalize_float,
    normalize_id,
)

CreateCommand = (
    CreateLine
    | CreateCircle
    | CreateArc
    | CreateRectangle
    | CreateDistanceDimension
    | CreateRadialDimension
)

_CREATES: Mapping[type[CreateCommand], type[Entity]] = {
    CreateLine: Line,
    CreateCircle: Circle,
    CreateArc: Arc,
    CreateRectangle: Rectangle,
    CreateDistanceDimension: DistanceDimension,
    CreateRadialDimension: RadialDimension,
}


_ALLOCATED_ID = re.compile(r"e([1-9][0-9]*)")


@dataclass(frozen=True, slots=True)
class Handled:
    document: Document
    command: Command
    """Resolved: ids filled in, values normalized."""
    label: str
    created_ids: tuple[EntityId, ...]


def handle(document: Document, command: Command) -> Handled | list[Error]:
    match command:
        case (
            CreateLine()
            | CreateCircle()
            | CreateArc()
            | CreateRectangle()
            | CreateDistanceDimension()
            | CreateRadialDimension()
        ):
            return _create(document, command)
        case ModifyEntity():
            return _modify(document, command)
        case MoveEntities():
            return _move(document, command)
        case DeleteEntities():
            return _delete(document, command)
        case FilletCorner():
            return _fillet(document, command)
        case _:
            assert_never(command)


def _create(document: Document, command: CreateCommand) -> Handled | list[Error]:
    entity_type = _CREATES[type(command)]
    names = field_types(entity_type)
    built = build_entity(entity_type, {name: getattr(command, name) for name in names}, document)
    errors = list(built) if isinstance(built, list) else []
    entity_id, next_id = _resolve_id(document, command.id, errors)
    if errors or isinstance(built, list):
        return errors
    resolved = replace(command, id=entity_id, **{name: getattr(built, name) for name in names})
    return Handled(
        document=Document(
            entities=MappingProxyType({**document.entities, entity_id: built}), next_id=next_id
        ),
        command=resolved,
        label=_title(command.kind),
        created_ids=(entity_id,),
    )


def _resolve_id(
    document: Document, requested: EntityId | None, errors: list[Error]
) -> tuple[EntityId, int]:
    """The id for a new entity and the document's next_id afterwards.

    An explicit id in the `e{n}` form the bus allocates also moves next_id past n. Resolved
    commands carry allocated ids, so this is what makes replaying them reproduce next_id.
    """
    if requested is None:
        number = document.next_id
        while EntityId(f"e{number}") in document.entities:
            number += 1
        return EntityId(f"e{number}"), number + 1
    before = len(errors)
    entity_id = normalize_id(requested, "id", errors)
    if len(errors) > before:
        return entity_id, document.next_id
    if entity_id in document.entities:
        errors.append(
            Error(code=ErrorCode.ID_TAKEN, message=f"id {entity_id!r} is already used", field="id")
        )
    if counter := _ALLOCATED_ID.fullmatch(entity_id):
        return entity_id, max(document.next_id, int(counter[1]) + 1)
    return entity_id, document.next_id


def _modify(document: Document, command: ModifyEntity) -> Handled | list[Error]:
    errors: list[Error] = []
    entity_id = normalize_id(command.id, "id", errors)
    if errors:
        return errors
    current = document.entities.get(entity_id)
    if current is None:
        return [
            Error(code=ErrorCode.ENTITY_NOT_FOUND, message=f"no entity {entity_id!r}", field="id")
        ]
    names = field_types(type(current))
    unknown = [
        Error(
            code=ErrorCode.FIELD_UNKNOWN,
            message=f"a {current.kind} has no field {name!r}; fields: {', '.join(names)}",
            field=name,
        )
        for name in command.changes
        if name not in names
    ]
    if unknown:
        return unknown
    values = {name: getattr(current, name) for name in names} | dict(command.changes)
    built = build_entity(type(current), values, document)
    if isinstance(built, list):
        return built
    changed = list(command.changes)
    return Handled(
        document=Document(
            entities=MappingProxyType({**document.entities, entity_id: built}),
            next_id=document.next_id,
        ),
        command=ModifyEntity(
            id=entity_id, changes=MappingProxyType({name: getattr(built, name) for name in changed})
        ),
        label=f"Change {_title(changed[0])}"
        if len(changed) == 1
        else f"Edit {_title(current.kind)}",
        created_ids=(),
    )


def _move(document: Document, command: MoveEntities) -> Handled | list[Error]:
    """Translate the geometry in `ids`. Annotations in `ids` are skipped; they follow their refs."""
    errors: list[Error] = []
    ids = _existing_ids(document, command.ids, errors)
    dx = normalize_float(command.dx, "dx", errors)
    dy = normalize_float(command.dy, "dy", errors)
    if errors:
        return errors
    moved: dict[EntityId, Entity] = {}
    for entity_id in ids:
        entity = document.entities[entity_id]
        if not isinstance(entity, Line | Circle | Arc | Rectangle):
            continue
        values = {name: getattr(entity, name) for name in field_types(type(entity))}
        built = build_entity(type(entity), _translated(entity, values, dx, dy), document)
        if isinstance(built, list):
            return [
                Error(code=e.code, message=f"moving {entity_id!r}: {e.message}", field="ids")
                for e in built
            ]
        moved[entity_id] = built
    return Handled(
        document=Document(
            entities=MappingProxyType({**document.entities, **moved}), next_id=document.next_id
        ),
        command=MoveEntities(ids=ids, dx=dx, dy=dy),
        label=_plural_label("Move", document, ids),
        created_ids=(),
    )


def _translated(
    entity: Geometry, values: dict[str, object], dx: float, dy: float
) -> dict[str, object]:
    def shift(p: Point2) -> Point2:
        return Point2(x=p.x + dx, y=p.y + dy)

    match entity:
        case Line(start=start, end=end):
            return values | {"start": shift(start), "end": shift(end)}
        case Circle(center=center) | Arc(center=center):
            return values | {"center": shift(center)}
        case Rectangle(corner=corner):
            return values | {"corner": shift(corner)}


def _delete(document: Document, command: DeleteEntities) -> Handled | list[Error]:
    """Delete `ids`, and in the same delta every annotation that refers to a deleted entity."""
    errors: list[Error] = []
    ids = _existing_ids(document, command.ids, errors)
    if errors:
        return errors
    doomed = set(ids)
    doomed |= {
        entity_id
        for entity_id, entity in document.entities.items()
        if (isinstance(entity, DistanceDimension) and {entity.a.entity, entity.b.entity} & doomed)
        or (isinstance(entity, RadialDimension) and entity.target in doomed)
    }
    return Handled(
        document=Document(
            entities=MappingProxyType(
                {i: e for i, e in document.entities.items() if i not in doomed}
            ),
            next_id=document.next_id,
        ),
        command=DeleteEntities(ids=ids),
        label=_plural_label("Delete", document, ids),
        created_ids=(),
    )


def _fillet(document: Document, command: FilletCorner) -> Handled | list[Error]:
    """Trim two lines back to an arc of `radius` tangent to both, and add that arc."""
    errors: list[Error] = []
    a_id = normalize_id(command.a, "a", errors)
    b_id = normalize_id(command.b, "b", errors)
    radius = normalize_float(command.radius, "radius", errors)
    if errors:
        return errors
    if radius <= 0:
        return [_error(ErrorCode.VALUE_NOT_POSITIVE, "radius", "radius must be greater than 0")]
    lines: dict[str, Line] = {}
    for field, entity_id in (("a", a_id), ("b", b_id)):
        entity = document.entities.get(entity_id)
        if entity is None:
            errors.append(_error(ErrorCode.ENTITY_NOT_FOUND, field, f"no entity {entity_id!r}"))
        elif not isinstance(entity, Line):
            errors.append(
                _error(
                    ErrorCode.ENTITY_WRONG_KIND,
                    field,
                    f"{entity_id!r} is a {entity.kind}; a fillet rounds two lines",
                )
            )
        else:
            lines[field] = entity
    if a_id == b_id:
        errors.append(
            _error(ErrorCode.REFERENCE_DEGENERATE, "b", "a and b must be different lines")
        )
    if errors:
        return errors

    corner = _shared_endpoint(lines["a"], lines["b"])
    if corner is None:
        return [
            _error(
                ErrorCode.GEOMETRY_DEGENERATE,
                "b",
                f"{a_id!r} and {b_id!r} must share exactly one endpoint to round the corner",
            )
        ]
    ua, length_a = _direction(corner, _far_end(lines["a"], corner))
    ub, length_b = _direction(corner, _far_end(lines["b"], corner))
    dot = ua.x * ub.x + ua.y * ub.y
    cross = ua.x * ub.y - ua.y * ub.x
    if cross == 0:
        return [
            _error(
                ErrorCode.GEOMETRY_DEGENERATE,
                "b",
                f"{a_id!r} and {b_id!r} are in line; there is no corner to round",
            )
        ]
    # Distance from the corner to each tangent point: radius / tan(half the corner angle),
    # written from the dot and cross products so a right angle stays exact.
    reach = radius * (1.0 + dot) / abs(cross)
    if reach >= length_a or reach >= length_b:
        # Equal is no good either: the tangent point would land on the far end and leave
        # nothing of that line.
        largest = min(length_a, length_b) * abs(cross) / (1.0 + dot)
        return [
            _error(
                ErrorCode.VALUE_OUT_OF_RANGE,
                "radius",
                f"radius {radius!r} needs {reach!r} mm of each line, but they are "
                f"{length_a!r} and {length_b!r} mm long; the radius must stay under {largest!r}",
            )
        ]
    touch_a = Point2(x=corner.x + ua.x * reach, y=corner.y + ua.y * reach)
    touch_b = Point2(x=corner.x + ub.x * reach, y=corner.y + ub.y * reach)
    inward = Point2(x=-ua.y, y=ua.x)
    if inward.x * ub.x + inward.y * ub.y < 0:
        inward = Point2(x=ua.y, y=-ua.x)
    center = Point2(x=touch_a.x + inward.x * radius, y=touch_a.y + inward.y * radius)

    trimmed: dict[EntityId, Entity] = {}
    for entity_id, line, touch in ((a_id, lines["a"], touch_a), (b_id, lines["b"], touch_b)):
        moved = "start" if line.start == corner else "end"
        values = {name: getattr(line, name) for name in field_types(Line)} | {moved: touch}
        built = build_entity(Line, values, document)
        if isinstance(built, list):
            return built
        trimmed[entity_id] = built
    arc = build_entity(Arc, _arc_values(center, radius, touch_a, touch_b), document)
    if isinstance(arc, list):
        return arc
    arc_id, next_id = _resolve_id(document, command.id, errors)
    if errors:
        return errors
    return Handled(
        document=Document(
            entities=MappingProxyType({**document.entities, **trimmed, arc_id: arc}),
            next_id=next_id,
        ),
        command=FilletCorner(a=a_id, b=b_id, radius=radius, id=arc_id),
        label="Fillet Corner",
        created_ids=(arc_id,),
    )


def _shared_endpoint(a: Line, b: Line) -> Point2 | None:
    """The one endpoint both lines have, or None if they share none or both."""
    shared = [p for p in (a.start, a.end) if p in (b.start, b.end)]
    return shared[0] if len(shared) == 1 else None


def _far_end(line: Line, corner: Point2) -> Point2:
    return line.end if line.start == corner else line.start


def _direction(start: Point2, end: Point2) -> tuple[Point2, float]:
    length = math.hypot(end.x - start.x, end.y - start.y)
    return Point2(x=(end.x - start.x) / length, y=(end.y - start.y) / length), length


def _arc_values(center: Point2, radius: float, a: Point2, b: Point2) -> dict[str, object]:
    """The arc between the two tangent points: the short way round, counter-clockwise."""
    angle_a = math.degrees(math.atan2(a.y - center.y, a.x - center.x))
    angle_b = math.degrees(math.atan2(b.y - center.y, b.x - center.x))
    sweep = (angle_b - angle_a) % 360.0
    start = angle_a if sweep <= 180.0 else angle_b
    return {
        "center": center,
        "radius": radius,
        "start_angle": start % 360.0,
        "sweep_angle": min(sweep, 360.0 - sweep),
    }


def _existing_ids(document: Document, ids: object, errors: list[Error]) -> tuple[EntityId, ...]:
    """Normalized ids, each once and in the order given, all present in `document`."""
    if not isinstance(ids, tuple | list):
        errors.append(
            Error(code=ErrorCode.VALUE_WRONG_TYPE, message="ids must be a list of ids", field="ids")
        )
        return ()
    if not ids:
        errors.append(
            Error(
                code=ErrorCode.SELECTION_EMPTY,
                message="ids must name at least one entity",
                field="ids",
            )
        )
        return ()
    resolved: dict[EntityId, None] = {}
    for index, value in enumerate(ids):
        field = f"ids[{index}]"
        before = len(errors)
        entity_id = normalize_id(value, field, errors)
        if len(errors) > before:
            continue
        if entity_id not in document.entities:
            errors.append(
                Error(
                    code=ErrorCode.ENTITY_NOT_FOUND, message=f"no entity {entity_id!r}", field=field
                )
            )
            continue
        resolved[entity_id] = None
    return tuple(resolved)


def _plural_label(verb: str, document: Document, ids: tuple[EntityId, ...]) -> str:
    if len(ids) == 1:
        return f"{verb} {_title(document.entities[ids[0]].kind)}"
    return f"{verb} {len(ids)} Entities"


def _error(code: ErrorCode, field: str, message: str) -> Error:
    return Error(code=code, message=message, field=field)


def _title(snake: str) -> str:
    return snake.replace("_", " ").title()
