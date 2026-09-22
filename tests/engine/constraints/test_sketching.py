"""Constraint suggestions, construction geometry, picking, fillets, files, and transactions."""

import json
from pathlib import Path

import pytest

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateCircle,
    CreateConstraint,
    CreateDimension,
    CreateLine,
    CreatePoint,
    CreateRectangle,
    FilletCorner,
    ModifyEntity,
    Rejected,
)
from caliper.contracts.document import (
    Constraint,
    ConstraintType,
    EntityId,
    Feature,
    Line,
    Point2,
    Ref,
)
from caliper.contracts.errors import ErrorCode
from caliper.contracts.queries import AreaProperties, Suggestion
from caliper.engine.commands.bus import Bus
from caliper.engine.constraints.suggest import Suggestions
from caliper.engine.geometry.fake_kernel import FakeKernel
from caliper.engine.io import snapshot
from caliper.engine.io.canonical import LoadError

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


# --- Suggestions (inference) --------------------------------------------------------------------


def nearly_a_corner() -> Bus:
    """e1 nearly horizontal; e2 nearly vertical, starting almost where e1 ends."""
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(100, 0.5)))
    run(bus, CreateLine(start=pt(100.2, 0.4), end=pt(100.6, 60)))
    return bus


def test_suggestions_find_what_the_geometry_nearly_has() -> None:
    found = {(s.type, s.refs) for s in nearly_a_corner().queries.suggest_constraints(tolerance=0.5)}
    assert (C.HORIZONTAL, (curve("e1"),)) in found
    assert (C.VERTICAL, (curve("e2"),)) in found
    assert (C.COINCIDENT, (ref("e1", "end"), ref("e2", "start"))) in found
    assert (C.PERPENDICULAR, (curve("e1"), curve("e2"))) in found


def test_suggestions_are_not_constraints_until_accepted() -> None:
    bus = nearly_a_corner()
    before = bus.document
    suggestions = bus.queries.suggest_constraints(tolerance=0.5)
    assert bus.document == before  # asking changed nothing
    assert not any(isinstance(e, Constraint) for e in bus.document.entities.values())
    session = Suggestions()
    horizontal = next(s for s in suggestions if s.type is C.HORIZONTAL)
    run(bus, session.accept(horizontal))
    assert bus.document.entities[E("e3")] == Constraint(type=C.HORIZONTAL, refs=(curve("e1"),))


def test_accepted_or_implied_relationships_are_no_longer_suggested() -> None:
    bus = nearly_a_corner()
    run(bus, CreateConstraint(type=C.HORIZONTAL, refs=(curve("e1"),)))
    run(bus, CreateConstraint(type=C.VERTICAL, refs=(curve("e2"),)))
    types = {s.type for s in bus.queries.suggest_constraints(tolerance=0.5)}
    assert C.HORIZONTAL not in types
    assert C.PERPENDICULAR not in types  # horizontal + vertical already imply it
    assert C.COINCIDENT in types


def test_rejecting_ignoring_and_switching_suggestions_off() -> None:
    suggestions = nearly_a_corner().queries.suggest_constraints(tolerance=0.5)
    session = Suggestions()
    session.reject(suggestions[0])
    assert suggestions[0] not in session.visible(suggestions)
    assert len(session.visible(suggestions)) == len(suggestions) - 1  # ignoring needs nothing
    session.enabled = False
    assert session.visible(suggestions) == ()
    session.enabled = True
    session.forget_rejections()
    assert session.visible(suggestions) == suggestions


def test_suggestions_can_be_limited_to_new_geometry_and_sorted_by_how_close() -> None:
    bus = nearly_a_corner()
    run(bus, CreateCircle(center=pt(50, 20.3), radius=20))  # nearly tangent to e1
    only_circle = bus.queries.suggest_constraints([E("e3")], tolerance=0.5)
    assert [(s.type, s.refs) for s in only_circle] == [(C.TANGENT, (curve("e1"), curve("e3")))]
    deviations = [s.deviation for s in bus.queries.suggest_constraints(tolerance=0.5)]
    assert deviations == sorted(deviations)
    assert bus.queries.suggest_constraints(tolerance=-1) == ()


