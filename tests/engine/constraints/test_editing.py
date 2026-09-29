"""Editing a constrained sketch after it's built: design intent survives the edits.

A small part, a plate held at a fixed origin with a hole dimensioned from its right and top
edges, is changed the ways a designer changes one: dimensions, moves, removing and adding
constraints, re-pointing and retyping a constraint, undo and redo. What depends on a changed
value follows it, what's held stays held and says what holds it, and every edit undoes
exactly. The rejections say what they mean, for a person or a model reading them.
"""

import pytest

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateCircle,
    CreateConstraint,
    CreateDistanceDimension,
    CreateLine,
    CreatePoint,
    CreateRadialDimension,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    MoveEntities,
    Rejected,
)
from caliper.contracts.document import (
    Circle,
    ConstraintType,
    DistanceOrientation,
    EntityId,
    Feature,
    Line,
    Point2,
    RadialMeasure,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import ErrorCode
from caliper.contracts.queries import ConstraintState
from caliper.engine.commands.bus import Bus

E = EntityId
C = ConstraintType
H, V, ALIGNED = (
    DistanceOrientation.HORIZONTAL,
    DistanceOrientation.VERTICAL,
    DistanceOrientation.ALIGNED,
)


def ref(entity: str, feature: str) -> Ref:
    return Ref(entity=E(entity), feature=Feature(feature))


def run(bus: Bus, command: Command) -> Applied:
    result = bus.execute(command)
    assert isinstance(result, Applied), result
    return result


def refused(bus: Bus, command: Command, code: ErrorCode) -> str:
    before = bus.document
    result = bus.execute(command)
    assert isinstance(result, Rejected), result
    assert result.errors[0].code is code, result.errors
    assert bus.document is before  # nothing changed
    return result.errors[0].message


def part() -> Bus:
    """e1 a point at the origin, fixed (e2); e3 a 100 by 50 plate on it (e4), its width e5 and
    height e6; e7 a Ø8 hole (e8), 20 from the right edge (e9) and 15 below the top (e10); e11
    a reference dimension from the origin to the hole, across."""
    bus = Bus()
    run(bus, CreatePoint(position=Point2(x=0, y=0)))
    run(bus, CreateConstraint(type=C.FIX, refs=(ref("e1", "point"),)))
    run(bus, CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50))
    run(
        bus,
        CreateConstraint(type=C.COINCIDENT, refs=(ref("e1", "point"), ref("e3", "bottom_left"))),
    )
    for a, b, orientation, value in (
        ("bottom_left", "bottom_right", H, 100.0),
        ("bottom_left", "top_left", V, 50.0),
    ):
        run(
            bus,
            CreateDistanceDimension(
                a=ref("e3", a), b=ref("e3", b), orientation=orientation, offset=8, value=value
            ),
        )
    run(bus, CreateCircle(center=Point2(x=80, y=35), radius=4))
    run(
        bus,
        CreateRadialDimension(
            target=E("e7"), measure=RadialMeasure.DIAMETER, label_angle=45, value=8.0
        ),
    )
    for side, value in (("right", 20.0), ("top", 15.0)):
        run(
            bus,
            CreateDistanceDimension(
                a=ref("e7", "center"), b=ref("e3", side), orientation=ALIGNED, offset=5, value=value
            ),
        )
    run(
        bus,
        CreateDistanceDimension(
            a=ref("e1", "point"), b=ref("e7", "center"), orientation=H, offset=-15
        ),
    )
    return bus


def hole(bus: Bus) -> Point2:
    circle = bus.document.entities[E("e7")]
    assert isinstance(circle, Circle)
    return circle.center


def reads(bus: Bus, dimension: str) -> float:
    value = bus.queries.dimension_value(E(dimension))
    assert isinstance(value, float)
    return value


# --- The part, and changing its dimensions ------------------------------------------------


def test_the_part_is_fully_constrained_and_its_reference_dimension_reads_it() -> None:
    bus = part()
    status = bus.queries.solve_status()
    assert status.state is ConstraintState.FULLY
    assert status.dof == 0
    assert reads(bus, "e11") == pytest.approx(80.0)


def test_a_dimension_change_moves_what_is_measured_from_it() -> None:
    bus = part()
    run(bus, ModifyEntity(id=E("e5"), changes={"value": 140.0}))  # wider
    assert hole(bus) == Point2(x=120.0, y=35.0)  # still 20 from the right edge
    assert reads(bus, "e11") == pytest.approx(120.0)  # the reference follows, still driven
    run(bus, ModifyEntity(id=E("e6"), changes={"value": 30.0}))  # lower
    assert hole(bus) == Point2(x=120.0, y=15.0)  # still 15 below the top
    run(bus, ModifyEntity(id=E("e8"), changes={"value": 10.0}))
    circle = bus.document.entities[E("e7")]
    assert isinstance(circle, Circle)
    assert circle.radius == pytest.approx(5.0)
    assert bus.queries.solve_status().dof == 0


