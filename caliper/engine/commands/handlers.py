"""What each command does to a Document. Pure functions: a Document in, a Document out.

Being pure, a command's outcome on a document never changes. `already` lets a caller that
has run commands once (an assistant's or MCP client's workspace) hand the outcomes to a bus
that runs the same commands from the same document: accepting a proposal then commits what
was already validated and solved, instead of doing all of it again.
"""

import math
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import assert_never

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateAngleDimension,
    CreateArc,
    CreateCheck,
    CreateCircle,
    CreateConstraint,
    CreateDimension,
    CreateDistanceDimension,
    CreateExtrude,
    CreateLine,
    CreatePoint,
    CreateRadialDimension,
    CreateRectangle,
    CreateSketch,
    DeleteEntities,
    FilletCorner,
    ModifyEntity,
    MoveEntities,
)
from caliper.contracts.document import (
    AngleDimension,
    Arc,
    Circle,
    Constraint,
    ConstraintType,
    DistanceDimension,
    Document,
    Entity,
    EntityId,
    Expectation,
    Extrude,
    ExtrudeOperation,
    FaceRef,
    Feature,
    Geometry,
    Line,
    PartFeature,
    Point,
    Point2,
    RadialDimension,
    Rectangle,
    Ref,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import DimensionType
from caliper.engine import faces, features, graph, part
from caliper.engine.commands.validation import (
    GEOMETRY,
    build_entity,
    build_feature,
    feature_errors,
    field_types,
    normalize_enum,
    normalize_float,
    normalize_id,
    normalize_point,
    normalize_refs,
    with_article,
)
from caliper.engine.constraints import dimensions
from caliper.engine.constraints.model import PARAMS
from caliper.engine.constraints.sketch import (
    Request,
    entity_params,
    is_relation,
    mover,
    ref_params,
    referrers,
    settle,
    turning,
)
from caliper.engine.document.delta import apply
from caliper.engine.queries import DocumentQueries

CreateCommand = (
    CreatePoint
    | CreateLine
    | CreateCircle
    | CreateArc
    | CreateRectangle
    | CreateDistanceDimension
    | CreateRadialDimension
    | CreateAngleDimension
    | CreateConstraint
    | CreateCheck
)

_CREATES: Mapping[type[CreateCommand], type[Entity]] = {
    CreatePoint: Point,
    CreateLine: Line,
    CreateCircle: Circle,
    CreateArc: Arc,
    CreateRectangle: Rectangle,
    CreateDistanceDimension: DistanceDimension,
    CreateRadialDimension: RadialDimension,
    CreateAngleDimension: AngleDimension,
    CreateConstraint: Constraint,
    CreateCheck: Expectation,
}

_GEOMETRY_CREATES = (CreatePoint, CreateLine, CreateCircle, CreateArc, CreateRectangle)
"""The commands that draw geometry, each in a sketch (ADR 0011)."""

_PLACEMENT_FIELDS = frozenset({"offset", "label_angle"})
"""Dimension fields that only position a label; changing them never needs a solve."""


_ALLOCATED_ID = re.compile(r"e([1-9][0-9]*)")


@dataclass(frozen=True, slots=True)
class Handled:
    document: Document
    command: Command
    """Resolved: ids filled in, values normalized."""
    label: str
    created_ids: tuple[EntityId, ...]
    solve: Request | None = None
    """What the constraints must re-check after this command, if anything."""


@dataclass(frozen=True, slots=True)
class Executed:
    """Commands a bus ran one after another, from `base` to `result`, and what each one did."""

    base: Document
    steps: tuple[Applied, ...]
    """Each command's outcome as the bus reported it: the resolved command, its delta, its
    label, and the ids it created. Applying every delta in turn to `base` gives `result`."""
    result: Document


class _Replay:
    """How far a bus running `executed`'s commands again has got, and on which document."""

    def __init__(self, executed: Executed) -> None:
        self.executed = executed
        self.done = 0
        self.document = executed.base

    def outcome(self, document: Document, command: Command) -> Handled | None:
        steps = self.executed.steps
        if self.done == len(steps) or document is not self.document:
            return None
        step = steps[self.done]
        if step.command is not command:
            return None
        self.done += 1
        # Each document in between is rebuilt from the recorded delta rather than kept, so a
        # record costs its deltas, not a copy of the sketch per command.
        after = apply(document, step.delta)
        if self.done == len(steps) and after == self.executed.result:
            after = self.executed.result  # the very document: all worked out for it still holds
        self.document = after
        return Handled(
            document=after, command=command, label=step.label, created_ids=step.created_ids
        )


_REPLAYS: list[_Replay] = []
"""The records `handle` commits from while `already` is open."""


@contextmanager
def already(executed: Executed) -> Iterator[None]:
    """While open, a bus that runs `executed`'s commands again, in the same order, starting
    from `executed.base` itself, commits each one's recorded outcome instead of validating and
    solving the command a second time.

    The first command must meet `base` and each later one the document the step before it
    returned (the same objects, not equal ones), and each command must be the very object
    recorded. Then the outcome is exactly what running it would give, a command's handler
    being a pure function of the two. From the first mismatch on, everything runs as usual.
    """
    replay = _Replay(executed)
    _REPLAYS.append(replay)
    try:
        yield
    finally:
        _REPLAYS.remove(replay)


def handle(document: Document, command: Command) -> Handled | list[Error]:
    """Apply `command`, then solve whatever constraints it touched."""
    for replay in _REPLAYS:
        if (recorded := replay.outcome(document, command)) is not None:
            return recorded
    outcome = _apply(document, command)
    if isinstance(outcome, list) or outcome.solve is None:
        return outcome
    solved = settle(document, outcome.document, outcome.solve)
    if isinstance(solved, list):
        return solved
    return replace(outcome, document=solved, solve=None)


def _apply(document: Document, command: Command) -> Handled | list[Error]:
    match command:
        case CreateSketch():
            return _create_sketch(document, command)
        case CreateExtrude():
            return _create_extrude(document, command)
        case (
            CreatePoint()
            | CreateLine()
            | CreateCircle()
            | CreateArc()
            | CreateRectangle()
            | CreateDistanceDimension()
            | CreateRadialDimension()
            | CreateAngleDimension()
            | CreateConstraint()
            | CreateCheck()
        ):
            return _create(document, command)
        case CreateDimension():
            return _create_dimension(document, command)
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


def _create_sketch(document: Document, command: CreateSketch) -> Handled | list[Error]:
    """Add a sketch at the end of the part's features. Nothing is drawn in it yet."""
    errors: list[Error] = []
    sketch_id, next_id = _resolve_id(document, command.id, errors)
    if errors:
        return errors
    values = {"id": sketch_id, "plane": command.plane}
    added = build_feature(Sketch, values, document, position=len(document.features))
    if isinstance(added, list):
        return added
    assert isinstance(added, Sketch)
    if problem := _face_problem(document, added, len(document.features)):
        return [problem]
    return Handled(
        document=replace(document, features=(*document.features, added), next_id=next_id),
        command=CreateSketch(plane=added.plane, id=sketch_id),
        label="Create Sketch",
        created_ids=(sketch_id,),
    )


def _create_extrude(document: Document, command: CreateExtrude) -> Handled | list[Error]:
    """Add an extrude at the end of the part's features. Nothing is built here: the solid is
    worked out when it's asked for (`caliper.engine.features`), so a command never needs a
    kernel. The profile must be closed now, which the engine checks exactly."""
    errors: list[Error] = []
    sketch = part.resolve_sketch(document, command.sketch, errors)
    feature_id, next_id = _resolve_id(document, command.id, errors)
    if errors or sketch is None:
        return errors
    values = {
        "id": feature_id,
        "sketch": sketch,
        "depth": command.depth,
        "operation": command.operation,
        "ids": command.ids,
        "reversed": faces.default_reversed(document, sketch, command.operation)
        if command.reversed is None
        else command.reversed,
    }
    position = len(document.features)
    added = build_feature(Extrude, values, document, position=position)
    if isinstance(added, list):
        return added
    assert isinstance(added, Extrude)
    if problem := _extrude_problem(document, added, position):
        return [problem]
    return Handled(
        document=replace(document, features=(*document.features, added), next_id=next_id),
        command=CreateExtrude(
            depth=added.depth,
            sketch=added.sketch,
            operation=added.operation,
            ids=added.ids,
            id=feature_id,
            reversed=added.reversed,
        ),
        label="Extrude",
        created_ids=(feature_id,),
    )


def _face_problem(document: Document, sketch: Sketch, position: int) -> Error | None:
    """Why a sketch can't go on its face now (ADR 0016): the extrude has no such flat face, or
    can't be placed itself. A plane is always there."""
    if not isinstance(sketch.plane, FaceRef):
        return None
    placed = faces.frame(document, sketch.plane, position)
    return placed if isinstance(placed, Error) else None


def _extrude_problem(document: Document, extrude: Extrude, position: int) -> Error | None:
    """Why an extrude can't be made or changed so now: its profile isn't one closed profile,
    or it removes with nothing that adds before it."""
    found = features.profile(document, extrude)
    if isinstance(found, Error):
        return found
    if extrude.operation is ExtrudeOperation.REMOVE and not any(
        isinstance(f, Extrude) and f.operation is ExtrudeOperation.ADD
        for f in document.features[:position]
    ):
        return _error(
            ErrorCode.VALUE_OUT_OF_RANGE,
            "operation",
            "there's no solid to remove from yet: the part's first extrude adds",
        )
    return None


def _create(document: Document, command: CreateCommand) -> Handled | list[Error]:
    entity_type = _CREATES[type(command)]
    names = field_types(entity_type)
    values = {name: getattr(command, name) for name in names}
    if isinstance(command, _GEOMETRY_CREATES):
        # Drawn in the sketch the command names, or the part's only one; the resolved
        # command records which.
        problems: list[Error] = []
        values["sketch"] = part.resolve_sketch(document, command.sketch, problems)
        if problems:
            return problems
    built = build_entity(entity_type, values, document)
    errors = list(built) if isinstance(built, list) else _check_errors(document, built)
    entity_id, next_id = _resolve_id(document, command.id, errors)
    if errors or isinstance(built, list):
        return errors
    resolved = replace(command, id=entity_id, **{name: getattr(built, name) for name in names})
    after = replace(
        document,
        entities=MappingProxyType({**document.entities, entity_id: built}),
        next_id=next_id,
    )
    label = (
        f"Add {_title(built.type)} Constraint"
        if isinstance(built, Constraint)
        else _title(command.kind)
    )
    return Handled(
        document=after,
        command=resolved,
        label=label,
        created_ids=(entity_id,),
        solve=_relation_solve(after, entity_id, new=True) if is_relation(built) else None,
    )


def _create_dimension(document: Document, command: CreateDimension) -> Handled | list[Error]:
    """Infer the dimension a selection and placement mean, then create it like any other."""
    errors: list[Error] = []
    refs = normalize_refs(command.refs, "refs", errors)
    placement = normalize_point(command.placement, "placement", errors)
    value = None if command.value is None else normalize_float(command.value, "value", errors)
    kind = (
        None
        if command.type is None
        else normalize_enum(DimensionType, command.type, "type", errors)
    )
    if errors:
        return errors
    for i, ref in enumerate(refs):
        errors += feature_errors(ref, f"refs[{i}]", document, curves=True)
    if errors:
        return errors
    if len(set(refs)) != len(refs):
        return [_error(ErrorCode.REFERENCE_DEGENERATE, "refs", "a reference is repeated")]
    what = "a dimension's references"
    if mixed := part.one_sketch(document, (r.entity for r in refs), field="refs", what=what):
        return [mixed]
    inferred = dimensions.infer(document, refs, placement, kind)
    if isinstance(inferred, Error):
        return [inferred]
    kind, entity = inferred
    values = {name: getattr(entity, name) for name in field_types(type(entity))}
    built = build_entity(type(entity), values | {"value": value}, document)
    if isinstance(built, list):
        return built
    entity_id, next_id = _resolve_id(document, command.id, errors)
    if errors:
        return errors
    after = replace(
        document,
        entities=MappingProxyType({**document.entities, entity_id: built}),
        next_id=next_id,
    )
    return Handled(
        document=after,
        command=CreateDimension(
            refs=refs, placement=placement, value=value, type=kind, id=entity_id
        ),
        label=f"Create {_title(kind)} Dimension",
        created_ids=(entity_id,),
        solve=_relation_solve(after, entity_id, new=True) if value is not None else None,
    )


def _check_errors(document: Document, entity: Entity) -> list[Error]:
    """Why a check can't be stored as it is: it must be one `Queries.check` can evaluate
    now. It may fail; that's what it's for. A geometry kernel missing from this machine
    isn't the check's fault: an area check of a profile is stored without OCCT, as it would be
    with it, and measured wherever a kernel is (the profile itself is checked first)."""
    if not isinstance(entity, Expectation):
        return []
    error = DocumentQueries(document).check(entity).error
    if error is None or error.code is ErrorCode.KERNEL_UNAVAILABLE:
        return []
    return [error]


def _relation_solve(document: Document, id: EntityId, *, new: bool) -> Request:
    """Solve for a constraint or driving dimension, moving the reference it names last."""
    relation = document.entities[id]
    moving = mover(document, relation)
    return Request(
        touched=frozenset({id}),
        movers=(ref_params(document, moving), entity_params(document, moving.entity)),
        new=frozenset({id}) if new else frozenset(),
        turning=turning(document, relation),
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
        while part.taken(document, EntityId(f"e{number}")):
            number += 1
        return EntityId(f"e{number}"), number + 1
    before = len(errors)
    entity_id = normalize_id(requested, "id", errors)
    if len(errors) > before:
        return entity_id, document.next_id
    if part.taken(document, entity_id):
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
        if (found := part.feature(document, entity_id)) is not None:
            return _modify_feature(document, found, command)
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
    if isinstance(current, GEOMETRY) and command.changes.get("sketch", current.sketch) != (
        current.sketch
    ):
        # Its dimensions and constraints would have to move with it (ADR 0011).
        message = f"a {current.kind}'s sketch can't be changed; draw it again in the other sketch"
        return [_error(ErrorCode.VALUE_OUT_OF_RANGE, "sketch", message)]
    values = {name: getattr(current, name) for name in names} | dict(command.changes)
    built = build_entity(type(current), values, document)
    if isinstance(built, list):
        return built
    if problems := _check_errors(document, built):
        return problems
    changed = list(command.changes)
    after = replace(document, entities=MappingProxyType({**document.entities, entity_id: built}))
    return Handled(
        document=after,
        command=ModifyEntity(
            id=entity_id, changes=MappingProxyType({name: getattr(built, name) for name in changed})
        ),
        label=f"Change {_title(changed[0])}"
        if len(changed) == 1 and not isinstance(current, Expectation)
        else f"Edit {_title(current.kind)}",  # a check's fields read badly alone ("Expected")
        created_ids=(),
        solve=_edit_solve(after, entity_id, current, built, set(changed)),
    )


def _modify_feature(
    document: Document, current: PartFeature, command: ModifyEntity
) -> Handled | list[Error]:
    """Change a feature's fields; never its id. A sketch's plane changes with its geometry's 2D
    coordinates kept, so the sketch moves to the new plane whole. An extrude's depth or
    operation changes, or what it reads, checked as when it was made. Nothing is solved: no
    constraint reaches outside a sketch, and solids are worked out when asked for."""
    names = tuple(field_types(type(current)))
    errors = [
        _error(
            ErrorCode.FIELD_UNKNOWN,
            name,
            f"a {current.kind} has no field {name!r}; fields: {', '.join(names)}",
        )
        for name in command.changes
        if name not in names
    ]
    if errors:
        return errors
    if command.changes.get("id", current.id) != current.id:
        return [_error(ErrorCode.VALUE_OUT_OF_RANGE, "id", f"a {current.kind}'s id can't change")]
    position = next(i for i, f in enumerate(document.features) if f is current)
    values = {name: getattr(current, name) for name in names} | dict(command.changes)
    built = build_feature(type(current), values, document, position=position)
    if isinstance(built, list):
        return built
    changed = list(command.changes)
    if (
        isinstance(built, Extrude)
        and {"sketch", "ids", "operation"} & set(changed)
        and (problem := _extrude_problem(document, built, position))
    ):
        return [problem]
    if (
        isinstance(built, Sketch)
        and "plane" in changed
        and (problem := _face_problem(document, built, position))
    ):
        return [problem]
    return Handled(
        document=replace(
            document, features=tuple(built if f is current else f for f in document.features)
        ),
        command=ModifyEntity(
            id=current.id,
            changes=MappingProxyType({name: getattr(built, name) for name in changed}),
        ),
        label=f"Change {_title(changed[0])}"
        if len(changed) == 1
        else f"Edit {_title(current.kind)}",
        created_ids=(),
    )


def _edit_solve(
    document: Document, id: EntityId, before: Entity, after: Entity, changed: set[str]
) -> Request | None:
    if isinstance(after, GEOMETRY):
        held = frozenset(
            (id, path) for path in PARAMS[type(after)] if path.split(".")[0] in changed
        )
        if not held:
            return None  # only the construction flag
        return Request(
            touched=frozenset({id}), held=held, keep=frozenset({id}), edited=frozenset({id})
        )
    if not is_relation(after) or not changed - _PLACEMENT_FIELDS:
        return None
    # A dimension turned driving, or a relation re-pointed, is new to the solver.
    structural = bool(changed - _PLACEMENT_FIELDS - {"value"})
    return _relation_solve(document, id, new=structural or not is_relation(before))


def _move(document: Document, command: MoveEntities) -> Handled | list[Error]:
    """Translate the geometry in `ids`. Annotations in `ids` are skipped; they follow their refs."""
    errors: list[Error] = []
    ids = _existing_ids(document, command.ids, errors, features=False)
    dx = normalize_float(command.dx, "dx", errors)
    dy = normalize_float(command.dy, "dy", errors)
    if errors:
        return errors
    geometry = (id for id in ids if isinstance(document.entities[id], GEOMETRY))
    what = "geometry moved together"
    if mixed := part.one_sketch(document, geometry, field="ids", what=what):
        return [mixed]
    moved: dict[EntityId, Entity] = {}
    for entity_id in ids:
        entity = document.entities[entity_id]
        if not isinstance(entity, GEOMETRY):
            continue
        values = {name: getattr(entity, name) for name in field_types(type(entity))}
        built = build_entity(type(entity), _translated(entity, values, dx, dy), document)
        if isinstance(built, list):
            return [
                Error(code=e.code, message=f"moving {entity_id!r}: {e.message}", field="ids")
                for e in built
            ]
        moved[entity_id] = built
    after = replace(document, entities=MappingProxyType({**document.entities, **moved}))
    return Handled(
        document=after,
        command=MoveEntities(ids=ids, dx=dx, dy=dy),
        label=_plural_label("Move", document, ids),
        created_ids=(),
        solve=Request(
            touched=frozenset(moved),
            held=frozenset(p for id in moved for p in entity_params(after, id)),
            edited=frozenset(moved),
        )
        if moved
        else None,
    )


def _translated(
    entity: Geometry, values: dict[str, object], dx: float, dy: float
) -> dict[str, object]:
    def shift(p: Point2) -> Point2:
        return Point2(x=p.x + dx, y=p.y + dy)

    match entity:
        case Point(position=position):
            return values | {"position": shift(position)}
        case Line(start=start, end=end):
            return values | {"start": shift(start), "end": shift(end)}
        case Circle(center=center) | Arc(center=center):
            return values | {"center": shift(center)}
        case Rectangle(corner=corner):
            return values | {"corner": shift(corner)}


def _delete(document: Document, command: DeleteEntities) -> Handled | list[Error]:
    """Delete `ids`, and in the same delta every dimension and constraint that refers to a
    deleted entity. A sketch takes everything drawn in it along. Removing constraints never
    breaks the others, so nothing is solved."""
    errors: list[Error] = []
    ids = _existing_ids(document, command.ids, errors, features=True)
    if errors:
        return errors
    # Features named, and those that read them (an extrude of a deleted sketch), in order.
    gone: set[EntityId] = {id for id in ids if id not in document.entities}
    for feature in document.features:
        if graph.reads(feature) & gone:
            gone.add(feature.id)
    sketches = {f.id for f in document.features if f.id in gone and isinstance(f, Sketch)}
    doomed = set(ids) - gone
    if sketches:
        doomed |= {
            id
            for id, entity in document.entities.items()
            if isinstance(entity, GEOMETRY) and entity.sketch in sketches
        }
    index = referrers(document)
    for entity_id in tuple(doomed):
        doomed |= index.get(entity_id, frozenset())
    return Handled(
        document=replace(
            document,
            entities=MappingProxyType(
                {i: e for i, e in document.entities.items() if i not in doomed}
            ),
            features=tuple(f for f in document.features if f.id not in gone),
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
    if mixed := part.one_sketch(document, (a_id, b_id), field="b", what="a fillet's lines"):
        return [mixed]

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
    corner_refs: set[Ref] = set()
    held: set[tuple[EntityId, str]] = set()
    for entity_id, line, touch in ((a_id, lines["a"], touch_a), (b_id, lines["b"], touch_b)):
        moved = "start" if line.start == corner else "end"
        values = {name: getattr(line, name) for name in field_types(Line)} | {moved: touch}
        built = build_entity(Line, values, document)
        if isinstance(built, list):
            return built
        trimmed[entity_id] = built
        corner_refs.add(Ref(entity=entity_id, feature=Feature(moved)))
        held |= {(entity_id, f"{moved}.x"), (entity_id, f"{moved}.y")}
    construction = lines["a"].construction and lines["b"].construction
    arc_values = _arc_values(center, radius, touch_a, touch_b) | {
        "construction": construction,
        "sketch": lines["a"].sketch,
    }
    arc = build_entity(Arc, arc_values, document)
    if isinstance(arc, list):
        return arc
    arc_id, next_id = _resolve_id(document, command.id, errors)
    if errors:
        return errors
    # The corner the two ends met at is gone, so a constraint holding them together goes too.
    consumed = {
        id
        for id, entity in document.entities.items()
        if isinstance(entity, Constraint)
        and entity.type is ConstraintType.COINCIDENT
        and set(entity.refs) == corner_refs
    }
    kept = {id: e for id, e in document.entities.items() if id not in consumed}
    return Handled(
        document=replace(
            document, entities=MappingProxyType({**kept, **trimmed, arc_id: arc}), next_id=next_id
        ),
        command=FilletCorner(a=a_id, b=b_id, radius=radius, id=arc_id),
        label="Fillet Corner",
        created_ids=(arc_id,),
        solve=Request(
            touched=frozenset({a_id, b_id}),
            held=frozenset(held),
            keep=frozenset({a_id, b_id}),
            edited=frozenset({a_id, b_id}),
        ),
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


def _existing_ids(
    document: Document, ids: object, errors: list[Error], *, features: bool
) -> tuple[EntityId, ...]:
    """Normalized ids, each once and in the order given, all present in `document`: entities,
    and the part's features too when `features` allows them."""
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
            found = part.feature(document, entity_id)
            if found is not None and not features:
                message = f"{entity_id!r} is {with_article(found.kind)}; name geometry instead"
                errors.append(Error(code=ErrorCode.ENTITY_WRONG_KIND, message=message, field=field))
                continue
            if found is None:
                errors.append(
                    Error(
                        code=ErrorCode.ENTITY_NOT_FOUND,
                        message=f"no entity {entity_id!r}",
                        field=field,
                    )
                )
                continue
        resolved[entity_id] = None
    return tuple(resolved)


def _plural_label(verb: str, document: Document, ids: tuple[EntityId, ...]) -> str:
    if len(ids) == 1:
        entity = document.entities.get(ids[0]) or part.feature(document, ids[0])
        assert entity is not None
        return f"{verb} {_title(entity.kind)}"
    return f"{verb} {len(ids)} Entities"


def _error(code: ErrorCode, field: str, message: str) -> Error:
    return Error(code=code, message=message, field=field)


def _title(snake: str) -> str:
    return snake.replace("_", " ").title()
