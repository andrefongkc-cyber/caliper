"""Degrees of freedom, the four sketch states, conflicts, and which constraints apply.

DOF comes from the equations: unknowns minus the rank of the constraint Jacobian, per
cluster and per entity. Nothing here is counted from UI state.
"""

import json

import pytest

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateArc,
    CreateCircle,
    CreateConstraint,
    CreateDimension,
    CreateLine,
    CreatePoint,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    Rejected,
)
from caliper.contracts.document import ConstraintType, EntityId, Feature, Point2, Ref
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import ConstraintState, DimensionType
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot

E = EntityId
C = ConstraintType


def pt(x: float, y: float) -> Point2:
    return Point2(x=x, y=y)


def ref(entity: str, feature: str) -> Ref:
    return Ref(entity=E(entity), feature=Feature(feature))


def curve(entity: str) -> Ref:
    return ref(entity, "curve")


def run(bus: Bus, command: Command) -> Applied:
    result = bus.execute(command)
    assert isinstance(result, Applied), result
    return result


def rejected(result: CommandResult) -> Error:
    assert isinstance(result, Rejected), result
    return result.errors[0]


def constrain(bus: Bus, type_: ConstraintType, *refs: Ref) -> Applied:
    return run(bus, CreateConstraint(type=type_, refs=refs))


def square_of_lines(bus: Bus) -> None:
    """e1..e4: a closed square of four lines, e5..e8 its corners, e9..e12 level sides."""
    corners = [pt(0, 0), pt(100, 2), pt(98, 51), pt(-1, 49)]
    for i in range(4):
        run(bus, CreateLine(start=corners[i], end=corners[(i + 1) % 4]))
    for i in range(4):
        first, second = f"e{i + 1}", f"e{(i + 1) % 4 + 1}"
        constrain(bus, C.COINCIDENT, ref(first, "end"), ref(second, "start"))
    constrain(bus, C.HORIZONTAL, curve("e1"))
    constrain(bus, C.VERTICAL, curve("e2"))
    constrain(bus, C.HORIZONTAL, curve("e3"))
    constrain(bus, C.VERTICAL, curve("e4"))


# --- Degrees of freedom -----------------------------------------------------------------------


def test_an_empty_sketch_has_nothing_to_move() -> None:
    status = Bus(kernel=None).queries.solve_status()
    assert (status.state, status.dof, dict(status.entity_dof)) == (ConstraintState.FULLY, 0, {})


@pytest.mark.parametrize(
    ("command", "freedom"),
    [
        (CreatePoint(position=pt(0, 0)), 2),
        (CreateLine(start=pt(0, 0), end=pt(1, 0)), 4),
        (CreateCircle(center=pt(0, 0), radius=1), 3),
        (CreateArc(center=pt(0, 0), radius=1, start_angle=0, sweep_angle=90), 5),
        (CreateRectangle(corner=pt(0, 0), width=1, height=1), 4),
    ],
)
def test_free_geometry_has_its_own_degrees_of_freedom(command: Command, freedom: int) -> None:
    bus = Bus(kernel=None)
    run(bus, command)
    status = bus.queries.solve_status()
    assert (status.state, status.dof, dict(status.entity_dof)) == (
        ConstraintState.UNDER,
        freedom,
        {"e1": freedom},
    )


def test_a_rectangle_of_lines_goes_from_16_to_4_to_fully_constrained() -> None:
    bus = Bus(kernel=None)
    square_of_lines(bus)
    status = bus.queries.solve_status()
    assert (status.state, status.dof) == (ConstraintState.UNDER, 4)  # x, y, width, height
    # Two dimensions and a fixed corner take the last four.
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(50, -10), value=100.0))
    run(bus, CreateDimension(refs=(curve("e2"),), placement=pt(110, 25), value=50.0))
    constrain(bus, C.FIX, ref("e1", "start"))
    status = bus.queries.solve_status()
    assert (status.state, status.dof) == (ConstraintState.FULLY, 0)
    assert set(status.entity_dof.values()) == {0}

    def corner(id: str, feature: str) -> Point2:
        point = bus.queries.feature_point(ref(id, feature))
        assert isinstance(point, Point2)
        return point

    fixed = corner("e1", "start")
    top_right = corner("e3", "start")
    assert (top_right.x - fixed.x, top_right.y - fixed.y) == pytest.approx((100, 50), abs=1e-9)
    # The width is still a parameter: change it and the right side moves, the fixed one stays.
    run(bus, ModifyEntity(id=E("e13"), changes={"value": 120.0}))
    assert corner("e4", "end") == corner("e1", "start") == fixed
    top_right = corner("e2", "end")
    assert (top_right.x - fixed.x, top_right.y - fixed.y) == pytest.approx((120, 50), abs=1e-9)


