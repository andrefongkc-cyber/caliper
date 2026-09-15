"""Project files: canonical JSON snapshots of a Document (ADR 0005)."""

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

from caliper.contracts.document import Document, Entity, EntityId
from caliper.contracts.errors import Error
from caliper.engine.commands.validation import build_entity, field_types, normalize_id
from caliper.engine.io import canonical
from caliper.engine.io.canonical import LoadError
from caliper.engine.io.codec import DecodeError, decode_document, encode

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
_OPTIONAL_TOP_LEVEL_KEYS = {"history"}  # defined by ADR 0005; not written or read yet


def dumps(document: Document) -> str:
    return canonical.dumps(
        {
            "document": encode(document),
            "format": FORMAT,
            "schema_version": SCHEMA_VERSION,
            "units": dict(UNITS),
        }
    )


def loads(text: str) -> Document:
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
    except DecodeError as e:
        raise LoadError(str(e)) from e
    return _validated(document)


def migrate(data: dict[str, object], version: int) -> dict[str, object]:
    while version < SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
        data["schema_version"] = version
    return data


def save(document: Document, path: Path) -> None:
    """Write atomically, so an interrupted save never leaves a half-written project."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(dumps(document).encode("utf-8"))
    os.replace(temporary, path)


def load(path: Path) -> Document:
    try:
        return loads(path.read_bytes().decode("utf-8"))
    except UnicodeDecodeError as e:
        raise LoadError(f"{path} is not UTF-8") from e


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
