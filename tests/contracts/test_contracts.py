"""Self-consistency of caliper.contracts, so the contract can't quietly drift as it grows."""

import dataclasses
import re
from types import MappingProxyType, NoneType, UnionType
from typing import get_args, get_type_hints

import pytest

from caliper.contracts import commands, document, queries
from caliper.contracts.commands import Command, Delta, ModifyEntity, ParamValue
from caliper.contracts.document import (
    CURVE_FEATURES,
    POINT_FEATURES,
    Circle,
    Document,
    Entity,
    EntityId,
    Geometry,
    Point2,
)
from caliper.contracts.errors import ErrorCode

ENTITY_TYPES = get_args(Entity)
COMMAND_TYPES = get_args(Command)
VALUE_TYPES = (
    document.Point2,
    document.Ref,
    queries.BoundingBox,
    queries.Distance,
    queries.AreaProperties,
    queries.Expectation,
    queries.CheckResult,
    queries.SolveStatus,
    queries.ConstraintOption,
    queries.Suggestion,
    commands.Delta,
    commands.Applied,
    commands.Rejected,
    commands.Change,
    Document,
)


@pytest.mark.parametrize(
    "cls", ENTITY_TYPES + COMMAND_TYPES + VALUE_TYPES, ids=lambda c: c.__name__
)
def test_dataclasses_are_frozen_slotted_and_keyword_only(cls: type) -> None:
    assert dataclasses.is_dataclass(cls)
    params = cls.__dataclass_params__
    assert params.frozen, "must be immutable"
    assert "__slots__" in cls.__dict__, "must use slots"
    assert all(f.kw_only for f in dataclasses.fields(cls)), "must be keyword-only"


@pytest.mark.parametrize("types", [ENTITY_TYPES, COMMAND_TYPES], ids=["entities", "commands"])
def test_kinds_are_unique_snake_case(types: tuple[type, ...]) -> None:
    kinds = [t.kind for t in types]
    assert len(kinds) == len(set(kinds))
    assert all(re.fullmatch(r"[a-z]+(_[a-z]+)*", k) for k in kinds)


@pytest.mark.parametrize("entity", ENTITY_TYPES, ids=lambda c: c.__name__)
def test_every_entity_has_a_create_command_with_matching_fields(entity: type) -> None:
    by_kind = {c.kind: c for c in COMMAND_TYPES}
    create = by_kind[f"create_{entity.kind}"]
    entity_hints = get_type_hints(entity)
    create_hints = get_type_hints(create)
    create_hints.pop("kind")
    assert create_hints.pop("id") == EntityId | None
    entity_hints.pop("kind")
    assert create_hints == entity_hints


@pytest.mark.parametrize("entity", ENTITY_TYPES, ids=lambda c: c.__name__)
def test_every_entity_field_is_editable_by_modify_entity(entity: type) -> None:
    allowed = set(get_args(ParamValue))
    for field in dataclasses.fields(entity):
        hint = get_type_hints(entity)[field.name]
        # An optional field (`value: float | None`) is settable when each alternative is.
        members = set(get_args(hint)) if isinstance(hint, UnionType) else {hint}
        assert members <= allowed, (
            f"{entity.__name__}.{field.name} can't be set through ModifyEntity"
        )
        if NoneType in members:
            assert field.default is None, "an optional field defaults to None"
    assert "changes" in {f.name for f in dataclasses.fields(ModifyEntity)}


def test_every_geometry_type_declares_its_features() -> None:
    assert set(POINT_FEATURES) == set(get_args(Geometry))
    assert set(CURVE_FEATURES) == set(get_args(Geometry))
    assert all(POINT_FEATURES.values())
    for geometry in get_args(Geometry):
        assert not POINT_FEATURES[geometry] & CURVE_FEATURES[geometry]


def test_every_geometry_can_be_construction_geometry() -> None:
    for geometry in get_args(Geometry):
        construction = {f.name: f for f in dataclasses.fields(geometry)}["construction"]
        assert construction.default is False


def test_error_codes_are_unique_and_dotted() -> None:
    values = [code.value for code in ErrorCode]
    assert len(values) == len(set(values))
    assert all(re.fullmatch(r"[a-z]+\.[a-z_]+", v) for v in values)


def test_delta_sets_and_inversion() -> None:
    e1, e2, e3 = EntityId("e1"), EntityId("e2"), EntityId("e3")
    small = Circle(center=Point2(x=0.0, y=0.0), radius=1.0)
    large = Circle(center=Point2(x=0.0, y=0.0), radius=2.0)
    delta = Delta(
        before=MappingProxyType({e1: small, e2: small}),
        after=MappingProxyType({e2: large, e3: large}),
        next_id_before=3,
        next_id_after=4,
    )
    assert (delta.removed, delta.modified, delta.added) == ({e1}, {e2}, {e3})
    inverse = delta.inverted()
    assert (inverse.removed, inverse.added) == ({e3}, {e1})
    assert inverse.inverted() == delta


def test_empty_document() -> None:
    doc = Document.empty()
    assert dict(doc.entities) == {}
    assert doc.next_id == 1
