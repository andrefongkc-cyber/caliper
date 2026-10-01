"""Self-consistency of caliper.contracts, so the contract can't quietly drift as it grows."""

import dataclasses
import re
from types import MappingProxyType, NoneType, UnionType
from typing import get_args, get_type_hints

import pytest

from caliper.contracts import commands, document, queries
from caliper.contracts.commands import Command, CreateSketch, Delta, ModifyEntity, ParamValue
from caliper.contracts.document import (
    CURVE_FEATURES,
    FIRST_SKETCH,
    ID_PATTERN,
    POINT_FEATURES,
    Circle,
    Document,
    Entity,
    EntityId,
    Geometry,
    PartFeature,
    Plane,
    Point2,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode, LoadError
from caliper.contracts.queries import Metric

ENTITY_TYPES = get_args(Entity)
COMMAND_TYPES = get_args(Command)
FEATURE_TYPES = get_args(PartFeature) or (PartFeature,)
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
    "cls", ENTITY_TYPES + FEATURE_TYPES + COMMAND_TYPES + VALUE_TYPES, ids=lambda c: c.__name__
)
def test_dataclasses_are_frozen_slotted_and_keyword_only(cls: type) -> None:
    assert dataclasses.is_dataclass(cls)
    params = cls.__dataclass_params__
    assert params.frozen, "must be immutable"
    assert "__slots__" in cls.__dict__, "must use slots"
    assert all(f.kw_only for f in dataclasses.fields(cls)), "must be keyword-only"


@pytest.mark.parametrize(
    "types",
    [ENTITY_TYPES + FEATURE_TYPES, COMMAND_TYPES],
    ids=["entities and features", "commands"],
)
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
    if entity in get_args(Geometry):
        # The command may leave the sketch to the bus, as it leaves the id (ADR 0011).
        assert entity_hints.pop("sketch") == EntityId
        assert create_hints.pop("sketch") == EntityId | None
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


def test_load_error_is_part_of_the_contract() -> None:
    # Callers that open files catch it from here, without importing the engine.
    problem = Error(
        code=ErrorCode.VALUE_NOT_POSITIVE, message="width must be greater than 0", field="width"
    )
    error = LoadError("invalid document", [problem, Error(code=ErrorCode.ID_TAKEN, message="x")])
    assert isinstance(error, ValueError)
    assert error.errors == (problem, Error(code=ErrorCode.ID_TAKEN, message="x"))
    assert str(error) == "invalid document: width: width must be greater than 0; x"
    assert str(LoadError("not a Caliper document")) == "not a Caliper document"
    assert LoadError("not a Caliper document").errors == ()


def test_metric_names_are_stable() -> None:
    # Bench cases and agents store these strings: adding one is fine, renaming one isn't.
    assert [metric.value for metric in Metric] == [
        "distance",
        "distance_x",
        "distance_y",
        "position_x",
        "position_y",
        "bbox_width",
        "bbox_height",
        "area",
        "dimension_value",
    ]


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


# --- The part (ADR 0011) ----------------------------------------------------------------


def test_a_new_part_has_one_sketch_on_xy() -> None:
    one = (Sketch(id=FIRST_SKETCH, plane=Plane.XY),)
    assert Document.empty().features == one
    # What a V1 document was, so every document built without features is one.
    assert Document(entities=MappingProxyType({}), next_id=7).features == one


def test_the_first_sketch_is_an_id_the_engine_never_allocates() -> None:
    assert re.fullmatch(ID_PATTERN, FIRST_SKETCH)
    assert not re.fullmatch(r"e[1-9][0-9]*", FIRST_SKETCH)
    assert Document.empty().next_id == 1


def test_geometry_names_its_sketch_and_nothing_else_stores_one() -> None:
    """A dimension or constraint is in the sketch of what it refers to, and a check belongs to
    the part: storing a sketch on them would store something worked out (ADR 0005)."""
    for entity in ENTITY_TYPES:
        fields = {f.name: f for f in dataclasses.fields(entity)}
        if entity in get_args(Geometry):
            assert get_type_hints(entity)["sketch"] == EntityId
            assert fields["sketch"].default == FIRST_SKETCH
        else:
            assert "sketch" not in fields, entity.__name__


def test_every_feature_has_an_id_and_a_kind_no_entity_has() -> None:
    for feature in FEATURE_TYPES:
        assert get_type_hints(feature)["id"] == EntityId
    assert not {f.kind for f in FEATURE_TYPES} & {e.kind for e in ENTITY_TYPES}


def test_every_feature_field_is_editable_by_modify_entity() -> None:
    allowed = set(get_args(ParamValue))
    for feature in FEATURE_TYPES:
        for name, hint in get_type_hints(feature).items():
            if name != "kind":
                assert hint in allowed, f"{feature.__name__}.{name}"


def test_plane_names_are_stable() -> None:
    # Files store these strings: adding one is fine, renaming one isn't.
    assert [plane.value for plane in Plane] == ["xy", "xz", "yz"]


def test_a_sketch_is_created_by_a_command_on_a_plane() -> None:
    assert CreateSketch in COMMAND_TYPES
    hints = get_type_hints(CreateSketch)
    assert (hints["plane"], hints["id"]) == (Plane, EntityId | None)


def test_the_sketch_error_codes_are_stable() -> None:
    assert ErrorCode.SKETCH_REQUIRED.value == "sketch.required"
    assert ErrorCode.SKETCH_MIXED.value == "sketch.mixed"


def test_a_delta_keeps_the_feature_list_only_when_it_changed() -> None:
    one = (Sketch(id=FIRST_SKETCH, plane=Plane.XY),)
    two = (*one, Sketch(id=EntityId("e1"), plane=Plane.XZ))
    none: MappingProxyType[EntityId, Entity] = MappingProxyType({})
    unchanged = Delta(before=none, after=none, next_id_before=1, next_id_after=1)
    assert (unchanged.features_before, unchanged.features_after) == (None, None)
    added = Delta(
        before=none,
        after=none,
        next_id_before=1,
        next_id_after=2,
        features_before=one,
        features_after=two,
    )
    assert (added.inverted().features_before, added.inverted().features_after) == (two, one)
    assert added.inverted().inverted() == added
    assert added.added == frozenset()  # entities only
