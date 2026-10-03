"""Convert contract dataclasses to and from JSON-compatible data.

Encoding is exact. Decoding is structural: it rebuilds dataclasses and containers from the
shape of the data and leaves scalars as they came, so wrong types and bad values surface
as `Error`s from validation (with a field and a code) rather than as decode failures.
"""

from collections.abc import Callable, Mapping, Set
from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import StrEnum
from types import MappingProxyType, UnionType
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from caliper.contracts.commands import Command
from caliper.contracts.document import Document, Entity, EntityId, PartFeature
from caliper.engine.io.canonical import JSON

ENTITY_KINDS: Mapping[str, type[Entity]] = {cls.kind: cls for cls in get_args(Entity)}
COMMAND_KINDS: Mapping[str, type[Command]] = {cls.kind: cls for cls in get_args(Command)}
FEATURE_KINDS: Mapping[str, type[PartFeature]] = {
    cls.kind: cls for cls in (get_args(PartFeature) or (PartFeature,))
}
"""One kind until V2's F3 adds a second, which makes `PartFeature` a union."""


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
    _check_keys(obj, path, required={"entities", "features", "next_id"}, optional=set())
    entities_data = _object(obj["entities"], f"{path}.entities")
    next_id = obj["next_id"]
    if isinstance(next_id, bool) or not isinstance(next_id, int) or next_id < 1:
        raise DecodeError(f"{path}.next_id", "must be a positive integer")
    entities = {
        EntityId(key): decode_entity(value, f"{path}.entities.{key}")
        for key, value in entities_data.items()
    }
    features_data = obj["features"]
    if not isinstance(features_data, list):
        raise DecodeError(f"{path}.features", "expected a list")
    features = tuple(
        decode_feature(item, f"{path}.features[{i}]") for i, item in enumerate(features_data)
    )
    return Document(entities=MappingProxyType(entities), next_id=next_id, features=features)


def decode_entity(data: object, path: str) -> Entity:
    return cast(Entity, _decode_tagged(data, path, ENTITY_KINDS))


def decode_feature(data: object, path: str) -> PartFeature:
    return cast(PartFeature, _decode_tagged(data, path, FEATURE_KINDS))


def decode_command(data: object, path: str) -> Command:
    return cast(Command, _decode_tagged(data, path, COMMAND_KINDS))


def _decode_tagged(data: object, path: str, kinds: Mapping[str, type[Any]]) -> object:
    obj = _object(data, path)
    kind = obj.get("kind")
    cls = kinds.get(kind) if isinstance(kind, str) else None
    if cls is None:
        raise DecodeError(path, f"unknown kind {kind!r}; expected one of: {', '.join(kinds)}")
    return _decode_dataclass(cls, {k: v for k, v in obj.items() if k != "kind"}, path)


type _Decode = Callable[[object, str], object]
"""Decodes a value found at a path. A type whose data is kept as it came has none."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Shape:
    """A dataclass's fields and how each is decoded."""

    required: frozenset[str]
    optional: frozenset[str]
    decoders: Mapping[str, _Decode | None]


_SHAPES: dict[type[Any], _Shape] = {}
"""Each class's shape, worked out the first time one is decoded: looking up its type hints
for every object was half the time of opening a file (Performance V2.2, Perf-7). Two
threads making one at once make the same one."""


def _shape(cls: type[Any]) -> _Shape:
    shape = _SHAPES.get(cls)
    if shape is None:
        hints = get_type_hints(cls)
        names = [f.name for f in fields(cls)]
        required = frozenset(
            f.name for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING
        )
        shape = _SHAPES[cls] = _Shape(
            required=required,
            optional=frozenset(names) - required,
            decoders={name: _decoder(hints[name]) for name in names},
        )
    return shape


def _decode_dataclass(cls: type[Any], obj: Mapping[str, object], path: str) -> object:
    shape = _shape(cls)
    _check_keys(obj, path, required=shape.required, optional=shape.optional)
    decoders = shape.decoders
    return cls(
        **{
            name: data if (decode := decoders[name]) is None else decode(data, f"{path}.{name}")
            for name, data in obj.items()
        }
    )


def _decoder(tp: object) -> _Decode | None:
    """How data of type `tp` is decoded: dataclasses and containers rebuilt from the shape of
    the data, and anything else (scalars, and data of the wrong shape) left as it came."""
    if is_dataclass(tp) and isinstance(tp, type):
        cls: type[Any] = tp
        return lambda data, path: (
            _decode_dataclass(cls, data, path) if isinstance(data, dict) else data
        )
    origin = get_origin(tp)
    if origin is tuple:
        item = _decoder(get_args(tp)[0])

        def decode_tuple(data: object, path: str) -> object:
            if not isinstance(data, list):
                return data
            if item is None:
                return tuple(data)
            return tuple(item(value, f"{path}[{i}]") for i, value in enumerate(data))

        return decode_tuple
    if origin is Mapping:
        value_of = _decoder(get_args(tp)[1])

        def decode_mapping(data: object, path: str) -> object:
            if not isinstance(data, dict):
                return data
            if value_of is None:
                return MappingProxyType(dict(data))
            return MappingProxyType(
                {key: value_of(value, f"{path}.{key}") for key, value in data.items()}
            )

        return decode_mapping
    if origin in (UnionType, Union):
        members = [
            (frozenset(f.name for f in fields(member)), cast(type[Any], member))
            for member in get_args(tp)
            if is_dataclass(member)
        ]
        if not members:
            return None  # None, or a scalar, as it came

        def decode_union(data: object, path: str) -> object:
            if isinstance(data, dict):
                for names, member in members:
                    if data.keys() == names:
                        return _decode_dataclass(member, data, path)
            return data

        return decode_union
    return None


def _object(data: object, path: str) -> dict[str, object]:
    if not isinstance(data, dict):
        raise DecodeError(path, "expected an object")
    return cast(dict[str, object], data)


def _check_keys(
    obj: Mapping[str, object], path: str, *, required: Set[str], optional: Set[str]
) -> None:
    if missing := required - obj.keys():
        raise DecodeError(path, f"missing field(s): {', '.join(sorted(missing))}")
    if unknown := obj.keys() - required - optional:
        raise DecodeError(path, f"unknown field(s): {', '.join(sorted(unknown))}")
