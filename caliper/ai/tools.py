"""The assistant's tools: Caliper's commands and queries, and nothing else.

There is one tool per `Command` kind, generated from the contract, so the tools follow the
contract as it grows. A call's arguments are the command's fields in the JSON form command
scripts use, decoded by the same codec replay uses and validated by the bus, which rejects
bad input with its usual structured errors. Three tools repeat geometry, mirror and linear and
circular pattern (`caliper.ai.patterns`): each is several of those same commands in one call,
kept or undone together. A few query tools let the model look before and after it acts: the
document, single entities, distances, checks, and solve status.

Everything runs in a `Workspace`: a scratch bus on a copy of the document. The user's
document is untouched until they accept what the workspace did, which is its `commands`: the
resolved commands, replayable on the original document to reach exactly the same result.
"""

import inspect
from collections.abc import Callable, Mapping
from dataclasses import MISSING, dataclass, fields
from enum import StrEnum
from types import NoneType, UnionType
from typing import Union, get_args, get_origin, get_type_hints

from caliper.ai import construct, patterns
from caliper.ai.context import describe
from caliper.ai.model import ToolCall, ToolOutcome, ToolSpec
from caliper.contracts.commands import Applied, Command, Delta, Rejected
from caliper.contracts.document import (
    POINT_FEATURES,
    AngleDimension,
    Constraint,
    DistanceDimension,
    Document,
    EntityId,
    Feature,
    Point2,
    RadialDimension,
    Ref,
)
from caliper.contracts.errors import Error
from caliper.contracts.queries import Expectation, Metric
from caliper.engine.commands.bus import Bus
from caliper.engine.commands.handlers import Executed
from caliper.engine.io.canonical import JSON
from caliper.engine.io.codec import COMMAND_KINDS, DecodeError, decode_command, encode

# --- Schemas from the contract ----------------------------------------------------------


def _schema(tp: object) -> dict[str, object]:
    """A JSON Schema for a contract field type, as the codec decodes it."""
    if tp is float:
        return {"type": "number"}
    if tp is bool:
        return {"type": "boolean"}
    if tp is EntityId or tp is str:
        return {"type": "string"}
    if tp is Point2:
        return {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
            "required": ["x", "y"],
            "additionalProperties": False,
        }
    if tp is Ref:
        return {
            "type": "object",
            "properties": {
                "entity": {"type": "string"},
                "feature": {"type": "string", "enum": [f.value for f in Feature]},
            },
            "required": ["entity", "feature"],
            "additionalProperties": False,
        }
    if isinstance(tp, type) and issubclass(tp, StrEnum):
        return {"type": "string", "enum": [member.value for member in tp]}
    origin = get_origin(tp)
    if origin is tuple:
        return {"type": "array", "items": _schema(get_args(tp)[0])}
    if origin is Mapping:
        return {
            "type": "object",
            "description": 'Field name to new value, e.g. {"width": 120}.',
            "additionalProperties": True,
        }
    if origin in (UnionType, Union):  # EntityId | None is a typing.Union
        members = [m for m in get_args(tp) if m is not NoneType]
        options = [_schema(m) for m in members]
        if NoneType in get_args(tp):
            options.append({"type": "null"})
        return options[0] if len(options) == 1 else {"anyOf": options}
    raise TypeError(f"no schema for {tp!r}")


def _description(cls: type) -> str:
    doc = inspect.getdoc(cls) or ""
    if not doc or doc.startswith(cls.__name__ + "("):  # the one dataclasses writes
        return f"Caliper's {cls.__name__} command."
    return doc


_FIELD_DESCRIPTIONS = {
    "construction": (
        "True for layout geometry: centre lines and reference circles that other geometry is "
        "constrained and dimensioned to. It is solved like any geometry but is not part of the "
        "part's outline."
    ),
}
"""What a command field is for, where its name and type don't say."""


def _field_schema(name: str, tp: object) -> dict[str, object]:
    schema = _schema(tp)
    if name in _FIELD_DESCRIPTIONS:
        return {**schema, "description": _FIELD_DESCRIPTIONS[name]}
    return schema