def test_a_midpoint_is_suggested_instead_of_a_coincidence() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 10)))
    run(bus, CreatePoint(position=pt(5.1, 5.0)))
    suggested = bus.queries.suggest_constraints(tolerance=0.2)
    assert [s.type for s in suggested] == [C.MIDPOINT]
    assert isinstance(suggested[0], Suggestion)


# --- Construction geometry ----------------------------------------------------------------------


def test_construction_geometry_is_real_geometry_but_not_a_profile() -> None:
    bus = Bus(kernel=FakeKernel())
    run(bus, CreateCircle(center=pt(0, 0), radius=10, construction=True))
    run(bus, CreateLine(start=pt(0, 0), end=pt(20, 3)))
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e2"), curve("e1"))))  # constrained
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(30, 0), value=30.0))  # dimensioned
    circle = bus.document.entities[E("e1")]
    assert circle.radius == pytest.approx(15.0)
    rim = pt(circle.center.x + 15.0, circle.center.y)  # type: ignore[union-attr]
    assert bus.queries.entity_at_point(rim, 0.1) == "e1"  # picked like anything else
    area = bus.queries.area_properties([E("e1")])
    assert not isinstance(area, AreaProperties)
    assert area.code is ErrorCode.PROFILE_CONSTRUCTION
    run(bus, ModifyEntity(id=E("e1"), changes={"construction": False}))
    assert isinstance(bus.queries.area_properties([E("e1")]), AreaProperties)


def test_the_construction_flag_takes_only_true_or_false() -> None:
    bus = Bus(kernel=None)
    result = bus.execute(CreateLine(start=pt(0, 0), end=pt(1, 0), construction=1))  # type: ignore[arg-type]
    assert isinstance(result, Rejected)
    assert (result.errors[0].code, result.errors[0].field) == (
        ErrorCode.VALUE_WRONG_TYPE,
        "construction",
    )


def test_a_fillet_between_construction_lines_is_construction() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(50, 0), construction=True))
    run(bus, CreateLine(start=pt(50, 0), end=pt(50, 50), construction=True))
    run(bus, FilletCorner(a=E("e1"), b=E("e2"), radius=5))
    assert bus.document.entities[E("e3")].construction is True


# --- Picking --------------------------------------------------------------------------------------


def test_picking_prefers_points_then_the_nearest_curve() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateRectangle(corner=pt(0, 0), width=100, height=50))
    run(bus, CreateCircle(center=pt(200, 0), radius=10))
    assert bus.queries.reference_at_point(pt(0.1, 0.1), 1) == ref("e1", "bottom_left")
    assert bus.queries.reference_at_point(pt(50, 49.5), 1) == ref("e1", "top")
    assert bus.queries.reference_at_point(pt(100.4, 25), 1) == ref("e1", "right")
    assert bus.queries.reference_at_point(pt(210.5, 0), 1) == curve("e2")
    assert bus.queries.reference_at_point(pt(50, 25), 1) == ref("e1", "center")
    assert bus.queries.reference_at_point(pt(30, 25), 1) is None  # inside, but no edge near
    assert bus.queries.reference_at_point(pt(float("nan"), 0), 1) is None


# --- Fillet with constraints ---------------------------------------------------------------------


def test_a_fillet_removes_the_corner_it_rounds_and_keeps_the_rest() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(50, 0)))
    run(bus, CreateLine(start=pt(50, 0), end=pt(50, 40)))
    run(bus, CreateConstraint(type=C.COINCIDENT, refs=(ref("e1", "end"), ref("e2", "start"))))
    run(bus, CreateConstraint(type=C.HORIZONTAL, refs=(curve("e1"),)))
    result = run(bus, FilletCorner(a=E("e1"), b=E("e2"), radius=5))
    assert E("e3") in result.delta.removed  # the corner coincidence went with the corner
    assert E("e4") in bus.document.entities  # horizontal still holds after trimming
    assert bus.queries.solve_status().conflicting == ()
    bus.undo()
    assert E("e3") in bus.document.entities


