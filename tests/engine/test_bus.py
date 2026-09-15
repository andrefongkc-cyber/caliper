import math
from types import MappingProxyType

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Change,
    ChangeReason,
    CommandResult,
    CreateArc,
    CreateCircle,
    CreateDistanceDimension,
    CreateLine,
    CreateRadialDimension,
    CreateRectangle,
    ModifyEntity,
    Rejected,
)
from caliper.contracts.document import (
    DistanceOrientation,
    Document,
    EntityId,
    Feature,
    Point2,
    RadialMeasure,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import ErrorCode
from caliper.engine.commands.bus import Bus

ORIGIN = Point2(x=0.0, y=0.0)
E1 = EntityId("e1")


def rectangle(**overrides: object) -> CreateRectangle:
    fields: dict[str, object] = {"corner": ORIGIN, "width": 100.0, "height": 50.0}
    return CreateRectangle(**(fields | overrides))


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def rejection(result: CommandResult) -> list[tuple[ErrorCode, str | None]]:
    assert isinstance(result, Rejected), result
    return [(e.code, e.field) for e in result.errors]


# --- Create -----------------------------------------------------------------------------


def test_create_allocates_an_id_and_records_a_delta() -> None:
    bus = Bus()
    result = applied(bus.execute(rectangle()))
    assert result.created_ids == (E1,)
    assert result.command == rectangle(id=E1)
    assert result.label == "Create Rectangle"
    assert result.delta.added == {E1}
    assert bus.document.entities[E1] == Rectangle(corner=ORIGIN, width=100.0, height=50.0)
    assert bus.document.next_id == 2


def test_ints_become_floats_in_the_document_and_the_resolved_command() -> None:
    bus = Bus()
    result = applied(bus.execute(rectangle(corner=Point2(x=0, y=0), width=100, height=50)))
    stored = bus.document.entities[E1]
    assert isinstance(stored, Rectangle)
    assert type(stored.width) is float
    assert type(stored.corner.x) is float
    assert result.command == rectangle(id=E1)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"width": 0.0}, [(ErrorCode.VALUE_NOT_POSITIVE, "width")]),
        ({"height": -1.0}, [(ErrorCode.VALUE_NOT_POSITIVE, "height")]),
        ({"width": math.nan}, [(ErrorCode.VALUE_NOT_FINITE, "width")]),
        ({"width": True}, [(ErrorCode.VALUE_WRONG_TYPE, "width")]),
        ({"width": "100"}, [(ErrorCode.VALUE_WRONG_TYPE, "width")]),
        ({"corner": Point2(x=math.inf, y=0.0)}, [(ErrorCode.VALUE_NOT_FINITE, "corner.x")]),
        ({"corner": "origin"}, [(ErrorCode.VALUE_WRONG_TYPE, "corner")]),
        (
            {"width": -1.0, "id": EntityId("Not Valid")},
            [(ErrorCode.VALUE_NOT_POSITIVE, "width"), (ErrorCode.ID_INVALID, "id")],
        ),
    ],
)
def test_invalid_values_are_rejected_with_code_and_field(
    overrides: dict[str, object], expected: list[tuple[ErrorCode, str]]
) -> None:
    bus = Bus()
    assert rejection(bus.execute(rectangle(**overrides))) == expected
    assert bus.document == Document.empty()
    assert bus.undo_label is None


def test_caller_chosen_ids() -> None:
    bus = Bus()
    plate = EntityId("mounting_plate")
    assert applied(bus.execute(rectangle(id=plate))).created_ids == (plate,)
    assert bus.document.next_id == 1
    assert rejection(bus.execute(rectangle(id=plate))) == [(ErrorCode.ID_TAKEN, "id")]
    applied(bus.execute(rectangle(id=E1)))
    assert applied(bus.execute(rectangle())).created_ids == (EntityId("e2"),)


def test_every_geometry_kind_can_be_created() -> None:
    bus = Bus()
    applied(bus.execute(CreateLine(start=ORIGIN, end=Point2(x=10.0, y=0.0))))
    applied(bus.execute(CreateCircle(center=ORIGIN, radius=5.0)))
    applied(bus.execute(CreateArc(center=ORIGIN, radius=5.0, start_angle=0.0, sweep_angle=90.0)))
    applied(bus.execute(rectangle()))
    assert [e.kind for e in bus.document.entities.values()] == [
        "line",
        "circle",
        "arc",
        "rectangle",
    ]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (CreateLine(start=ORIGIN, end=ORIGIN), [(ErrorCode.GEOMETRY_DEGENERATE, "end")]),
        (CreateCircle(center=ORIGIN, radius=0.0), [(ErrorCode.VALUE_NOT_POSITIVE, "radius")]),
        (
            CreateArc(center=ORIGIN, radius=1.0, start_angle=0.0, sweep_angle=360.0),
            [(ErrorCode.VALUE_OUT_OF_RANGE, "sweep_angle")],
        ),
    ],
)
def test_geometry_rules(command: CreateLine, expected: list[tuple[ErrorCode, str]]) -> None:
    assert rejection(Bus().execute(command)) == expected


# --- Dimensions -------------------------------------------------------------------------


def dimension(a: Ref, b: Ref) -> CreateDistanceDimension:
    return CreateDistanceDimension(a=a, b=b, orientation=DistanceOrientation.HORIZONTAL, offset=5.0)


