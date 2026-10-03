"""Extrude, the part's first solid feature (V2's F3, ADR 0013): made, edited, deleted, and
undone like any change; refused when it can't be made now; failing, with the reason, when a
later edit breaks what it reads. Run on the analytic kernel; the milestone runs on both."""

import math

import pytest

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateCheck,
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    CreateSketch,
    DeleteEntities,
    ModifyEntity,
    Rejected,
)
from caliper.contracts.document import (
    FIRST_SKETCH,
    Document,
    EntityId,
    Extrude,
    ExtrudeOperation,
    Metric,
    Plane,
    Point2,
)
from caliper.contracts.errors import Error, ErrorCode, LoadError
from caliper.contracts.queries import Expectation, SolidProperties
from caliper.engine import features, graph
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry.fake_kernel import FakeKernel
from caliper.engine.io import snapshot

ORIGIN = Point2(x=0.0, y=0.0)
E0 = FIRST_SKETCH


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def refused(result: CommandResult) -> Error:
    assert isinstance(result, Rejected), result
    return result.errors[0]


def created(bus: Bus, command: Command) -> EntityId:
    (id,) = applied(bus.execute(command)).created_ids
    return id


def part() -> Bus:
    return Bus(kernel=FakeKernel())


def solid(bus: Bus, *ids: EntityId) -> SolidProperties:
    found = bus.queries.solid_properties(ids)
    assert not isinstance(found, Error), found
    return found


def plate(bus: Bus, width: float = 120.0, height: float = 50.0) -> EntityId:
    return created(bus, CreateRectangle(corner=ORIGIN, width=width, height=height))


# --- Making one ---------------------------------------------------------------------------


def test_an_extrude_is_a_feature_after_its_sketch_and_undoes_like_any_change() -> None:
    bus = part()
    plate(bus)
    result = applied(bus.execute(CreateExtrude(depth=10.0)))
    assert result.label == "Extrude"
    (extrude,) = result.created_ids
    assert result.command == CreateExtrude(depth=10.0, sketch=E0, id=extrude, reversed=False)
    assert bus.document.features[1] == Extrude(id=extrude, sketch=E0, depth=10.0)
    assert extrude not in bus.document.entities  # a feature, not an entity
    assert solid(bus).volume == pytest.approx(60_000.0, rel=1e-12)
    box = solid(bus).bounding_box
    assert box is not None
    assert (box.x_max, box.y_max, box.z_max) == (120.0, 50.0, 10.0)
    bus.undo()
    assert len(bus.document.features) == 1
    assert refused_query(bus).code is ErrorCode.SELECTION_EMPTY
    bus.redo()
    assert solid(bus).volume == pytest.approx(60_000.0, rel=1e-12)


def refused_query(bus: Bus) -> Error:
    found = bus.queries.solid_properties()
    assert isinstance(found, Error)
    return found


def test_an_extrude_needs_one_closed_profile_now() -> None:
    bus = part()
    error = refused(bus.execute(CreateExtrude(depth=10.0)))
    assert (error.code, error.field) == (ErrorCode.SELECTION_EMPTY, "sketch")
    created(bus, CreateLine(start=ORIGIN, end=Point2(x=10.0, y=0.0)))
    assert refused(bus.execute(CreateExtrude(depth=10.0))).code is ErrorCode.PROFILE_NOT_CLOSED
    bus = part()
    created(bus, CreateRectangle(corner=ORIGIN, width=1.0, height=1.0, construction=True))
    assert refused(bus.execute(CreateExtrude(depth=10.0))).code is ErrorCode.SELECTION_EMPTY


@pytest.mark.parametrize("depth", [0.0, -5.0, math.inf])
def test_a_depth_must_be_more_than_nothing_and_finite(depth: float) -> None:
    bus = part()
    plate(bus)
    error = refused(bus.execute(CreateExtrude(depth=depth)))
    assert error.field == "depth"


def test_named_profile_geometry_must_be_drawn_in_its_sketch_and_not_construction() -> None:
    bus = part()
    rectangle = plate(bus)
    layout = created(bus, CreateCircle(center=ORIGIN, radius=5.0, construction=True))
    other = created(bus, CreateSketch(plane=Plane.XZ))
    there = created(bus, CreateCircle(center=ORIGIN, radius=5.0, sketch=other))
    cases = {
        (rectangle, there): ErrorCode.SKETCH_MIXED,
        (layout,): ErrorCode.PROFILE_CONSTRUCTION,
        (EntityId("e99"),): ErrorCode.ENTITY_NOT_FOUND,
    }
    for ids, code in cases.items():
        error = refused(bus.execute(CreateExtrude(depth=1.0, sketch=E0, ids=ids)))
        assert error.code is code, ids
    # With two sketches, say which.
    assert refused(bus.execute(CreateExtrude(depth=1.0))).code is ErrorCode.SKETCH_REQUIRED
    created(bus, CreateExtrude(depth=1.0, sketch=E0, ids=(rectangle,)))