def test_per_entity_freedom_shows_what_can_still_move() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreateLine(start=pt(10, 0), end=pt(10, 10)))
    constrain(bus, C.FIX, curve("e1"))
    constrain(bus, C.COINCIDENT, ref("e1", "end"), ref("e2", "start"))
    status = bus.queries.solve_status()
    assert dict(status.entity_dof) == {"e1": 0, "e2": 2}  # e2 can only swing its free end
    assert status.dof == 2


def test_driven_dimensions_and_construction_do_not_change_freedom() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0), construction=True))
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(5, -5)))
    assert bus.queries.solve_status().dof == 4


# --- Over-constrained and conflicting ----------------------------------------------------------


def test_redundant_constraints_are_rejected_and_name_what_implies_them() -> None:
    bus = Bus(kernel=None)
    square_of_lines(bus)
    before = bus.document
    error = rejected(
        bus.execute(CreateConstraint(type=C.PARALLEL, refs=(curve("e1"), curve("e3"))))
    )
    assert error.code is ErrorCode.CONSTRAINT_REDUNDANT
    assert set(error.ids) == {"e9", "e11"}  # the two horizontals already say it
    assert bus.document == before


def test_a_conflicting_dimension_change_is_rejected_and_nothing_moves() -> None:
    bus = Bus(kernel=None)
    for start, end in ((pt(0, 0), pt(30, 0)), (pt(30, 0), pt(30, 40)), (pt(30, 40), pt(0, 0))):
        run(bus, CreateLine(start=start, end=end))
    for a, b in (("e1", "e2"), ("e2", "e3"), ("e3", "e1")):
        constrain(bus, C.COINCIDENT, ref(a, "end"), ref(b, "start"))
    for id, placement in (("e1", pt(15, -10)), ("e2", pt(40, 20)), ("e3", pt(0, 30))):
        run(bus, CreateDimension(refs=(curve(id),), placement=placement, value=None))
    for dimension, length in (("e7", 30.0), ("e8", 40.0), ("e9", 50.0)):
        run(bus, ModifyEntity(id=E(dimension), changes={"value": length}))
    before = bus.document
    # A 30-40-100 triangle doesn't exist.
    error = rejected(bus.execute(ModifyEntity(id=E("e9"), changes={"value": 100.0})))
    assert error.code is ErrorCode.CONSTRAINT_CONFLICT
    assert set(error.ids) >= {"e7", "e8"}  # dropping either other side would let it close
    assert bus.document == before
    assert bus.undo_label == "Change Value"  # the rejection recorded nothing


def test_the_conflict_can_be_resolved_by_removing_the_named_constraint() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    constrain(bus, C.FIX, curve("e1"))
    error = rejected(bus.execute(ModifyEntity(id=E("e1"), changes={"end": pt(20, 5)})))
    assert error.ids == ("e1", "e2")
    run(bus, DeleteEntities(ids=(E("e2"),)))
    run(bus, ModifyEntity(id=E("e1"), changes={"end": pt(20, 5)}))


def test_a_file_saved_over_constrained_still_opens_and_says_so() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    constrain(bus, C.HORIZONTAL, curve("e1"))
    data = json.loads(snapshot.dumps(bus.document))
    # Hand-edited: a second horizontal repeats the first.
    data["document"]["entities"]["e3"] = {
        "kind": "constraint",
        "type": "horizontal",
        "refs": [{"entity": "e1", "feature": "curve"}],
    }
    status = Bus(snapshot.loads(json.dumps(data)), kernel=None).queries.solve_status()
    assert (status.state, status.redundant, status.conflicting) == (
        ConstraintState.OVER,
        ("e3",),
        (),
    )