def _command_spec(kind: str, cls: type) -> ToolSpec:
    hints = get_type_hints(cls)
    names = [f.name for f in fields(cls)]
    required = [
        f.name for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING
    ]
    return ToolSpec(
        name=kind,
        description=_description(cls),
        input_schema={
            "type": "object",
            "properties": {name: _field_schema(name, hints[name]) for name in names},
            "required": required,
            "additionalProperties": False,
        },
    )


CONVENTIONS = (
    "Units are millimetres and degrees, and y points up. A rectangle's corner is its bottom-left "
    "corner. Arcs run counter-clockwise from start_angle through sweep_angle. Refer to entities by "
    "the ids Caliper gives them, which you'll see in tool results and in the document summary."
    "\n\n"
    "Build a sketch the way a designer does: the layout first, then the part. Start with "
    "construction geometry (construction: true) that holds the design intent: centre lines, "
    "and the circles and lines the sizes are measured on, such as a gear's pitch, root, and tip "
    "circles or a bolt circle. Drive those with dimensions. Then draw the part's outline on the "
    "layout and tie it on with constraints (coincident, tangent, concentric, symmetric) instead "
    "of relying on coordinates you worked out, so changing one layout dimension moves "
    "everything that depends on it. Finish one feature, such as a tooth or a hole, and check it "
    "before repeating it from the same layout."
    "\n\n"
    "Trace an outline with create_outline, one call for its lines and arcs with their "
    "joints (and tangency where you ask), and an arc through three known points with "
    "create_arc_through_points, rather than working out centres yourself. "
    "Repeat rather than redraw: mirror the second half of a symmetric feature with "
    "mirror_entities, repeat rows and grids with linear_pattern, and repeat round a centre "
    "(holes, teeth, a star's points) with circular_pattern, one call each. The copies stay "
    "tied to the original, so constrain and dimension the original first. Keep the "
    "constraints lean: dimension each feature from the edge, centre line, or neighbour it "
    "relates to, with the label just outside it; state each relationship once, since Caliper "
    "rejects one the others already imply (show a size that's already fixed as a dimension with "
    "no value); and check the degrees of freedom with solve_status after each feature."
)
"""What any model driving these tools needs to know, whether in the app or over MCP."""

_REFS = {"type": "array", "items": _schema(Ref)}
_IDS = {"type": "array", "items": {"type": "string"}}

METRICS = {
    Metric.DISTANCE: "refs [a, b]: the straight distance between two point features",
    Metric.DISTANCE_X: "refs [a, b]: the horizontal distance between them, never negative",
    Metric.DISTANCE_Y: "refs [a, b]: the vertical distance between them, never negative",
    Metric.POSITION_X: "refs [point]: its x from the origin, signed",
    Metric.POSITION_Y: "refs [point]: its y from the origin, signed",
    Metric.BBOX_WIDTH: "ids: the width of their bounding box (no ids: the whole sketch)",
    Metric.BBOX_HEIGHT: "ids: the height of their bounding box (no ids: the whole sketch)",
    Metric.AREA: "ids: the area inside one closed profile",
    Metric.DIMENSION_VALUE: "ids [dimension]: what the dimension measures",
}
"""What each check metric measures and reads, for the model (AI-5): the contract's names alone
left it guessing, and it ordered points defensively."""

