"""Checks are stored in the document (C-1, ADR 0010): made by `CreateCheck`, edited and
removed by the ordinary commands, undone like anything else, and saved with the project."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateCheck,
    CreateCircle,
    CreateLine,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    MoveEntities,
    Rejected,
)
from caliper.contracts.document import (
    Circle,
    Document,
    Entity,
    EntityId,
    Expectation,
    Feature,
    Metric,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import ErrorCode
from caliper.engine.commands.bus import Bus
from caliper.engine.constraints import sketch
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


# --- Properties (N2) -----------------------------------------------------------------------

Op = tuple[object, ...]

_at = st.integers(min_value=-50, max_value=50)
_size = st.integers(min_value=1, max_value=40)
_pick = st.integers(min_value=0, max_value=1000)
_nudge = st.integers(min_value=-3, max_value=3)

OPS = st.lists(
    st.one_of(
        st.tuples(st.just("rectangle"), _at, _at, _size, _size),
        st.tuples(st.just("circle"), _at, _at, _size),
        st.tuples(
            st.just("check"), _pick, _pick, st.sampled_from(["width", "height", "distance"]), _nudge
        ),
        st.tuples(st.just("edit_check"), _pick, _nudge),
        st.tuples(st.just("delete"), _pick),
        st.tuples(st.just("delete_check"), _pick),
        st.tuples(st.just("move"), _pick, _nudge, _nudge),
        st.just(("undo",)),
        st.just(("redo",)),
    ),
    max_size=24,
)


def _shapes(document: Document) -> list[EntityId]:
    return sorted(i for i, e in document.entities.items() if isinstance(e, Rectangle | Circle))


def _checks(document: Document) -> dict[EntityId, Expectation]:
    return {i: e for i, e in document.entities.items() if isinstance(e, Expectation)}


def _named(check: Expectation) -> set[EntityId]:
    return set(check.ids) | {ref.entity for ref in check.refs}


def _command(op: Op, document: Document) -> Command | None:
    """The command `op` stands for on `document`, or None when there's nothing to act on."""
    shapes, checks = _shapes(document), sorted(_checks(document))
    match op:
        case ("rectangle", x, y, w, h):
            return CreateRectangle(corner=Point2(x=x, y=y), width=w, height=h)  # type: ignore[arg-type]
        case ("circle", x, y, r):
            return CreateCircle(center=Point2(x=x, y=y), radius=r)  # type: ignore[arg-type]
        case ("check", first, second, kind, nudge) if shapes:
            a = shapes[first % len(shapes)]  # type: ignore[operator]
            b = shapes[second % len(shapes)]  # type: ignore[operator]
            measured = {"width": Metric.BBOX_WIDTH, "height": Metric.BBOX_HEIGHT}
            if kind in measured:
                metric, refs, ids = measured[kind], (), (a,)  # type: ignore[index]
            else:
                refs = (
                    Ref(entity=a, feature=Feature.CENTER),
                    Ref(entity=b, feature=Feature.CENTER),
                )
                metric, ids = Metric.DISTANCE, ()
            return CreateCheck(
                metric=metric,
                expected=10.0 + nudge,
                tolerance=0.5,
                refs=refs,
                ids=ids,  # type: ignore[operator]
            )
        case ("edit_check", which, nudge) if checks:
            id = checks[which % len(checks)]  # type: ignore[operator]
            expected = _checks(document)[id].expected + nudge  # type: ignore[operator]
            return ModifyEntity(id=id, changes={"expected": expected})
        case ("delete", which) if shapes:
            return DeleteEntities(ids=(shapes[which % len(shapes)],))  # type: ignore[operator]
        case ("delete_check", which) if checks:
            return DeleteEntities(ids=(checks[which % len(checks)],))  # type: ignore[operator]
        case ("move", which, dx, dy) if shapes:
            id = shapes[which % len(shapes)]  # type: ignore[operator]
            return MoveEntities(ids=(id,), dx=float(dx), dy=float(dy))  # type: ignore[arg-type]
    return None