def test_the_first_extrude_adds() -> None:
    bus = part()
    plate(bus)
    error = refused(bus.execute(CreateExtrude(depth=10.0, operation=ExtrudeOperation.REMOVE)))
    assert (error.code, error.field) == (ErrorCode.VALUE_OUT_OF_RANGE, "operation")


def test_a_reversed_extrude_goes_against_the_normal_and_can_be_flipped_back() -> None:
    """ADR 0016: the same profile swept the other way. Left as None it goes along the
    normal, and the resolved command says so."""
    bus = part()
    plate(bus)
    result = applied(bus.execute(CreateExtrude(depth=10.0, reversed=True)))
    (extrude,) = result.created_ids
    assert result.command == CreateExtrude(depth=10.0, sketch=E0, id=extrude, reversed=True)
    assert bus.document.features[1] == Extrude(id=extrude, sketch=E0, depth=10.0, reversed=True)
    box = solid(bus).bounding_box
    assert box is not None
    assert (box.z_min, box.z_max) == (-10.0, 0.0)
    assert solid(bus).volume == pytest.approx(60_000.0, rel=1e-12)
    applied(bus.execute(ModifyEntity(id=extrude, changes={"reversed": False})))
    box = solid(bus).bounding_box
    assert box is not None
    assert (box.z_min, box.z_max) == (0.0, 10.0)
    bus.undo()
    assert bus.document.features[1] == Extrude(id=extrude, sketch=E0, depth=10.0, reversed=True)


def test_reversed_must_be_true_or_false() -> None:
    bus = part()
    plate(bus)
    result = bus.execute(CreateExtrude(depth=10.0, reversed="yes"))  # type: ignore[arg-type]
    assert isinstance(result, Rejected)
    assert [(e.code, e.field) for e in result.errors] == [(ErrorCode.VALUE_WRONG_TYPE, "reversed")]


# --- Several, and what they build ---------------------------------------------------------


def test_a_remove_through_the_plate_leaves_a_hole() -> None:
    bus = part()
    rectangle = plate(bus)
    hole = created(bus, CreateCircle(center=Point2(x=60.0, y=25.0), radius=10.0))
    created(bus, CreateExtrude(depth=10.0, ids=(rectangle,)))
    cut = created(bus, CreateExtrude(depth=10.0, ids=(hole,), operation=ExtrudeOperation.REMOVE))
    expected = (6_000.0 - math.pi * 100.0) * 10.0
    assert solid(bus).volume == pytest.approx(expected, rel=1e-12)
    assert solid(bus, cut).volume == pytest.approx(expected, rel=1e-12)
    # Drawn as one profile with a hole, one extrude gives the same.
    single = part()
    plate(single)
    created(single, CreateCircle(center=Point2(x=60.0, y=25.0), radius=10.0))
    created(single, CreateExtrude(depth=10.0))
    assert solid(single).volume == pytest.approx(expected, rel=1e-12)


def test_extrudes_of_two_sketches_add_up_and_each_is_the_part_as_it_stood() -> None:
    bus = part()
    plate(bus, 10.0, 10.0)
    first = created(bus, CreateExtrude(depth=10.0))
    second_sketch = created(bus, CreateSketch(plane=Plane.XY))
    created(
        bus,
        CreateRectangle(
            corner=Point2(x=20.0, y=0.0), width=10.0, height=10.0, sketch=second_sketch
        ),
    )
    second = created(bus, CreateExtrude(depth=5.0, sketch=second_sketch))
    assert solid(bus, first).volume == pytest.approx(1_000.0)
    assert solid(bus, second).volume == pytest.approx(1_500.0)
    assert solid(bus).volume == pytest.approx(1_500.0)
    assert solid(bus, second_sketch).volume == pytest.approx(1_000.0)  # the part as it stood