def test_a_fillet_that_would_break_a_driving_length_is_rejected() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(50, 0)))
    run(bus, CreateLine(start=pt(50, 0), end=pt(50, 40)))
    run(bus, CreateConstraint(type=C.FIX, refs=(curve("e2"),)))
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(25, -5), value=50.0))
    result = bus.execute(FilletCorner(a=E("e1"), b=E("e2"), radius=5))
    assert isinstance(result, Rejected)
    assert result.errors[0].code is ErrorCode.CONSTRAINT_CONFLICT


# --- Files ----------------------------------------------------------------------------------------


def constrained() -> Bus:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(100, 3), construction=True))
    run(bus, CreateCircle(center=pt(50, 30), radius=10))
    run(bus, CreateConstraint(type=C.HORIZONTAL, refs=(curve("e1"),)))
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e1"), curve("e2"))))
    run(bus, CreateDimension(refs=(curve("e2"),), placement=pt(80, 30), value=24.0))
    run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(50, -10)))
    return bus


def test_the_parametric_model_survives_save_and_reopen(tmp_path: Path) -> None:
    bus = constrained()
    path = tmp_path / "part.caliper"
    snapshot.save(bus.document, path)
    reopened = Bus(snapshot.load(path), kernel=None)
    assert reopened.document == bus.document  # ids, references, values, construction
    assert reopened.queries.solve_status() == bus.queries.solve_status()
    # It is still parametric after reopening: the diameter drives the circle.
    run(reopened, ModifyEntity(id=E("e5"), changes={"value": 30.0}))
    circle = reopened.document.entities[E("e2")]
    assert circle.radius == pytest.approx(15.0)
    data = json.loads(path.read_text())
    assert data["document"]["entities"]["e3"] == {
        "kind": "constraint",
        "refs": [{"entity": "e1", "feature": "curve"}],
        "type": "horizontal",
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {
                "kind": "constraint",
                "type": "parallel",
                "refs": [{"entity": "e1", "feature": "curve"}],
            },
            "parallel needs two lines",
        ),
        (
            {
                "kind": "constraint",
                "type": "horizontal",
                "refs": [{"entity": "e9", "feature": "curve"}],
            },
            "no entity 'e9'",
        ),
        (
            {"kind": "constraint", "type": "sideways", "refs": []},
            "type must be one of",
        ),
    ],
)
def test_a_file_with_a_constraint_that_cannot_exist_is_refused(
    change: dict[str, object], message: str
) -> None:
    data = json.loads(snapshot.dumps(constrained().document))
    data["document"]["entities"]["e3"] = change
    with pytest.raises(LoadError, match=message):
        snapshot.loads(json.dumps(data))


# --- Transactions ---------------------------------------------------------------------------------


def test_constraints_in_a_transaction_undo_as_one_step_and_roll_back() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 3)))
    before = bus.document
    with bus.transaction("Level and Size"):
        run(bus, CreateConstraint(type=C.HORIZONTAL, refs=(curve("e1"),)))
        run(bus, CreateDimension(refs=(curve("e1"),), placement=pt(5, -5), value=20.0))
    assert bus.undo_label == "Level and Size"
    after = bus.document
    bus.undo()
    assert bus.document == before
    bus.redo()
    assert bus.document == after
    with bus.transaction("Try") as attempt:
        run(bus, ModifyEntity(id=E("e3"), changes={"value": 40.0}))
        attempt.rollback()
    assert bus.document == after
    line = bus.document.entities[E("e1")]
    assert isinstance(line, Line)