def run_session(ops: list[Op]) -> None:
    """Run `ops` on a bus, asserting after every step what stored checks must keep."""
    bus = Bus(kernel=None)
    states = [bus.document]
    """The document after each recorded step, from the empty one: undo and redo walk it."""
    made: list[Command] = []
    """The resolved commands behind `states`, for replaying the session."""
    position = 0
    for op in ops:
        before = bus.document
        if op == ("undo",):
            if bus.undo() is not None:
                position -= 1
                assert bus.document == states[position], "undo isn't exact"
            continue
        if op == ("redo",):
            if bus.redo() is not None:
                position += 1
                assert bus.document == states[position], "redo isn't exact"
            continue
        command = _command(op, before)
        if command is None:
            continue
        result = bus.execute(command)
        if isinstance(result, Rejected):
            assert bus.document is before, "a rejected command changed the document"
            continue
        assert isinstance(result, Applied)
        after = bus.document
        if not (result.delta.before or result.delta.after):
            continue
        if isinstance(command, DeleteEntities):
            kept = set(_checks(before)) - set(command.ids)
            assert kept <= set(_checks(after)), "a check didn't survive deleting what it measures"
        assert bus.undo() is not None
        assert bus.document == before, "undo isn't exact"
        assert bus.redo() is not None
        assert bus.document == after, "redo isn't exact"
        states[position + 1 :] = [after]
        made[position:] = [result.command]
        position += 1
        assert snapshot.loads(snapshot.dumps(after)) == after, "save and load changed it"
        queries = bus.queries
        for check in _checks(after).values():
            outcome = queries.check(check)
            if _named(check) <= after.entities.keys():
                assert outcome.error is None, f"a check of live geometry can't measure: {outcome}"
            else:
                assert outcome.error is not None
                assert outcome.error.code is ErrorCode.ENTITY_NOT_FOUND
    replayed = Bus(kernel=None)
    for command in made[:position]:
        assert isinstance(replayed.execute(command), Applied)
    assert snapshot.dumps(replayed.document) == snapshot.dumps(bus.document), "replay differs"


@settings(max_examples=200, deadline=None)
@given(ops=OPS)
def test_stored_checks_survive_undo_redo_save_load_and_replay(ops: list[Op]) -> None:
    run_session(ops)


def test_the_properties_catch_a_delete_that_takes_checks_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The deliberate break: checks refer to what they measure, so deleting it deletes them,
    # the way dimensions cascade. The property above must fail.
    real = sketch.references

    def cascading(entity: Entity) -> tuple[Ref, ...]:
        if isinstance(entity, Expectation):
            return entity.refs + tuple(Ref(entity=i, feature=Feature.CURVE) for i in entity.ids)
        return real(entity)

    sketch._REFERRERS.clear()
    monkeypatch.setattr(sketch, "references", cascading)
    try:
        with pytest.raises(AssertionError, match="didn't survive"):
            run_session([("rectangle", 0, 0, 10, 5), ("check", 0, 0, "width", 0), ("delete", 0)])
    finally:
        monkeypatch.undo()
        sketch._REFERRERS.clear()
    run_session([("rectangle", 0, 0, 10, 5), ("check", 0, 0, "width", 0), ("delete", 0)])


def test_the_properties_catch_a_check_that_forgets_its_value_on_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A second break: a file that drops a check's tolerance on load.
    real = codec.decode_entity

    def forgetful(data: object, path: str) -> Entity:
        entity = real(data, path)
        if isinstance(entity, Expectation):
            return Expectation(
                metric=entity.metric,
                expected=entity.expected,
                tolerance=0.0,
                refs=entity.refs,
                ids=entity.ids,
            )
        return entity

    monkeypatch.setattr(codec, "decode_entity", forgetful)
    with pytest.raises(AssertionError, match="save and load"):
        run_session([("rectangle", 0, 0, 10, 5), ("check", 0, 0, "width", 0)])


def test_history_names_each_change_to_a_check() -> None:
    bus = plate()
    made = bus.execute(width_check())
    assert isinstance(made, Applied)
    (check,) = made.created_ids
    labels = [
        made.label,
        bus.execute(ModifyEntity(id=check, changes={"expected": 121.0})).label,  # type: ignore[union-attr]
        bus.execute(ModifyEntity(id=check, changes={"tolerance": 0.5})).label,  # type: ignore[union-attr]
        bus.execute(DeleteEntities(ids=(check,))).label,  # type: ignore[union-attr]
    ]
    assert labels == ["Create Check", "Edit Check", "Edit Check", "Delete Check"]


def test_an_area_check_is_stored_without_a_kernel_but_not_an_open_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Found by N4's tests run without OCCT, as Linux CI and an app without the extra run:
    # validation used this machine's default kernel, and refused every area check.
    from caliper.engine.commands import handlers
    from caliper.engine.queries import DocumentQueries

    monkeypatch.setattr(handlers, "DocumentQueries", lambda doc: DocumentQueries(doc, kernel=None))
    bus = Bus(kernel=None)
    corners = [Point2(x=0, y=0), Point2(x=30, y=0), Point2(x=30, y=20), Point2(x=0, y=20)]
    for a, b in zip(corners, [*corners[1:], corners[0]], strict=True):
        bus.execute(CreateLine(start=a, end=b))
    lines = tuple(EntityId(f"e{n}") for n in range(1, 5))
    stored = bus.execute(CreateCheck(metric=Metric.AREA, expected=600.0, tolerance=0.01, ids=lines))
    assert isinstance(stored, Applied)
    result = bus.queries.check(bus.document.entities[stored.created_ids[0]])  # type: ignore[arg-type]
    assert result.error is not None
    assert result.error.code is ErrorCode.KERNEL_UNAVAILABLE  # measured where there's a kernel
    open_ = bus.execute(
        CreateCheck(metric=Metric.AREA, expected=1.0, tolerance=0.01, ids=lines[:3])
    )
    assert isinstance(open_, Rejected)
    assert open_.errors[0].code is ErrorCode.PROFILE_NOT_CLOSED