def test_asking_for_the_solid_of_something_that_isnt_a_feature() -> None:
    bus = part()
    rectangle = plate(bus)
    assert refused_query(bus).code is ErrorCode.SELECTION_EMPTY  # only a sketch so far
    # No feature makes a solid yet: that needs no kernel to say.
    nothing = Bus(bus.document, kernel=None).queries.solid_properties()
    assert getattr(nothing, "code", None) is ErrorCode.SELECTION_EMPTY
    created(bus, CreateExtrude(depth=10.0))
    for ids, code in {
        (rectangle,): ErrorCode.ENTITY_WRONG_KIND,
        (EntityId("e99"),): ErrorCode.ENTITY_NOT_FOUND,
        (E0, E0): ErrorCode.VALUE_OUT_OF_RANGE,
    }.items():
        found = bus.queries.solid_properties(ids)
        assert isinstance(found, Error)
        assert found.code is code, ids
    no_kernel = Bus(bus.document, kernel=None).queries
    assert getattr(no_kernel.solid_properties(), "code", None) is ErrorCode.KERNEL_UNAVAILABLE
    assert getattr(no_kernel.mesh(), "code", None) is ErrorCode.KERNEL_UNAVAILABLE


def test_a_mesh_draws_the_solid() -> None:
    bus = part()
    plate(bus)
    created(bus, CreateExtrude(depth=10.0))
    mesh = bus.queries.mesh()
    assert not isinstance(mesh, Error)
    assert len(mesh.triangles) == 12  # a box: two triangles a face
    assert bus.queries.mesh() is mesh  # kept while the solid is the same
    assert getattr(bus.queries.mesh(tolerance=0.0), "code", None) is ErrorCode.VALUE_NOT_POSITIVE


# --- Editing, breaking, deleting ----------------------------------------------------------


def test_an_extrude_is_edited_like_any_change() -> None:
    bus = part()
    plate(bus)
    extrude = created(bus, CreateExtrude(depth=10.0))
    result = applied(bus.execute(ModifyEntity(id=extrude, changes={"depth": 20.0})))
    assert result.label == "Change Depth"
    assert solid(bus).volume == pytest.approx(120_000.0)
    bus.undo()
    assert solid(bus).volume == pytest.approx(60_000.0)
    for changes, code in {
        "operation": ({"operation": "remove"}, ErrorCode.VALUE_OUT_OF_RANGE),
        "depth": ({"depth": -1.0}, ErrorCode.VALUE_NOT_POSITIVE),
        "id": ({"id": "e9"}, ErrorCode.VALUE_OUT_OF_RANGE),
        "plane": ({"plane": "xz"}, ErrorCode.FIELD_UNKNOWN),
    }.values():
        error = refused(bus.execute(ModifyEntity(id=extrude, changes=changes)))  # type: ignore[arg-type]
        assert error.code is code, changes


def test_an_extrude_reads_only_a_sketch_before_it() -> None:
    bus = part()
    plate(bus)
    extrude = created(bus, CreateExtrude(depth=10.0))
    later = created(bus, CreateSketch(plane=Plane.XY))
    error = refused(bus.execute(ModifyEntity(id=extrude, changes={"sketch": later})))
    assert (error.code, error.field) == (ErrorCode.DEPENDENCY_CYCLE, "sketch")
    error = refused(bus.execute(ModifyEntity(id=extrude, changes={"sketch": extrude})))
    assert error.code is ErrorCode.DEPENDENCY_CYCLE  # itself


def test_a_part_that_reads_a_later_feature_is_refused_on_load() -> None:
    """A file can't hold what no command could make: an extrude reading the sketch after it."""
    bus = part()
    plate(bus)
    extrude = created(bus, CreateExtrude(depth=10.0))
    later = created(bus, CreateSketch(plane=Plane.XY))
    sketch, _, after = bus.document.features
    looped = Document(
        entities=bus.document.entities,
        next_id=bus.document.next_id,
        features=(sketch, Extrude(id=extrude, sketch=later, depth=10.0), after),
    )
    ordered = graph.order(looped)
    assert isinstance(ordered, Error)
    assert ordered.code is ErrorCode.DEPENDENCY_CYCLE
    assert ordered.ids == (extrude, later)
    with pytest.raises(LoadError, match="features read only what comes before them"):
        snapshot.loads(snapshot.dumps(looped))
    results = features.solids(looped, FakeKernel())
    assert all(
        r.error is not None and r.error.code is ErrorCode.DEPENDENCY_CYCLE for r in results.values()
    )


