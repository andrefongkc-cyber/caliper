"""Project files: canonical JSON snapshots of a Document (ADR 0005).

A file may carry an optional `history` section: the resolved commands, in order. It is off
by default, and `export` never writes it.
"""

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from caliper.contracts.commands import Command
from caliper.contracts.document import FIRST_SKETCH, Document, Entity, EntityId, PartFeature, Plane
from caliper.contracts.errors import Error, ErrorCode, LoadError
from caliper.engine.commands.validation import (
    build_entity,
    build_feature,
    field_types,
    normalize_id,
)
from caliper.engine.io import canonical
from caliper.engine.io.canonical import JSON
from caliper.engine.io.codec import DecodeError, decode_command, decode_document, encode

FORMAT = "caliper.document"
SCHEMA_VERSION = 4
UNITS: Mapping[str, str] = MappingProxyType({"angle": "deg", "length": "mm"})

type Migration = Callable[[dict[str, object]], dict[str, object]]


def _v1_to_v2(data: dict[str, object]) -> dict[str, object]:
    """V1.5 sketch constraints: geometry gains `construction`, dimensions a driving `value`.

    Every V1 entity keeps its meaning: nothing was construction geometry, and every
    dimension was driven.
    """
    document = data.get("document")
    if not isinstance(document, dict) or not isinstance(document.get("entities"), dict):
        return data  # malformed; decoding reports it with a path
    added: dict[object, dict[str, object]] = {
        "line": {"construction": False},
        "circle": {"construction": False},
        "arc": {"construction": False},
        "rectangle": {"construction": False},
        "distance_dimension": {"value": None},
        "radial_dimension": {"value": None},
    }
    entities = {
        id: entity | added.get(entity.get("kind"), {}) if isinstance(entity, dict) else entity
        for id, entity in document["entities"].items()
    }
    return data | {"document": document | {"entities": entities}}


def _v2_to_v3(data: dict[str, object]) -> dict[str, object]:
    """Checks are stored in the document (C-1, ADR 0010): a new entity kind, `check`.

    A schema-2 file has none, and everything in it keeps its meaning, so nothing changes.
    The version moves so that a Caliper too old to know checks says to update, rather than
    failing on an unknown kind.
    """
    return data


_GEOMETRY_KINDS = frozenset({"point", "line", "circle", "arc", "rectangle"})


def _v3_to_v4(data: dict[str, object]) -> dict[str, object]:
    """V2's part (ADR 0011): features in order, and geometry in a sketch.

    Everything a schema-3 file holds goes into one sketch on XY: `e0`, the sketch a new part
    starts with, which the engine never allocates, so every id and `next_id` stay as they
    were. A file that already used `e0` for something gets the next free `e{n}` instead, with
    `next_id` moved past it, as allocating it would have.
    """
    document = data.get("document")
    if not isinstance(document, dict) or not isinstance(document.get("entities"), dict):
        return data  # malformed; decoding reports it with a path
    entities: dict[object, object] = document["entities"]
    next_id = document.get("next_id")
    sketch, moved = str(FIRST_SKETCH), {}
    if sketch in entities and isinstance(next_id, int) and not isinstance(next_id, bool):
        number = next_id
        while f"e{number}" in entities:
            number += 1
        sketch, moved = f"e{number}", {"next_id": number + 1}
    placed = {
        id: entity | {"sketch": sketch}
        if isinstance(entity, dict) and entity.get("kind") in _GEOMETRY_KINDS
        else entity
        for id, entity in entities.items()
    }
    features = [{"id": sketch, "kind": "sketch", "plane": Plane.XY.value}]
    return data | {"document": document | {"entities": placed, "features": features} | moved}


MIGRATIONS: dict[int, Migration] = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4}
"""MIGRATIONS[n] upgrades the data of a schema-n file to schema n+1.

Each migration is a pure function over raw JSON data and needs a fixture test: an old file
in, the expected upgraded data out.
"""

_TOP_LEVEL_KEYS = {"document", "format", "schema_version", "units"}
_OPTIONAL_TOP_LEVEL_KEYS = {"history"}


@dataclass(frozen=True, slots=True, kw_only=True)
class Snapshot:
    """Everything a project file holds, as read."""

    document: Document
    history: tuple[Command, ...] | None
    """None when the file has no history section."""
    schema_version: int
    """The version the file was written with, before any migration."""


