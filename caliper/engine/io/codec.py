"""Convert contract dataclasses to and from JSON-compatible data.

Encoding is exact. Decoding is structural: it rebuilds dataclasses and containers from the
shape of the data and leaves scalars as they came, so wrong types and bad values surface
as `Error`s from validation (with a field and a code) rather than as decode failures.
"""

from collections.abc import Mapping
from dataclasses import MISSING, fields, is_dataclass
from enum import StrEnum
from types import MappingProxyType, NoneType, UnionType
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from caliper.contracts.commands import Command
from caliper.contracts.document import Document, Entity, EntityId
from caliper.engine.io.canonical import JSON

ENTITY_KINDS: Mapping[str, type[Entity]] = {cls.kind: cls for cls in get_args(Entity)}
COMMAND_KINDS: Mapping[str, type[Command]] = {cls.kind: cls for cls in get_args(Command)}


class DecodeError(ValueError):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path


# --- Encoding ---------------------------------------------------------------------------


def encode(value: object) -> JSON:
    match value:
        case bool():
            return value
        case StrEnum():
            return value.value
        case None | int() | float() | str():
            return value
        case tuple() | list():
            return [encode(item) for item in value]
        case Mapping():
            return {str(key): encode(item) for key, item in value.items()}
        case _ if is_dataclass(value) and not isinstance(value, type):
            data = {f.name: encode(getattr(value, f.name)) for f in fields(value)}
            kind = getattr(type(value), "kind", None)
            if isinstance(kind, str):
                data["kind"] = kind
            return data
    raise TypeError(f"can't encode {value!r}")


# --- Decoding ---------------------------------------------------------------------------


def decode_document(data: object, path: str = "document") -> Document:
    obj = _object(data, path)
    _check_keys(obj, path, required={"entities", "next_id"}, optional=set())
    entities_data = _object(obj["entities"], f"{path}.entities")
    next_id = obj["next_id"]
    if isinstance(next_id, bool) or not isinstance(next_id, int) or next_id < 1:
        raise DecodeError(f"{path}.next_id", "must be a positive integer")
    entities = {
        EntityId(key): decode_entity(value, f"{path}.entities.{key}")
        for key, value in entities_data.items()
    }
    return Document(entities=MappingProxyType(entities), next_id=next_id)


def decode_entity(data: object, path: str) -> Entity:
    return cast(Entity, _decode_tagged(data, path, ENTITY_KINDS))


def decode_command(data: object, path: str) -> Command:
    return cast(Command, _decode_tagged(data, path, COMMAND_KINDS))


def _decode_tagged(data: object, path: str, kinds: Mapping[str, type[Any]]) -> object:
    obj = _object(data, path)
    kind = obj.get("kind")
    cls = kinds.get(kind) if isinstance(kind, str) else None
    if cls is None:
        raise DecodeError(path, f"unknown kind {kind!r}; expected one of: {', '.join(kinds)}")
    return _decode_dataclass(cls, {k: v for k, v in obj.items() if k != "kind"}, path)


def _decode_dataclass(cls: type[Any], obj: Mapping[str, object], path: str) -> object:
    required = {
        f.name for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING
    }
    _check_keys(obj, path, required=required, optional={f.name for f in fields(cls)} - required)
    hints = get_type_hints(cls)
    return cls(**{name: _decode_value(hints[name], obj[name], f"{path}.{name}") for name in obj})


def _decode_value(tp: object, data: object, path: str) -> object:
    origin = get_origin(tp)
    if is_dataclass(tp) and isinstance(tp, type):
        return _decode_dataclass(tp, data, path) if isinstance(data, dict) else data
    if origin is tuple and isinstance(data, list):
        item_type = get_args(tp)[0]
        return tuple(_decode_value(item_type, item, f"{path}[{i}]") for i, item in enumerate(data))
    if origin is Mapping and isinstance(data, dict):
        value_type = get_args(tp)[1]
        return MappingProxyType(
            {key: _decode_value(value_type, value, f"{path}.{key}") for key, value in data.items()}
        )
    if origin in (UnionType, Union):
        if data is None and NoneType in get_args(tp):
            return None
        if isinstance(data, dict):
            for member in get_args(tp):
                if is_dataclass(member) and set(data) == {f.name for f in fields(member)}:
                    return _decode_dataclass(cast(type[Any], member), data, path)
    return data


def _object(data: object, path: str) -> dict[str, object]:
    if not isinstance(data, dict):
        raise DecodeError(path, "expected an object")
    return cast(dict[str, object], data)


def _check_keys(
    obj: Mapping[str, object], path: str, *, required: set[str], optional: set[str]
) -> None:
    if missing := required - obj.keys():
        raise DecodeError(path, f"missing field(s): {', '.join(sorted(missing))}")
    if unknown := obj.keys() - required - optional:
        raise DecodeError(path, f"unknown field(s): {', '.join(sorted(unknown))}")
