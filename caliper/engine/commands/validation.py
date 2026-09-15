"""Normalize and validate entity values, reporting problems as Error values.

Shared by the command bus and by file loading, so a file can't hold anything a command
couldn't have created. Normalizing means ints become floats and strings become enums or
ids, so a script and the UI produce identical documents.
"""

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import fields
from enum import StrEnum
from types import MappingProxyType
from typing import get_args, get_type_hints

from caliper.contracts.document import (
    ID_PATTERN,
    POINT_FEATURES,
    Arc,
    Circle,
    DistanceDimension,
    Document,
    Entity,
    EntityId,
    Feature,
    Line,
    Point2,
    RadialDimension,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import Error, ErrorCode

_ID = re.compile(ID_PATTERN)
_POSITIVE_FIELDS: Mapping[type, tuple[str, ...]] = {
    Circle: ("radius",),
    Arc: ("radius",),
    Rectangle: ("width", "height"),
}


def _field_types(entity_type: type[Entity]) -> Mapping[str, object]:
    hints = get_type_hints(entity_type)
    return MappingProxyType({f.name: hints[f.name] for f in fields(entity_type)})


_FIELD_TYPES: Mapping[type[Entity], Mapping[str, object]] = {
    entity_type: _field_types(entity_type) for entity_type in get_args(Entity)
}


def field_types(entity_type: type[Entity]) -> Mapping[str, object]:
    """Field name → annotated type, in declaration order."""
    return _FIELD_TYPES[entity_type]


def build_entity(
    entity_type: type[Entity], values: Mapping[str, object], document: Document
) -> Entity | list[Error]:
    """Normalize `values` into an entity and validate it against `document`."""
    errors: list[Error] = []
    normalized = {
        name: normalize(tp, values[name], name, errors)
        for name, tp in field_types(entity_type).items()
    }
    if errors:
        return errors
    construct: Callable[..., Entity] = entity_type
    entity = construct(**normalized)
    problems = domain_errors(entity) + reference_errors(entity, document)
    return problems or entity


# --- Values -----------------------------------------------------------------------------
# Each normalizer appends to `errors` and returns a placeholder when the value is invalid.
# Callers check `errors` before using the result.


def normalize(tp: object, value: object, field: str, errors: list[Error]) -> object:
    if tp is float:
        return normalize_float(value, field, errors)
    if tp is Point2:
        return normalize_point(value, field, errors)
    if tp is Ref:
        return normalize_ref(value, field, errors)
    if tp is EntityId:
        return normalize_id(value, field, errors)
    if isinstance(tp, type) and issubclass(tp, StrEnum):
        return normalize_enum(tp, value, field, errors)
    raise TypeError(f"no normalizer for field type {tp!r}")


def normalize_float(value: object, field: str, errors: list[Error]) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        errors.append(_error(ErrorCode.VALUE_WRONG_TYPE, field, f"{field} must be a number"))
        return math.nan
    number = float(value)
    if not math.isfinite(number):
        errors.append(_error(ErrorCode.VALUE_NOT_FINITE, field, f"{field} must be finite"))
    return number


def normalize_point(value: object, field: str, errors: list[Error]) -> Point2:
    if not isinstance(value, Point2):
        errors.append(_error(ErrorCode.VALUE_WRONG_TYPE, field, f"{field} must be a point"))
        return Point2(x=math.nan, y=math.nan)
    return Point2(
        x=normalize_float(value.x, f"{field}.x", errors),
        y=normalize_float(value.y, f"{field}.y", errors),
    )


def normalize_id(value: object, field: str, errors: list[Error]) -> EntityId:
    if isinstance(value, str) and _ID.fullmatch(value):
        return EntityId(value)
    errors.append(_error(ErrorCode.ID_INVALID, field, f"{field} must match {ID_PATTERN}"))
    return EntityId("")


def normalize_ref(value: object, field: str, errors: list[Error]) -> Ref:
    if not isinstance(value, Ref):
        errors.append(
            _error(ErrorCode.VALUE_WRONG_TYPE, field, f"{field} must be a feature reference")
        )
        return Ref(entity=EntityId(""), feature=Feature.CENTER)
    entity = normalize_id(value.entity, f"{field}.entity", errors)
    try:
        feature = Feature(value.feature)
    except ValueError:
        options = ", ".join(member.value for member in Feature)
        errors.append(
            _error(
                ErrorCode.REFERENCE_INVALID_FEATURE,
                f"{field}.feature",
                f"{field}.feature must be one of: {options}",
            )
        )
        feature = Feature.CENTER
    return Ref(entity=entity, feature=feature)


def normalize_enum[E: StrEnum](enum: type[E], value: object, field: str, errors: list[Error]) -> E:
    for member in enum:
        if value == member.value:
            return member
    options = ", ".join(member.value for member in enum)
    errors.append(_error(ErrorCode.VALUE_OUT_OF_RANGE, field, f"{field} must be one of: {options}"))
    return next(iter(enum))


# --- Entities ---------------------------------------------------------------------------


def domain_errors(entity: Entity) -> list[Error]:
    """Rules about an entity on its own."""
    errors = [
        _error(ErrorCode.VALUE_NOT_POSITIVE, name, f"{name} must be greater than 0")
        for name in _POSITIVE_FIELDS.get(type(entity), ())
        if getattr(entity, name) <= 0
    ]
    match entity:
        case Line(start=start, end=end) if start == end:
            errors.append(
                _error(ErrorCode.GEOMETRY_DEGENERATE, "end", "a line needs distinct endpoints")
            )
        case Arc(sweep_angle=sweep) if not 0 < sweep < 360:
            errors.append(
                _error(
                    ErrorCode.VALUE_OUT_OF_RANGE,
                    "sweep_angle",
                    "sweep_angle must be between 0 and 360 degrees, exclusive",
                )
            )
    return errors


def reference_errors(entity: Entity, document: Document) -> list[Error]:
    """Rules about what an annotation refers to."""
    match entity:
        case DistanceDimension(a=a, b=b):
            errors = feature_errors(a, "a", document) + feature_errors(b, "b", document)
            if a == b:
                errors.append(
                    _error(
                        ErrorCode.REFERENCE_DEGENERATE, "b", "a and b must be different features"
                    )
                )
            return errors
        case RadialDimension(target=target):
            found = document.entities.get(target)
            if found is None:
                return [_error(ErrorCode.ENTITY_NOT_FOUND, "target", f"no entity {target!r}")]
            if not isinstance(found, Circle | Arc):
                return [
                    _error(
                        ErrorCode.ENTITY_WRONG_KIND,
                        "target",
                        f"{target!r} is a {found.kind}; radial dimensions need a circle or arc",
                    )
                ]
            return []
        case _:
            return []


def feature_errors(ref: Ref, field: str, document: Document) -> list[Error]:
    """Whether `ref` names a feature that exists on a geometry entity in `document`."""
    target = document.entities.get(ref.entity)
    if target is None:
        return [_error(ErrorCode.ENTITY_NOT_FOUND, f"{field}.entity", f"no entity {ref.entity!r}")]
    if not isinstance(target, Line | Circle | Arc | Rectangle):
        return [
            _error(
                ErrorCode.ENTITY_WRONG_KIND,
                f"{field}.entity",
                f"{ref.entity!r} is a {target.kind}; dimensions attach to geometry",
            )
        ]
    features = POINT_FEATURES[type(target)]
    if ref.feature not in features:
        options = ", ".join(sorted(features))
        return [
            _error(
                ErrorCode.REFERENCE_INVALID_FEATURE,
                f"{field}.feature",
                f"a {target.kind} has no {ref.feature} feature; use one of: {options}",
            )
        ]
    return []


def _error(code: ErrorCode, field: str, message: str) -> Error:
    return Error(code=code, message=message, field=field)