def test_dimensions_attach_to_features() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    width = dimension(
        Ref(entity=E1, feature=Feature.BOTTOM_LEFT), Ref(entity=E1, feature=Feature.BOTTOM_RIGHT)
    )
    assert applied(bus.execute(width)).created_ids == (EntityId("e2"),)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            dimension(
                Ref(entity=E1, feature=Feature.START), Ref(entity=E1, feature=Feature.CENTER)
            ),
            [(ErrorCode.REFERENCE_INVALID_FEATURE, "a.feature")],
        ),
        (
            dimension(
                Ref(entity=E1, feature=Feature.CENTER),
                Ref(entity=EntityId("e9"), feature=Feature.CENTER),
            ),
            [(ErrorCode.ENTITY_NOT_FOUND, "b.entity")],
        ),
        (
            dimension(
                Ref(entity=E1, feature=Feature.CENTER), Ref(entity=E1, feature=Feature.CENTER)
            ),
            [(ErrorCode.REFERENCE_DEGENERATE, "b")],
        ),
        (
            dimension(Ref(entity=E1, feature="corner"), Ref(entity=E1, feature=Feature.CENTER)),
            [(ErrorCode.REFERENCE_INVALID_FEATURE, "a.feature")],
        ),
        (
            CreateRadialDimension(target=E1, measure=RadialMeasure.DIAMETER, label_angle=45.0),
            [(ErrorCode.ENTITY_WRONG_KIND, "target")],
        ),
    ],
)
def test_dimension_reference_rules(
    command: CreateDistanceDimension, expected: list[tuple[ErrorCode, str]]
) -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    assert rejection(bus.execute(command)) == expected


# --- Modify -----------------------------------------------------------------------------


def test_changing_width_keeps_the_corner() -> None:
    bus = Bus(Document.empty())
    applied(bus.execute(rectangle(corner=Point2(x=5.0, y=7.0))))
    result = applied(bus.execute(ModifyEntity(id=E1, changes={"width": 120})))
    assert result.label == "Change Width"
    assert dict(result.command.changes) == {"width": 120.0}
    assert result.delta.modified == {E1}
    assert bus.document.entities[E1] == Rectangle(
        corner=Point2(x=5.0, y=7.0), width=120.0, height=50.0
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            ModifyEntity(id=EntityId("e9"), changes={"width": 1.0}),
            [(ErrorCode.ENTITY_NOT_FOUND, "id")],
        ),
        (ModifyEntity(id=E1, changes={"depth": 1.0}), [(ErrorCode.FIELD_UNKNOWN, "depth")]),
        (ModifyEntity(id=E1, changes={"width": -1.0}), [(ErrorCode.VALUE_NOT_POSITIVE, "width")]),
    ],
)
def test_modify_rules(command: ModifyEntity, expected: list[tuple[ErrorCode, str]]) -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    before = bus.document
    assert rejection(bus.execute(command)) == expected
    assert bus.document is before


def test_a_change_that_changes_nothing_is_not_recorded() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    changes: list[Change] = []
    bus.subscribe(changes.append)
    result = applied(bus.execute(ModifyEntity(id=E1, changes=MappingProxyType({"width": 100.0}))))
    assert not result.delta.before
    assert not result.delta.after
    assert changes == []
    assert bus.undo_label == "Create Rectangle"


# --- Undo, redo, notifications ----------------------------------------------------------


def test_undo_and_redo_walk_history_exactly() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    created = bus.document
    applied(bus.execute(ModifyEntity(id=E1, changes={"width": 120.0})))
    final = bus.document

    assert bus.undo_label == "Change Width"
    assert bus.undo() is not None
    assert bus.document == created
    assert bus.undo() is not None
    assert bus.document == Document.empty()
    assert bus.undo() is None
    assert bus.redo_label == "Create Rectangle"

    bus.redo()
    bus.redo()
    assert bus.document == final
    assert bus.redo() is None


def test_a_new_command_clears_redo() -> None:
    bus = Bus()
    applied(bus.execute(rectangle()))
    bus.undo()
    applied(bus.execute(CreateCircle(center=ORIGIN, radius=1.0)))
    assert bus.redo_label is None


def test_subscribers_see_every_change_until_they_unsubscribe() -> None:
    bus = Bus()
    seen: list[tuple[ChangeReason, str]] = []
    unsubscribe = bus.subscribe(lambda change: seen.append((change.reason, change.label)))
    applied(bus.execute(rectangle()))
    bus.undo()
    bus.redo()
    unsubscribe()
    bus.undo()
    assert seen == [
        (ChangeReason.EXECUTE, "Create Rectangle"),
        (ChangeReason.UNDO, "Create Rectangle"),
        (ChangeReason.REDO, "Create Rectangle"),
    ]


def test_undo_stack_is_bounded() -> None:
    bus = Bus(undo_limit=2)
    for _ in range(3):
        applied(bus.execute(rectangle()))
    assert bus.undo() is not None
    assert bus.undo() is not None
    assert bus.undo() is None
    assert set(bus.document.entities) == {E1}


@given(
    sizes=st.lists(
        st.tuples(
            st.floats(min_value=0.01, max_value=1e4), st.floats(min_value=0.01, max_value=1e4)
        ),
        min_size=1,
        max_size=12,
    ),
    new_width=st.floats(min_value=0.01, max_value=1e4),
)
def test_undoing_everything_returns_to_empty_and_redo_restores(
    sizes: list[tuple[float, float]], new_width: float
) -> None:
    bus = Bus()
    for width, height in sizes:
        applied(bus.execute(rectangle(width=width, height=height)))
    bus.execute(ModifyEntity(id=E1, changes={"width": new_width}))
    final = bus.document
    while bus.undo():
        pass
    assert bus.document == Document.empty()
    while bus.redo():
        pass
    assert bus.document == final


# --- Not in the Phase 0 slice -----------------------------------------------------------


def test_features_landing_in_v1_say_so() -> None:
    bus = Bus()
    with pytest.raises(NotImplementedError):
        bus.execute(rectangle(), merge_key="drag")
    with pytest.raises(NotImplementedError):
        bus.transaction("Batch")
