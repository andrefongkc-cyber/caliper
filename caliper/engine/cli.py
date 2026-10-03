"""Headless command line: python -m caliper.engine <command>.

replay SCRIPT [-o OUTPUT] [--history]   run a command script and write the .caliper file
inspect FILE                            summarize a .caliper file: entities, dimension
                                        values, constraints, degrees of freedom, conflicts
export FILE [-o OUTPUT]                 write a copy that is safe to send: latest schema,
                                        no history section
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import fields
from enum import StrEnum
from pathlib import Path

from caliper.contracts.commands import Command, Rejected
from caliper.contracts.document import (
    AngleDimension,
    Constraint,
    DistanceDimension,
    Document,
    Entity,
    Expectation,
    Extrude,
    FaceRef,
    PartFeature,
    Point2,
    RadialDimension,
    Ref,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode, LoadError
from caliper.contracts.queries import BoundingBox, ConstraintState, Queries, SolveStatus
from caliper.engine import part
from caliper.engine.commands.bus import Bus
from caliper.engine.commands.validation import GEOMETRY
from caliper.engine.io import script, snapshot

_HIDDEN_FIELDS = frozenset({"offset", "label_angle", "value", "construction", "sketch"})
"""Fields inspect shows another way: placement not at all, measured values after `=`, the
driving value and construction flag as markers, and the sketch only when there are several."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m caliper.engine",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    replay = commands.add_parser("replay", help="run a command script and write the document")
    replay.add_argument("script", type=Path)
    replay.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    replay.add_argument(
        "--history", action="store_true", help="include the resolved commands in the file"
    )

    inspect = commands.add_parser("inspect", help="summarize a .caliper file")
    inspect.add_argument("file", type=Path)

    export = commands.add_parser(
        "export", help="write a copy without history, at the latest schema"
    )
    export.add_argument("file", type=Path)
    export.add_argument("-o", "--output", type=Path, help="write here instead of stdout")

    args = parser.parse_args(argv)
    match args.command:
        case "replay":
            return _replay(args.script, args.output, history=args.history)
        case "inspect":
            return _inspect(args.file)
        case _:
            return _export(args.file, args.output)


def _replay(script_path: Path, output: Path | None, *, history: bool) -> int:
    try:
        read = script.read(script_path.read_text(encoding="utf-8"))
    except (OSError, LoadError) as e:
        return _fail(str(e))
    bus = Bus(read.start())
    resolved: list[Command] = []
    for index, command in enumerate(read.commands):
        result = bus.execute(command)
        if isinstance(result, Rejected):
            for error in result.errors:
                where = f" {error.field}" if error.field else ""
                ids = f" (involves {', '.join(error.ids)})" if error.ids else ""
                _fail(
                    f"commands[{index}] {command.kind}:{where} [{error.code}] {error.message}{ids}"
                )
            return 1
        resolved.append(result.command)
    _write(bus.document, output, history=resolved if history else None)
    return 0


def _inspect(path: Path) -> int:
    try:
        read = snapshot.read_file(path)
    except (OSError, LoadError) as e:
        return _fail(f"{path}: {e}")
    document = read.document
    queries = Bus(document).queries

    version = str(read.schema_version)
    if read.schema_version < snapshot.SCHEMA_VERSION:
        version += f" (migrated to {snapshot.SCHEMA_VERSION} on load)"
    kinds = Counter(entity.kind for entity in document.entities.values())
    counts = ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items()))
    bounds = queries.bounding_box()

    print(path)
    print(f"  schema version  {version}")
    print(f"  units           {snapshot.UNITS['length']}, {snapshot.UNITS['angle']}")
    print(f"  entities        {len(document.entities)}" + (f": {counts}" if counts else ""))
    print(f"  next id         {document.next_id}")
    features = ", ".join(_feature(f) for f in document.features)
    print(f"  features        {len(document.features)}" + (f": {features}" if features else ""))
    print(f"  bounds          {_part_bounds(document, bounds)}")
    print(f"  solid           {_solid(queries)}")
    status = queries.solve_status()
    print(f"  sketch          {_status(status)}")
    print(f"  history         {_history(read.history)}")
    if document.entities:
        print()
    id_width = max(len(id) for id in document.entities) if document.entities else 0
    kind_width = max(len(kind) for kind in kinds) if kinds else 0
    for id in sorted(document.entities):
        entity = document.entities[id]
        line = f"  {id:<{id_width}}  {entity.kind:<{kind_width}}  {_fields(entity)}"
        if (
            len(part.sketches(document)) > 1
            and (sketch := part.sketch_of(document, id)) is not None
        ):
            line += f" (in {sketch})"
        if isinstance(entity, DistanceDimension | RadialDimension | AngleDimension):
            value = queries.dimension_value(id)
            line += f" = {value!r}" if isinstance(value, float) else f" = ? ({value.message})"
            if entity.value is not None:
                line += " (driving)"
        if isinstance(entity, Expectation):
            result = queries.check(entity)
            if result.error is not None:
                line += f" = ? ({result.error.message})"
            else:
                line += f" = {result.actual!r} ({'passes' if result.passed else 'fails'})"
        if getattr(entity, "construction", False):
            line += " (construction)"
        if id in status.entity_dof:
            line += f"  [dof {status.entity_dof[id]}]"
        if id in status.conflicting:
            line += "  [conflicting]"
        elif id in status.redundant:
            line += "  [redundant]"
        print(line)
    return 0