# --- Moving what's held -------------------------------------------------------------------


def test_held_geometry_refuses_a_move_and_names_what_holds_it() -> None:
    bus = part()
    message = refused(bus, MoveEntities(ids=(E("e7"),), dx=5, dy=0), ErrorCode.CONSTRAINT_CONFLICT)
    assert "e9 (distance dimension 20.0)" in message
    refused(
        bus,
        ModifyEntity(id=E("e7"), changes={"center": Point2(x=10, y=10)}),
        ErrorCode.CONSTRAINT_CONFLICT,
    )
    message = refused(bus, MoveEntities(ids=(E("e3"),), dx=5, dy=0), ErrorCode.CONSTRAINT_CONFLICT)
    assert "e2 (fix)" in message


def test_removing_a_dimension_frees_what_it_held_and_nothing_moves() -> None:
    bus = part()
    before = bus.document.entities
    run(bus, DeleteEntities(ids=(E("e9"),)))
    assert all(
        bus.document.entities[id] is entity for id, entity in before.items() if id != E("e9")
    )
    status = bus.queries.solve_status()
    assert status.dof == 1
    assert status.entity_dof[E("e7")] == 1  # only the hole, only across
    run(bus, MoveEntities(ids=(E("e7"),), dx=5, dy=0))  # across, as it's now free to
    assert hole(bus) == Point2(x=85.0, y=35.0)
    # A move is exact: one against what still holds the hole is refused, not bent to fit.
    message = refused(bus, MoveEntities(ids=(E("e7"),), dx=5, dy=3), ErrorCode.CONSTRAINT_CONFLICT)
    assert "e10 (distance dimension 15.0)" in message


# --- Adding to a finished part ------------------------------------------------------------


def test_adding_to_a_fully_constrained_part_is_refused_as_redundant_or_conflicting() -> None:
    bus = part()
    again = CreateDistanceDimension(
        a=ref("e7", "center"), b=ref("e3", "right"), orientation=ALIGNED, offset=9, value=20.0
    )
    message = refused(bus, again, ErrorCode.CONSTRAINT_REDUNDANT)
    assert "already implied by" in message
    assert "driven dimension (no value)" in message  # what to do instead
    down = CreateDistanceDimension(
        a=ref("e1", "point"), b=ref("e7", "center"), orientation=V, offset=-25, value=5.0
    )
    message = refused(bus, down, ErrorCode.CONSTRAINT_CONFLICT)
    assert "e10 (distance dimension 15.0)" in message
    assert "e6 (distance dimension 50.0)" in message


# --- Editing a constraint -----------------------------------------------------------------


def two_lines() -> Bus:
    """e1 and e2, both a little off level; e3 holds e1 horizontal."""
    bus = Bus()
    run(bus, CreateLine(start=Point2(x=0, y=0), end=Point2(x=10, y=1)))
    run(bus, CreateLine(start=Point2(x=0, y=5), end=Point2(x=10, y=7)))
    run(bus, CreateConstraint(type=C.HORIZONTAL, refs=(ref("e1", "curve"),)))
    return bus


def line(bus: Bus, id: str) -> Line:
    found = bus.document.entities[E(id)]
    assert isinstance(found, Line)
    return found


def test_a_constraint_pointed_at_other_geometry_moves_that_and_frees_the_first() -> None:
    bus = two_lines()
    first = line(bus, "e1")
    run(bus, ModifyEntity(id=E("e3"), changes={"refs": (ref("e2", "curve"),)}))
    assert line(bus, "e1") is first  # left where it was, free now
    assert line(bus, "e2").start.y == pytest.approx(line(bus, "e2").end.y)
    assert bus.queries.solve_status().entity_dof[E("e1")] == 4


def test_a_constraint_s_type_can_change() -> None:
    bus = two_lines()
    run(bus, ModifyEntity(id=E("e3"), changes={"type": C.VERTICAL}))
    assert line(bus, "e1").start.x == pytest.approx(line(bus, "e1").end.x)


def test_an_edit_that_would_repeat_a_constraint_is_refused() -> None:
    bus = two_lines()
    run(bus, CreateConstraint(type=C.HORIZONTAL, refs=(ref("e2", "curve"),)))
    message = refused(
        bus,
        ModifyEntity(id=E("e4"), changes={"refs": (ref("e1", "curve"),)}),
        ErrorCode.CONSTRAINT_REDUNDANT,
    )
    assert "already implied by e3 (horizontal)" in message


def test_an_edit_into_a_conflict_is_refused_and_names_it() -> None:
    bus = two_lines()
    run(bus, CreateLine(start=Point2(x=0, y=20), end=Point2(x=10, y=25)))  # e4, pinned
    run(bus, CreateConstraint(type=C.FIX, refs=(ref("e4", "start"),)))
    run(bus, CreateConstraint(type=C.FIX, refs=(ref("e4", "end"),)))
    message = refused(
        bus,
        ModifyEntity(id=E("e3"), changes={"refs": (ref("e4", "curve"),)}),
        ErrorCode.CONSTRAINT_CONFLICT,
    )
    assert "e5 (fix)" in message or "e6 (fix)" in message


