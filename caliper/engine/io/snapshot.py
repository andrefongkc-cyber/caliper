"""Project files: canonical JSON snapshots of a Document (ADR 0005).

A file may carry an optional `history` section: the resolved commands, in order. It is off
by default, and `export` never writes it.
"""

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from caliper.contracts.commands import Command
from caliper.contracts.document import Document, Entity, EntityId
from caliper.contracts.errors import Error
from caliper.engine.commands.validation import build_entity, field_types, normalize_id
from caliper.engine.io import canonical
from caliper.engine.io.canonical import JSON, LoadError
from caliper.engine.io.codec import DecodeError, decode_command, decode_document, encode

FORMAT = "caliper.document"
SCHEMA_VERSION = 1
UNITS: Mapping[str, str] = MappingProxyType({"angle": "deg", "length": "mm"})

type Migration = Callable[[dict[str, object]], dict[str, object]]

MIGRATIONS: dict[int, Migration] = {}
"""MIGRATIONS[n] upgrades the data of a schema-n file to schema n+1. None exist yet.

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
    """Normalize every entity and apply the same rules commands follow."""
    errors: list[Error] = []
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
    return Document(entities=MappingProxyType(entities), next_id=document.next_id)