def dumps(document: Document, *, history: Sequence[Command] | None = None) -> str:
    data: dict[str, JSON] = {
        "document": encode(document),
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "units": dict(UNITS),
    }
    if history is not None:
        data["history"] = encode(tuple(history))
    return canonical.dumps(data)


def loads(text: str) -> Document:
    return read(text).document


def read(text: str) -> Snapshot:
    data = canonical.parse(text)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise LoadError("not a Caliper document")
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise LoadError("schema_version must be a positive integer")
    if version > SCHEMA_VERSION:
        raise LoadError(
            f"this file uses schema version {version}, but this version of Caliper only "
            f"reads up to {SCHEMA_VERSION}; update Caliper to open it"
        )
    data = migrate(data, version)
    if unknown := data.keys() - _TOP_LEVEL_KEYS - _OPTIONAL_TOP_LEVEL_KEYS:
        raise LoadError(f"unknown top-level field(s): {', '.join(sorted(unknown))}")
    if data.get("units") != dict(UNITS):
        raise LoadError(f"units must be {dict(UNITS)}")
    try:
        document = decode_document(data.get("document"))
        history = _history(data["history"]) if "history" in data else None
    except DecodeError as e:
        raise LoadError(str(e)) from e
    return Snapshot(document=_validated(document), history=history, schema_version=version)


def migrate(data: dict[str, object], version: int) -> dict[str, object]:
    while version < SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
        data["schema_version"] = version
    return data


def save(document: Document, path: Path, *, history: Sequence[Command] | None = None) -> None:
    """Write atomically, so an interrupted save never leaves a half-written project."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(dumps(document, history=history).encode("utf-8"))
    os.replace(temporary, path)


def load(path: Path) -> Document:
    return read_file(path).document


def read_file(path: Path) -> Snapshot:
    try:
        return read(path.read_bytes().decode("utf-8"))
    except UnicodeDecodeError as e:
        raise LoadError(f"{path} is not UTF-8") from e


def _history(data: object) -> tuple[Command, ...]:
    """Structure only: history records what ran, so its commands aren't re-validated."""
    if not isinstance(data, list):
        raise DecodeError("history", "must be a list of commands")
    return tuple(decode_command(item, f"history[{i}]") for i, item in enumerate(data))


def _validated(document: Document) -> Document:
    """Normalize every feature and entity and apply the same rules commands follow."""
    errors: list[Error] = []
    features = _validated_features(document, errors)
    document = replace(document, features=features)
    entities: dict[EntityId, Entity] = {}
    for entity_id in sorted(document.entities):
        where = f"document.entities.{entity_id}"
        id_errors: list[Error] = []
        normalize_id(entity_id, where, id_errors)
        entity = document.entities[entity_id]
        values = {name: getattr(entity, name) for name in field_types(type(entity))}
        built = build_entity(type(entity), values, document)
        if isinstance(built, list):
            id_errors.extend(built)
        else:
            entities[entity_id] = built
        errors.extend(
            Error(code=e.code, message=e.message, field=f"{where}.{e.field}" if e.field else where)
            for e in id_errors
        )
    if errors:
        raise LoadError("invalid document", errors)
    return replace(document, entities=MappingProxyType(entities))


def _validated_features(document: Document, errors: list[Error]) -> tuple[PartFeature, ...]:
    """Each feature built as a command would build it at its place in the order: ids valid and
    unique across features and entities, a sketch's plane one of the three, an extrude's depth
    positive and its sketch one before it. An extrude whose profile a later edit broke still
    loads, and fails when recomputed, with the reason."""
    features: list[PartFeature] = []
    seen: set[EntityId] = set()
    for index, feature in enumerate(document.features):
        where = f"document.features[{index}]"
        found: list[Error] = []
        id = normalize_id(feature.id, "id", found)
        if not found and (id in seen or id in document.entities):
            found.append(
                Error(code=ErrorCode.ID_TAKEN, message=f"id {id!r} is used twice", field="id")
            )
        seen.add(id)
        values = {name: getattr(feature, name) for name in field_types(type(feature))}
        built = build_feature(type(feature), values, document, position=index)
        if isinstance(built, list):
            found += built
        else:
            features.append(built)
        errors.extend(
            Error(code=e.code, message=e.message, field=f"{where}.{e.field}") for e in found
        )
    return tuple(features)