QUERY_TOOLS = (
    ToolSpec(
        name="inspect_document",
        description=(
            "The sketch as it is now, with your unapplied changes: counts, bounds, solve status, "
            "the selection, "
            "and entities (selection and focus first, then nearby geometry, then the rest, up "
            "to limit). Use focus to see particular entities in large sketches."
        ),
        input_schema={
            "type": "object",
            "properties": {"focus": _IDS, "limit": {"type": "integer", "minimum": 1}},
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="inspect_entities",
        description=(
            "Everything about some entities: their stored fields, the location of each point "
            "feature, what a dimension measures, and the constraints and dimensions on them."
        ),
        input_schema={
            "type": "object",
            "properties": {"ids": _IDS},
            "required": ["ids"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="measure_distance",
        description="The distance from point feature a to point feature b, with dx and dy.",
        input_schema={
            "type": "object",
            "properties": {"a": _schema(Ref), "b": _schema(Ref)},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="run_check",
        description=(
            "Check a measurement against what the request asked for. Passes when "
            "|actual - expected| <= tolerance. Checks you run are shown to the user with your "
            "changes, so run one for each measurement the request states. Running a check of "
            "the same measurement again replaces the earlier one. Metrics: "
            + "; ".join(f"{metric.value}: {meaning}" for metric, meaning in METRICS.items())
            + "."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": [m.value for m in Metric]},
                "expected": {"type": "number"},
                "tolerance": {"type": "number", "minimum": 0},
                "refs": _REFS,
                "ids": _IDS,
            },
            "required": ["metric", "expected", "tolerance"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="remove_check",
        description=(
            "Take a check you ran off your pending changes: the one of this metric, refs, and "
            "ids, whatever value it expected. To correct a check's value, run it again instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": [m.value for m in Metric]},
                "refs": _REFS,
                "ids": _IDS,
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="solve_status",
        description="Degrees of freedom and constraint health: under, fully, over, conflicting.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="applicable_constraints",
        description=(
            "Which constraint and dimension types fit a selection of references, with the "
            "references in the order create_constraint expects, or why a type doesn't fit."
        ),
        input_schema={
            "type": "object",
            "properties": {"refs": _REFS},
            "required": ["refs"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="undo",
        description="Undo your last change that isn't applied yet. Applied changes are kept.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
)

REPEAT_TOOLS = (patterns.MIRROR, patterns.PATTERN, patterns.CIRCULAR)
DRAWING_TOOLS = (construct.ARC_THROUGH, construct.OUTLINE)

TOOLS: tuple[ToolSpec, ...] = (
    *(_command_spec(kind, cls) for kind, cls in COMMAND_KINDS.items()),
    *DRAWING_TOOLS,
    *REPEAT_TOOLS,
    *QUERY_TOOLS,
)


# --- The workspace ----------------------------------------------------------------------


class _ToolError(Exception):
    """A tool call that can't run as asked. Reported to the model, never raised further."""

    def __init__(self, content: JSON) -> None:
        super().__init__(str(content))
        self.content = content


@dataclass
class Workspace:
    """A scratch bus on a copy of `base`, where one turn's tool calls run."""

    base: Document
    selection: frozenset[EntityId] = frozenset()

    def __post_init__(self) -> None:
        self._bus = Bus(self.base)
        self._applied: list[Applied] = []
        self._calls: list[tuple[int, str]] = []
        """Each call that changed something: how many of `_applied` it made, and its label.
        Undo takes back a whole call, so a mirror or pattern goes in one step."""
        self._checks: list[Expectation] = []
        self._handlers: Mapping[str, Callable[[Mapping[str, object]], JSON]] = {
            patterns.MIRROR.name: lambda arguments: self._repeat(patterns.mirror, arguments),
            patterns.PATTERN.name: lambda arguments: self._repeat(
                patterns.linear_pattern, arguments
            ),
            patterns.CIRCULAR.name: lambda arguments: self._repeat(
                patterns.circular_pattern, arguments
            ),
            construct.ARC_THROUGH.name: lambda arguments: self._repeat(
                construct.arc_through_points, arguments
            ),
            construct.OUTLINE.name: lambda arguments: self._repeat(construct.outline, arguments),
            "inspect_document": self._inspect_document,
            "inspect_entities": self._inspect_entities,
            "measure_distance": self._measure_distance,
            "run_check": self._run_check,
            "remove_check": self._remove_check,
            "solve_status": self._solve_status,
            "applicable_constraints": self._applicable_constraints,
            "undo": self._undo,
        }

    @property
    def document(self) -> Document:
        return self._bus.document

    @property
    def commands(self) -> tuple[Command, ...]:
        """The resolved commands that took `base` to `document`, in order."""
        return tuple(applied.command for applied in self._applied)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(applied.label for applied in self._applied)

    @property
    def executed(self) -> Executed:
        """How `commands` took `base` to `document`: accepting them commits these outcomes
        instead of solving every command again (`already`)."""
        return Executed(base=self.base, steps=tuple(self._applied), result=self._bus.document)

    @property
    def checks(self) -> tuple[Expectation, ...]:
        """Each distinct check the model ran, in the order it first ran it."""
        return tuple(self._checks)

    def call(self, call: ToolCall) -> ToolOutcome:
        arguments: object = call.arguments  # a model can send anything
        if not isinstance(arguments, Mapping):
            return ToolOutcome(call, {"error": "arguments must be an object"}, is_error=True)
        try:
            if call.name in COMMAND_KINDS:
                return ToolOutcome(call, self._command(call.name, arguments))
            handler = self._handlers.get(call.name)
            if handler is None:
                raise _ToolError({"error": f"no tool named {call.name!r}"})
            return ToolOutcome(call, handler(arguments))
        except _ToolError as problem:
            return ToolOutcome(call, problem.content, is_error=True)

    # --- Commands -----------------------------------------------------------------------

    def _command(self, kind: str, arguments: Mapping[str, object]) -> JSON:
        try:
            command = decode_command({**arguments, "kind": kind}, kind)
        except (DecodeError, TypeError, ValueError) as e:
            raise _ToolError({"error": str(e)}) from e
        result = self._bus.execute(command)
        if isinstance(result, Rejected):
            raise _ToolError({"rejected": [_error(e) for e in result.errors]})
        assert isinstance(result, Applied)
        if not (result.delta.before or result.delta.after):
            return {"applied": True, "changed": "nothing: the document already was that way"}
        self._applied.append(result)
        self._calls.append((1, result.label))
        # No echo of the command: what it created and changed, in stored form, says all the
        # resolved command would (ids, inferred kinds, canonical order), in half the tokens.
        return {
            "applied": True,
            "label": result.label,
            "created": [str(id) for id in result.created_ids],
            "changed": _changes(result.delta),
        }

    def _repeat(
        self,
        make: Callable[[Document, Mapping[str, object], patterns.Run], patterns.Repeated],
        arguments: Mapping[str, object],
    ) -> JSON:
        """A mirror or pattern: its commands run one by one, and are kept only if all are."""
        steps: list[Applied] = []

        def run(command: Command) -> Applied:
            result = self._bus.execute(command)
            if isinstance(result, Rejected):
                raise patterns.PatternError(
                    {
                        "rejected": [_error(e) for e in result.errors],
                        "at": f"{command.kind} {encode(command)}",
                        "undone": "everything this call did before it",
                    }
                )
            assert isinstance(result, Applied)
            steps.append(result)
            return result

        try:
            made = make(self.document, arguments, run)
        except patterns.PatternError as problem:
            for _ in steps:
                self._bus.undo()
            raise _ToolError(problem.content) from problem
        self._applied.extend(steps)
        self._calls.append((len(steps), made.label))
        delta = _merged([step.delta for step in steps])
        shown = set(made.copies) | {id for ids in made.copies.values() for id in _listed(ids)}
        shown |= {str(id) for id in made.created}
        return {
            "applied": True,
            "label": made.label,
            **({"created": made.created} if made.created else {}),
            "copies": made.copies,
            "construction": made.construction,
            "constraints": made.constraints,
            "dimensions": made.dimensions,
            **({"skipped": made.skipped} if made.skipped else {}),
            **({"note": made.note} if made.note else {}),
            # The copies in full; the constraints and layout by id, above.
            "changed": _changes(delta, added=lambda id: id in shown or id in made.construction),
        }

    def _undo(self, arguments: Mapping[str, object]) -> JSON:
        if not self._calls:
            raise _ToolError({"error": "nothing to undo: no unapplied changes"})
        steps, label = self._calls.pop()
        changes = []
        for _ in range(steps):
            change = self._bus.undo()
            assert change is not None
            changes.append(change.delta)
            self._applied.pop()
        return {"undone": label, "changed": _changes(_merged(changes))}

    # --- Queries ------------------------------------------------------------------------

    def _inspect_document(self, arguments: Mapping[str, object]) -> JSON:
        limit = arguments.get("limit", 40)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise _ToolError({"error": "limit must be a positive integer"})
        focus = _ids(arguments.get("focus", []), "focus")
        return describe(self.document, selection=self.selection, focus=focus, limit=limit)

    def _inspect_entities(self, arguments: Mapping[str, object]) -> JSON:
        queries = self._bus.queries
        found: dict[str, JSON] = {}
        for id in _ids(arguments.get("ids"), "ids"):
            entity = self.document.entities.get(id)
            if entity is None:
                found[id] = {"error": f"no entity {id!r}"}
                continue
            data = encode(entity)
            assert isinstance(data, dict)
            if isinstance(entity, DistanceDimension | RadialDimension | AngleDimension):
                measured = queries.dimension_value(id)
                data["measured"] = None if isinstance(measured, Error) else measured
            elif not isinstance(entity, Constraint):
                points: dict[str, JSON] = {}
                for feature in sorted(POINT_FEATURES[type(entity)]):
                    at = queries.feature_point(Ref(entity=id, feature=feature))
                    if isinstance(at, Point2):
                        points[feature.value] = {"x": at.x, "y": at.y}
                data["points"] = points
                data["constrained_by"] = list(queries.constraints_on([id]))
            found[id] = data
        return found

    def _measure_distance(self, arguments: Mapping[str, object]) -> JSON:
        a, b = _ref(arguments.get("a"), "a"), _ref(arguments.get("b"), "b")
        distance = self._bus.queries.measure_distance(a, b)
        if isinstance(distance, Error):
            raise _ToolError({"error": _error(distance)})
        return {"distance": distance.value, "dx": distance.dx, "dy": distance.dy}

    def _run_check(self, arguments: Mapping[str, object]) -> JSON:
        refs = arguments.get("refs", [])
        if not isinstance(refs, list):
            raise _ToolError({"error": "refs must be a list of references"})
        expectation = Expectation(
            metric=arguments.get("metric"),  # type: ignore[arg-type]  # check validates it
            expected=arguments.get("expected"),  # type: ignore[arg-type]
            tolerance=arguments.get("tolerance"),  # type: ignore[arg-type]
            refs=tuple(_ref(r, f"refs[{i}]") for i, r in enumerate(refs)),
            ids=_ids(arguments.get("ids", []), "ids"),
        )
        result = self._bus.queries.check(expectation)
        replaced: Expectation | None = None
        if result.error is None:
            normalized = Expectation(
                metric=Metric(expectation.metric),
                expected=float(expectation.expected),
                tolerance=float(expectation.tolerance),
                refs=expectation.refs,
                ids=expectation.ids,
            )
            # The same measurement again replaces the earlier check: a check run with the
            # wrong value can be put right instead of staying on the proposal, failing (AI-2).
            for n, earlier in enumerate(self._checks):
                if (earlier.metric, earlier.refs, earlier.ids) == (
                    normalized.metric,
                    normalized.refs,
                    normalized.ids,
                ):
                    if earlier != normalized:
                        replaced, self._checks[n] = earlier, normalized
                    break
            else:
                self._checks.append(normalized)
        return {
            "passed": result.passed,
            "actual": result.actual,
            "error": None if result.error is None else _error(result.error),
            **(
                {}
                if replaced is None
                else {"replaced": {"expected": replaced.expected, "tolerance": replaced.tolerance}}
            ),
        }

    def _remove_check(self, arguments: Mapping[str, object]) -> JSON:
        """Take back a check (AI-2): matched by what it measures, not the value it expected."""
        refs = arguments.get("refs", [])
        if not isinstance(refs, list):
            raise _ToolError({"error": "refs must be a list of references"})
        name = arguments.get("metric")
        if name not in {m.value for m in Metric}:
            raise _ToolError({"error": f"no metric {name!r}"})
        metric = Metric(str(name))
        measurement = (
            metric,
            tuple(_ref(r, f"refs[{i}]") for i, r in enumerate(refs)),
            _ids(arguments.get("ids", []), "ids"),
        )
        for n, check in enumerate(self._checks):
            if (check.metric, check.refs, check.ids) == measurement:
                del self._checks[n]
                return {"removed": {"expected": check.expected, "tolerance": check.tolerance}}
        raise _ToolError({"error": "you ran no check of that measurement"})

    def _solve_status(self, arguments: Mapping[str, object]) -> JSON:
        status = self._bus.queries.solve_status()
        return {
            "state": status.state.value,
            "dof": status.dof,
            "conflicting": list(status.conflicting),
            "redundant": list(status.redundant),
        }

    def _applicable_constraints(self, arguments: Mapping[str, object]) -> JSON:
        raw = arguments.get("refs")
        if not isinstance(raw, list):
            raise _ToolError({"error": "refs must be a list of references"})
        refs = [_ref(r, f"refs[{i}]") for i, r in enumerate(raw)]
        return [
            {
                "type": option.type.value,
                "refs": encode(option.refs),
                "fits": option.error is None,
                **({} if option.error is None else {"why_not": option.error.message}),
            }
            for option in self._bus.queries.applicable_constraints(refs)
        ]


# --- Helpers ----------------------------------------------------------------------------


def _error(error: Error) -> JSON:
    return {
        "code": error.code.value,
        "message": error.message,
        "field": error.field,
        "ids": list(error.ids),
    }


MODIFIED_SHOWN = 8
"""Entities a result shows in full when a solve moved them; the rest are listed by id."""


def _changes(delta: Delta, added: Callable[[EntityId], bool] = lambda id: True) -> JSON:
    """What a change did. Everything `added` picks out of what it added is shown in full. A
    solve can move many entities a little (every point of a star, say): the first
    `MODIFIED_SHOWN` are shown, the others named in `also_modified`, and `inspect_entities`
    gives them in full."""
    modified = sorted(delta.modified)
    changes: dict[str, JSON] = {
        "added": {id: encode(delta.after[id]) for id in sorted(delta.added) if added(id)},
        "modified": {id: encode(delta.after[id]) for id in modified[:MODIFIED_SHOWN]},
        "removed": [str(id) for id in sorted(delta.removed)],
    }
    if len(modified) > MODIFIED_SHOWN:
        changes["also_modified"] = [str(id) for id in modified[MODIFIED_SHOWN:]]
    return changes


def _merged(deltas: list[Delta]) -> Delta:
    """One delta for several in a row: each entity as it was before the first to touch it and
    after the last."""
    before: dict[EntityId, object] = {}
    after: dict[EntityId, object] = {}
    missing = object()
    for delta in deltas:
        for id in delta.before.keys() | delta.after.keys():
            before.setdefault(id, delta.before.get(id, missing))
            after[id] = delta.after.get(id, missing)
    touched = [id for id in before if before[id] is not after[id]]
    return Delta(
        before={id: before[id] for id in touched if before[id] is not missing},  # type: ignore[misc]
        after={id: after[id] for id in touched if after[id] is not missing},  # type: ignore[misc]
        next_id_before=deltas[0].next_id_before if deltas else 0,
        next_id_after=deltas[-1].next_id_after if deltas else 0,
    )


def _listed(value: JSON) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else [str(value)]


def _ids(value: object, field: str) -> tuple[EntityId, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _ToolError({"error": f"{field} must be a list of entity ids"})
    return tuple(EntityId(v) for v in value)


def _ref(value: object, field: str) -> Ref:
    if not isinstance(value, Mapping) or set(value) != {"entity", "feature"}:
        raise _ToolError({"error": f"{field} must be an object with entity and feature"})
    entity, feature = value["entity"], value["feature"]
    if not isinstance(entity, str) or feature not in {f.value for f in Feature}:
        raise _ToolError(
            {"error": f"{field} names no feature; features: {[f.value for f in Feature]}"}
        )
    return Ref(entity=EntityId(entity), feature=Feature(feature))