def test_an_edit_to_a_missing_or_unsuitable_reference_is_refused() -> None:
    bus = two_lines()
    refused(
        bus,
        ModifyEntity(id=E("e3"), changes={"refs": (ref("e99", "curve"),)}),
        ErrorCode.ENTITY_NOT_FOUND,
    )
    run(bus, CreateCircle(center=Point2(x=30, y=0), radius=2))  # e4
    refused(
        bus,
        ModifyEntity(id=E("e3"), changes={"refs": (ref("e4", "curve"),)}),
        ErrorCode.CONSTRAINT_NOT_APPLICABLE,
    )
    refused(
        bus, ModifyEntity(id=E("e3"), changes={"type": C.PIERCE}), ErrorCode.CONSTRAINT_UNSUPPORTED
    )


def test_a_dimension_can_be_pointed_at_other_geometry() -> None:
    bus = Bus()
    run(bus, CreateLine(start=Point2(x=0, y=0), end=Point2(x=10, y=0)))
    run(bus, CreateLine(start=Point2(x=0, y=5), end=Point2(x=20, y=5)))
    run(
        bus,
        CreateDistanceDimension(
            a=ref("e1", "start"), b=ref("e1", "end"), orientation=ALIGNED, offset=3, value=10.0
        ),
    )
    run(bus, ModifyEntity(id=E("e3"), changes={"a": ref("e2", "start"), "b": ref("e2", "end")}))
    assert line(bus, "e2").end == Point2(x=10.0, y=5.0)  # the 20 mm line is 10 now


# --- Undo and redo ------------------------------------------------------------------------


def test_every_edit_undoes_and_redoes_exactly() -> None:
    bus = part()
    start = bus.document
    edits: list[Command] = [
        ModifyEntity(id=E("e5"), changes={"value": 140.0}),
        ModifyEntity(id=E("e6"), changes={"value": 30.0}),
        DeleteEntities(ids=(E("e9"),)),
        MoveEntities(ids=(E("e7"),), dx=5, dy=0),
        CreateDistanceDimension(
            a=ref("e1", "point"), b=ref("e7", "center"), orientation=H, offset=-20, value=90.0
        ),
        ModifyEntity(id=E("e11"), changes={"value": 90.0}),  # the reference made driving
    ]
    after = []
    for edit in edits[:-1]:
        run(bus, edit)
        after.append(bus.document)
    # A second dimension of the same distance can't drive: it would repeat the first.
    refused(bus, edits[-1], ErrorCode.CONSTRAINT_REDUNDANT)
    for document in reversed([start, *after[:-1]]):
        bus.undo()
        assert bus.document == document
    for document in after:
        bus.redo()
        assert bus.document == document
    assert bus.queries.solve_status().dof == 0


# --- What a rejection says ----------------------------------------------------------------


def test_a_constraint_that_repeats_part_of_another_says_it_is_partly_implied() -> None:
    # Fixing a line pins four numbers; a horizontal already pins one of them. Caliper refuses
    # the fix, but it used to say the fix was "already implied" by the horizontal, which a
    # model took to mean the line was fixed.
    bus = two_lines()
    message = refused(
        bus,
        CreateConstraint(type=C.FIX, refs=(ref("e1", "curve"),)),
        ErrorCode.CONSTRAINT_REDUNDANT,
    )
    assert "partly implied by e3 (horizontal)" in message
    assert "already implied" not in message


def test_a_value_no_geometry_can_meet_is_refused_without_blaming_itself() -> None:
    bus = part()
    message = refused(
        bus, ModifyEntity(id=E("e5"), changes={"value": 1e-12}), ErrorCode.CONSTRAINT_CONFLICT
    )
    assert "can't be satisfied by the geometry it refers to" in message
    assert "conflicts with e5" not in message  # it used to name itself as the conflict


# --- Known limitations --------------------------------------------------------------------


def test_c12_a_constraint_that_holds_only_by_squeezing_to_nothing_is_accepted_today() -> None:
    """Known issue C-12, pinned as it is: the fix (one scale for convergence and collapse)
    changes which commands are accepted, and waits for a decision. If this starts failing, the
    behaviour changed: update docs/known-issues.md with it."""
    bus = Bus()
    run(bus, CreateRectangle(corner=Point2(x=0, y=0), width=69, height=1))
    run(
        bus,
        CreateConstraint(type=C.VERTICAL, refs=(ref("e1", "bottom_left"), ref("e1", "center"))),
    )
    squeezed = bus.document.entities[E("e1")]
    assert isinstance(squeezed, Rectangle)
    assert squeezed.width < 1e-6