def test_an_edit_that_breaks_the_profile_makes_the_extrude_fail_with_the_reason() -> None:
    """Allowed, like a check whose geometry is deleted (C-1): the extrude says why it fails,
    and what comes after it isn't worked out."""
    bus = part()
    corners = [ORIGIN, Point2(x=10.0, y=0.0), Point2(x=10.0, y=10.0)]
    lines = [
        created(bus, CreateLine(start=a, end=b))
        for a, b in zip(corners, [*corners[1:], corners[0]], strict=True)
    ]
    extrude = created(bus, CreateExtrude(depth=2.0))
    later = created(bus, CreateSketch(plane=Plane.XY))
    created(bus, CreateCircle(center=Point2(x=50.0, y=0.0), radius=1.0, sketch=later))
    after = created(bus, CreateExtrude(depth=2.0, sketch=later))
    assert bus.queries.feature_error(extrude) is None
    applied(bus.execute(DeleteEntities(ids=(lines[0],))))
    error = bus.queries.feature_error(extrude)
    assert error is not None
    assert error.code is ErrorCode.PROFILE_NOT_CLOSED
    downstream = bus.queries.feature_error(after)
    assert downstream is not None
    assert (downstream.code, downstream.ids) == (ErrorCode.FEATURE_FAILED, (extrude,))
    assert getattr(bus.queries.solid_properties(), "code", None) is ErrorCode.FEATURE_FAILED
    assert bus.queries.feature_error(E0) is None  # a sketch never fails
    assert bus.queries.feature_error(lines[1]) is None  # not a feature
    bus.undo()
    assert bus.queries.feature_error(extrude) is None


def test_deleting_a_sketch_deletes_the_extrude_that_reads_it_and_undo_restores_both() -> None:
    bus = part()
    plate(bus)
    extrude = created(bus, CreateExtrude(depth=10.0))
    whole = bus.document
    applied(bus.execute(DeleteEntities(ids=(E0,))))
    assert bus.document.features == ()
    assert dict(bus.document.entities) == {}
    bus.undo()
    assert bus.document == whole
    applied(bus.execute(DeleteEntities(ids=(extrude,))))
    assert [f.id for f in bus.document.features] == [E0]  # the sketch stays


def test_deleting_what_an_extrude_needs_makes_it_fail_and_undo_mends_it() -> None:
    """Allowed, as an edit that opens the profile is: deleting the extrude a cut builds on
    leaves the cut with nothing to cut, and deleting geometry an extrude names leaves it a
    name with nothing behind it. Each says why, and undo puts the solid back (F8)."""
    bus = part()
    outline = plate(bus)
    hole = created(bus, CreateCircle(center=Point2(x=60.0, y=25.0), radius=5.0))
    boss = created(bus, CreateExtrude(depth=10.0, ids=(outline,)))
    cut = created(bus, CreateExtrude(depth=10.0, ids=(hole,), operation=ExtrudeOperation.REMOVE))
    whole = solid(bus).volume
    assert whole == pytest.approx(60_000.0 - math.pi * 25.0 * 10.0)

    applied(bus.execute(DeleteEntities(ids=(boss,))))
    error = bus.queries.feature_error(cut)
    assert error is not None
    assert (error.code, error.field) == (ErrorCode.VALUE_OUT_OF_RANGE, "operation")
    assert "no solid before it" in error.message
    assert bus.queries.solid_properties() == error
    bus.undo()
    assert solid(bus).volume == whole

    applied(bus.execute(DeleteEntities(ids=(hole,))))
    assert [f.id for f in bus.document.features] == [E0, boss, cut]  # the cut stays, failing
    error = bus.queries.feature_error(cut)
    assert error is not None
    assert (error.code, error.ids) == (ErrorCode.ENTITY_NOT_FOUND, (hole,))
    assert solid(bus, boss).volume == pytest.approx(60_000.0)  # the part before it stands
    bus.undo()
    assert solid(bus).volume == whole


# --- Checks of volume ---------------------------------------------------------------------


def test_a_volume_check_is_stored_and_follows_the_part() -> None:
    bus = part()
    rectangle = plate(bus)
    created(bus, CreateExtrude(depth=10.0))
    check = created(bus, CreateCheck(metric=Metric.VOLUME, expected=60_000.0, tolerance=1e-6))
    stored = bus.document.entities[check]
    assert isinstance(stored, Expectation)
    assert bus.queries.check(stored).passed
    applied(bus.execute(ModifyEntity(id=rectangle, changes={"width": 140.0})))
    result = bus.queries.check(stored)
    assert not result.passed
    assert result.actual == pytest.approx(70_000.0)
    bus.undo()
    assert bus.queries.check(stored).passed


def test_a_volume_check_of_a_part_with_no_solid_isnt_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from caliper.engine import geometry

    monkeypatch.setattr(geometry, "default_kernel", FakeKernel)  # the command checks with it
    bus = part()
    plate(bus)
    check = CreateCheck(metric=Metric.VOLUME, expected=1.0, tolerance=0.0)
    assert refused(bus.execute(check)).code is ErrorCode.SELECTION_EMPTY
    # Without a kernel it's stored, and measured wherever there is one (as for area).
    monkeypatch.setattr(geometry, "default_kernel", lambda: None)
    created(bus, CreateExtrude(depth=1.0))
    created(bus, CreateCheck(metric=Metric.VOLUME, expected=6_000.0, tolerance=1e-6))
