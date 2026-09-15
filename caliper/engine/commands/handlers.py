"""What each command does to a Document. Pure functions: a Document in, a Document out."""

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
    """The id for a new entity and the document's next_id afterwards."""
    if requested is None:
        number = document.next_id
        while EntityId(f"e{number}") in document.entities:
            number += 1
        return EntityId(f"e{number}"), number + 1
    before = len(errors)
    entity_id = normalize_id(requested, "id", errors)
    if len(errors) == before and entity_id in document.entities:
        errors.append(
            Error(code=ErrorCode.ID_TAKEN, message=f"id {entity_id!r} is already used", field="id")
        )
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


def _title(snake: str) -> str:
    return snake.replace("_", " ").title()
