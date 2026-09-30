"""Checks are stored in the document (C-1, ADR 0010): made by `CreateCheck`, edited and
removed by the ordinary commands, undone like anything else, and saved with the project."""

from pathlib import Path

from caliper.contracts.commands import (
    Applied,
    CreateCheck,
    CreateCircle,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    MoveEntities,
    Rejected,
)
from caliper.contracts.document import EntityId, Expectation, Feature, Metric, Point2, Ref
from caliper.contracts.errors import ErrorCode
from caliper.engine.commands.bus import Bus
from caliper.engine.io import codec, snapshot


def plate() -> Bus:
    bus = Bus()
    bus.execute(CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0))
    return bus


def width_check(expected: float = 120.0) -> CreateCheck:
    return CreateCheck(
        metric=Metric.DISTANCE_X,
        expected=expected,
        tolerance=0.01,
        refs=(
            Ref(entity=EntityId("e1"), feature=Feature.BOTTOM_LEFT),
            Ref(entity=EntityId("e1"), feature=Feature.BOTTOM_RIGHT),
        ),
    )


def test_a_check_is_an_entity_with_an_id_and_one_undo_step() -> None:
    bus = plate()
    result = bus.execute(width_check())
    assert isinstance(result, Applied)
    assert result.label == "Create Check"
    (id,) = result.created_ids
    stored = bus.document.entities[id]
    assert stored == Expectation(
        metric=Metric.DISTANCE_X,
        expected=120.0,
        tolerance=0.01,
        refs=width_check().refs,
    )
    assert bus.queries.check(stored).passed
    bus.undo()
    assert id not in bus.document.entities
    bus.redo()
    assert bus.document.entities[id] == stored


def test_a_check_that_fails_is_stored_one_that_cant_be_evaluated_isnt() -> None:
    bus = plate()
    assert isinstance(bus.execute(width_check(expected=100.0)), Applied)  # fails: still wanted
    before = bus.document
    for bad, code in (
        (
            CreateCheck(metric=Metric.DISTANCE, expected=1.0, tolerance=0.1),
            ErrorCode.VALUE_OUT_OF_RANGE,
        ),
        (
            CreateCheck(
                metric=Metric.DIMENSION_VALUE, expected=1.0, tolerance=0.1, ids=(EntityId("e9"),)
            ),
            ErrorCode.ENTITY_NOT_FOUND,
        ),
        (
            CreateCheck(metric=Metric.BBOX_WIDTH, expected=1.0, tolerance=-1.0),
            ErrorCode.VALUE_OUT_OF_RANGE,
        ),
        (
            CreateCheck(metric="volume", expected=1.0, tolerance=0.1),  # type: ignore[arg-type]
            ErrorCode.VALUE_OUT_OF_RANGE,
        ),
    ):
        result = bus.execute(bad)
        assert isinstance(result, Rejected), bad
        assert result.errors[0].code is code
    assert bus.document is before


def test_deleting_what_a_check_measures_leaves_the_check_failing() -> None:
    bus = plate()
    (check,) = bus.execute(width_check()).created_ids  # type: ignore[union-attr]
    bus.execute(DeleteEntities(ids=(EntityId("e1"),)))
    assert list(bus.document.entities) == [check]
    result = bus.queries.check(bus.document.entities[check])  # type: ignore[arg-type]
    assert not result.passed
    assert result.error is not None
    assert result.error.code is ErrorCode.ENTITY_NOT_FOUND
    # Editing it must leave something that can be evaluated.
    assert isinstance(bus.execute(ModifyEntity(id=check, changes={"expected": 90.0})), Rejected)
    bus.execute(DeleteEntities(ids=(check,)))
    assert bus.document.entities == {}


def test_a_check_is_edited_and_re_pointed_like_any_entity() -> None:
    bus = plate()
    (check,) = bus.execute(width_check()).created_ids  # type: ignore[union-attr]
    bus.execute(CreateCircle(center=Point2(x=150.0, y=25.0), radius=10.0))
    result = bus.execute(
        ModifyEntity(id=check, changes={"metric": "bbox_width", "refs": (), "expected": 160})
    )
    assert isinstance(result, Applied), result
    stored = bus.document.entities[check]
    assert isinstance(stored, Expectation)
    assert (stored.metric, stored.refs, stored.expected) == (Metric.BBOX_WIDTH, (), 160.0)
    assert bus.queries.check(stored).passed
    assert isinstance(bus.execute(ModifyEntity(id=check, changes={"ids": ["e3"]})), Applied)
    assert bus.document.entities[check].ids == (EntityId("e3"),)  # type: ignore[union-attr]
    itself = bus.execute(ModifyEntity(id=check, changes={"ids": [check]}))
    assert isinstance(itself, Rejected)  # a check measures geometry, not checks
    assert itself.errors[0].code is ErrorCode.ENTITY_WRONG_KIND


def test_moving_a_check_does_nothing_and_it_follows_the_geometry() -> None:
    bus = plate()
    (check,) = bus.execute(width_check()).created_ids  # type: ignore[union-attr]
    stored = bus.document.entities[check]
    bus.execute(MoveEntities(ids=(EntityId("e1"), check), dx=5.0, dy=0.0))
    assert bus.document.entities[check] is stored
    assert bus.queries.check(stored).passed
    bus.execute(ModifyEntity(id=EntityId("e1"), changes={"width": 100.0}))
    assert not bus.queries.check(stored).passed


def test_checks_are_saved_and_reopened_even_failing_ones(tmp_path: Path) -> None:
    bus = plate()
    bus.execute(width_check())
    bus.execute(
        CreateCheck(metric=Metric.BBOX_HEIGHT, expected=50.0, tolerance=0.0, ids=(EntityId("e1"),))
    )
    bus.execute(DeleteEntities(ids=(EntityId("e1"),)))  # both checks now name nothing
    path = tmp_path / "checked.caliper"
    snapshot.save(bus.document, path)
    assert '"kind": "check"' in path.read_text()
    assert snapshot.load(path) == bus.document


def test_a_check_command_round_trips_through_json() -> None:
    command = width_check()
    assert codec.decode_command(codec.encode(command), "c") == CreateCheck(
        metric="distance_x",  # type: ignore[arg-type]  # decoding is structural
        expected=120.0,
        tolerance=0.01,
        refs=command.refs,
    )
    bus = plate()
    result = bus.execute(codec.decode_command(codec.encode(command), "c"))
    assert isinstance(result, Applied)
    assert result.command == CreateCheck(
        metric=Metric.DISTANCE_X,
        expected=120.0,
        tolerance=0.01,
        refs=command.refs,
        id=EntityId("e2"),
    )