def test_a_file_whose_constraints_do_not_hold_opens_as_conflicting() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    constrain(bus, C.HORIZONTAL, curve("e1"))
    data = json.loads(snapshot.dumps(bus.document))
    data["document"]["entities"]["e1"]["end"]["y"] = 3.0  # edited by hand, unsolved
    opened = Bus(snapshot.loads(json.dumps(data)), kernel=None)
    status = opened.queries.solve_status()
    assert (status.state, status.conflicting) == (ConstraintState.CONFLICTING, ("e2",))
    # Nothing moved on load. The next command that touches the geometry solves it.
    assert opened.queries.feature_point(ref("e1", "end")) == pt(10, 3)
    run(opened, ModifyEntity(id=E("e1"), changes={"start": pt(0, 1)}))
    assert opened.queries.solve_status().state is ConstraintState.UNDER


# --- Applicability -------------------------------------------------------------------------------


def applicable(bus: Bus, *refs: Ref) -> set[str]:
    return {o.type.value for o in bus.queries.applicable_constraints(refs) if o.error is None}


def sketch() -> Bus:
    """e1, e2: lines; e3, e4: circles; e5: arc; e6: point; e7: rectangle."""
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 1)))
    run(bus, CreateLine(start=pt(0, 5), end=pt(9, 7)))
    run(bus, CreateCircle(center=pt(30, 0), radius=4))
    run(bus, CreateCircle(center=pt(45, 0), radius=6))
    run(bus, CreateArc(center=pt(60, 0), radius=5, start_angle=0, sweep_angle=90))
    run(bus, CreatePoint(position=pt(5, 20)))
    run(bus, CreateRectangle(corner=pt(0, 40), width=20, height=10))
    return bus


def test_one_line_offers_level_fix_and_its_dimensions() -> None:
    assert applicable(sketch(), curve("e1")) == {
        "horizontal",
        "vertical",
        "fix",
        "length",
        "horizontal_distance",
        "vertical_distance",
    }


def test_two_lines() -> None:
    assert applicable(sketch(), curve("e1"), curve("e2")) == {
        "coincident",
        "parallel",
        "perpendicular",
        "equal",
        "curvature",
        "angle",
    }


def test_two_circles() -> None:
    assert applicable(sketch(), curve("e3"), curve("e4")) == {
        "coincident",
        "concentric",
        "tangent",
        "equal",
        "distance",
        "horizontal_distance",
        "vertical_distance",
    }


def test_a_line_and_a_circle() -> None:
    assert applicable(sketch(), curve("e1"), curve("e3")) == {"tangent", "normal", "distance"}


def test_a_point_and_a_line() -> None:
    assert applicable(sketch(), ref("e6", "point"), curve("e1")) == {
        "coincident",
        "midpoint",
        "distance",
    }


def test_symmetric_needs_a_mirror_line() -> None:
    options = applicable(sketch(), ref("e6", "point"), ref("e1", "start"), curve("e2"))
    assert options == {"symmetric"}


def test_a_rectangle_side_is_a_line_but_is_already_level() -> None:
    options = applicable(sketch(), ref("e7", "bottom"))
    assert "horizontal" not in options
    assert {"fix", "length"} <= options


def test_every_type_is_answered_with_the_refs_in_storage_order_or_a_reason() -> None:
    options = sketch().queries.applicable_constraints([ref("e6", "point"), curve("e1")])
    assert [o.type for o in options] == [*ConstraintType, *DimensionType]
    by_type = {o.type: o for o in options}
    assert by_type[C.MIDPOINT].refs == (curve("e1"), ref("e6", "point"))
    assert by_type[C.PARALLEL].error is not None
    assert by_type[C.PARALLEL].error.code is ErrorCode.CONSTRAINT_NOT_APPLICABLE
    assert "two lines" in by_type[C.PARALLEL].error.message
    assert by_type[C.PIERCE].error is not None
    assert by_type[C.PIERCE].error.code is ErrorCode.CONSTRAINT_UNSUPPORTED


def test_bad_references_make_every_option_report_the_reference_error() -> None:
    options = sketch().queries.applicable_constraints([ref("nope", "start")])
    assert {o.error.code for o in options if o.error} == {ErrorCode.ENTITY_NOT_FOUND}
    assert all(o.error is not None for o in options)


def test_constraints_on_an_entity() -> None:
    bus = sketch()
    constrain(bus, C.PARALLEL, curve("e1"), curve("e2"))
    constrain(bus, C.TANGENT, curve("e3"), curve("e4"))
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(5, -5)))
    assert bus.queries.constraints_on([E("e1")]) == ("e10", "e8")
    assert bus.queries.constraints_on([E("e4"), E("unknown")]) == ("e9",)