def _status(status: SolveStatus) -> str:
    match status.state:
        case ConstraintState.FULLY:
            return "fully constrained"
        case ConstraintState.UNDER:
            return f"under-constrained, {status.dof} DOF remaining"
        case ConstraintState.OVER:
            return f"over-constrained: {', '.join(status.redundant)} repeat others"
        case ConstraintState.CONFLICTING:
            return f"constraint conflict: {', '.join(status.conflicting)} don't hold"


def _export(path: Path, output: Path | None) -> int:
    try:
        read = snapshot.read_file(path)
    except (OSError, LoadError) as e:
        return _fail(f"{path}: {e}")
    _write(read.document, output, history=None)
    if read.history is not None:
        print(f"removed the history section ({_history(read.history)})", file=sys.stderr)
    return 0


def _write(document: Document, output: Path | None, *, history: Sequence[Command] | None) -> None:
    if output is None:
        # Bytes, not text: text mode would translate newlines on Windows.
        sys.stdout.buffer.write(snapshot.dumps(document, history=history).encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        snapshot.save(document, output, history=history)


def _history(history: tuple[Command, ...] | None) -> str:
    if history is None:
        return "none"
    return f"{len(history)} command" + ("" if len(history) == 1 else "s")


def _part_bounds(document: Document, whole: BoundingBox | Error) -> str:
    """The geometry's bounds, or each sketch's when it's in several: a box across two planes
    means nothing."""
    if isinstance(whole, BoundingBox):
        return _bounds(whole)
    if whole.code is not ErrorCode.SKETCH_MIXED:
        return "none"
    queries = Bus(document).queries
    found = []
    for sketch in part.sketches(document):
        ids = [id for id in document.entities if part.sketch_of(document, id) == sketch]
        box = queries.bounding_box(
            [id for id in ids if isinstance(document.entities[id], GEOMETRY)]
        )
        if isinstance(box, BoundingBox):
            found.append(f"{sketch}: {_bounds(box)}")
    return "; ".join(found)


def _solid(queries: Queries) -> str:
    """The part's solid: its volume and bounds, why there isn't one, or "none" before any."""
    found = queries.solid_properties()
    if isinstance(found, Error):
        return "none" if found.code is ErrorCode.SELECTION_EMPTY else f"? ({found.message})"
    box = found.bounding_box
    bounds = (
        ""
        if box is None
        else f", x {box.x_min!r} to {box.x_max!r}, y {box.y_min!r} to {box.y_max!r}, "
        f"z {box.z_min!r} to {box.z_max!r}"
    )
    return f"{found.volume!r} mm³{bounds}"


def _feature(feature: PartFeature) -> str:
    match feature:
        case Sketch(plane=plane):
            where = (
                f"{plane.face} of {plane.feature}" if isinstance(plane, FaceRef) else plane.value
            )
            return f"{feature.id} sketch on {where}"
        case Extrude(sketch=sketch, depth=depth, operation=operation, reversed=back):
            way = ", reversed" if back else ""
            return f"{feature.id} extrude of {sketch}, {depth!r} mm, {operation.value}{way}"


def _fields(entity: Entity) -> str:
    if isinstance(entity, Constraint):
        return f"{entity.type.value}: {_value(entity.refs)}"
    return ", ".join(
        f"{field.name} {_value(getattr(entity, field.name))}"
        for field in fields(entity)
        if field.name not in _HIDDEN_FIELDS and getattr(entity, field.name) != ()
    )


def _value(value: object) -> str:
    match value:
        case Point2(x=x, y=y):
            return f"({x!r}, {y!r})"
        case Ref(entity=entity, feature=feature):
            return f"{entity}.{feature}"
        case tuple():
            return ", ".join(_value(item) for item in value)
        case StrEnum():
            return value.value
        case float():
            return repr(value)
        case _:
            return str(value)


def _bounds(box: BoundingBox) -> str:
    return (
        f"x {box.x_min!r} to {box.x_max!r}, y {box.y_min!r} to {box.y_max!r} "
        f"({box.width!r} x {box.height!r} mm)"
    )


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1
