"""Every constraint type, solved for real.

For each type: the relationship holds after it is added, the geometry named last is what
moved (and nothing else did), degrees of freedom drop by what the type removes, the
relationship survives later edits, and undo/redo are exact.
"""

import math

import pytest

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateArc,
    CreateCircle,
    CreateConstraint,
    CreateLine,
    CreatePoint,
    CreateRectangle,
    ModifyEntity,
    MoveEntities,
    Rejected,
)
from caliper.contracts.document import (
    Arc,
    Circle,
    Constraint,
    ConstraintType,
    EntityId,
    Feature,
    Line,
    Point,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import ErrorCode
from caliper.engine.commands.bus import Bus

TOL = 1e-9


def pt(x: float, y: float) -> Point2:
    return Point2(x=x, y=y)


def ref(entity: str, feature: str) -> Ref:
    return Ref(entity=EntityId(entity), feature=Feature(feature))


def curve(entity: str) -> Ref:
    return ref(entity, "curve")


def run(bus: Bus, command: Command) -> Applied:
    result = bus.execute(command)
    assert isinstance(result, Applied), result
    return result


def rejected(result: CommandResult) -> Rejected:
    assert isinstance(result, Rejected), result
    return result


def constrain(bus: Bus, type_: ConstraintType, *refs: Ref) -> Applied:
    """Add a constraint, and check undo/redo return exactly to before and after."""
    before = bus.document
    result = run(bus, CreateConstraint(type=type_, refs=refs))
    after = bus.document
    bus.undo()
    assert bus.document == before
    bus.redo()
    assert bus.document == after
    return result


def at(bus: Bus, r: Ref) -> Point2:
    point = bus.queries.feature_point(r)
    assert isinstance(point, Point2)
    return point


def geometry(bus: Bus, id: str) -> object:
    return bus.document.entities[EntityId(id)]


def line(bus: Bus, id: str) -> Line:
    entity = geometry(bus, id)
    assert isinstance(entity, Line)
    return entity


def circle(bus: Bus, id: str) -> Circle | Arc:
    entity = geometry(bus, id)
    assert isinstance(entity, Circle | Arc)
    return entity


def dof(bus: Bus) -> int:
    return bus.queries.solve_status().dof


def direction(entity: Line) -> tuple[float, float]:
    dx, dy = entity.end.x - entity.start.x, entity.end.y - entity.start.y
    length = math.hypot(dx, dy)
    return dx / length, dy / length


def sine(a: Line, b: Line) -> float:
    (ax, ay), (bx, by) = direction(a), direction(b)
    return ax * by - ay * bx


def cosine(a: Line, b: Line) -> float:
    (ax, ay), (bx, by) = direction(a), direction(b)
    return ax * bx + ay * by


def offset(p: Point2, entity: Line) -> float:
    """Signed distance from `p` to the line's infinite extension."""
    dx, dy = entity.end.x - entity.start.x, entity.end.y - entity.start.y
    return (dx * (p.y - entity.start.y) - dy * (p.x - entity.start.x)) / math.hypot(dx, dy)


def length(entity: Line) -> float:
    return math.hypot(entity.end.x - entity.start.x, entity.end.y - entity.start.y)


# --- Coincident -----------------------------------------------------------------------------


def test_coincident_points_moves_the_second_point_onto_the_first() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreateLine(start=pt(12, 1), end=pt(20, 5)))
    assert dof(bus) == 8
    constrain(bus, ConstraintType.COINCIDENT, ref("e1", "end"), ref("e2", "start"))
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(10, 0))
    assert line(bus, "e2") == Line(start=pt(10, 0), end=pt(20, 5))
    assert dof(bus) == 6
    # Parametric: moving the first line drags the joined end along.
    run(bus, MoveEntities(ids=(EntityId("e1"),), dx=5, dy=-3))
    assert at(bus, ref("e2", "start")) == at(bus, ref("e1", "end")) == pt(15, -3)
    assert line(bus, "e2").end == pt(20, 5)


def test_coincident_point_on_a_line_uses_its_infinite_extension() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreatePoint(position=pt(25, 3)))
    constrain(bus, ConstraintType.COINCIDENT, ref("e2", "point"), curve("e1"))
    point = geometry(bus, "e2")
    assert isinstance(point, Point)
    assert point.position == pt(25, 0)
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(10, 0))
    assert dof(bus) == 4 + 2 - 1


def test_coincident_point_on_a_circle() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=10))
    run(bus, CreatePoint(position=pt(13, 0)))
    constrain(bus, ConstraintType.COINCIDENT, curve("e1"), ref("e2", "point"))
    assert geometry(bus, "e2") == Point(position=pt(10, 0))
    # Growing the circle keeps the point on it: the point follows the rim.
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"radius": 20.0}))
    p = geometry(bus, "e2")
    assert isinstance(p, Point)
    assert math.hypot(p.position.x, p.position.y) == pytest.approx(20.0, abs=TOL)


def test_coincident_lines_are_collinear() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreateLine(start=pt(12, 2), end=pt(20, 3)))
    constrain(bus, ConstraintType.COINCIDENT, curve("e1"), curve("e2"))
    assert line(bus, "e2") == Line(start=pt(12, 0), end=pt(20, 0))
    assert dof(bus) == 8 - 2


def test_coincident_circles_share_their_circle() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=10))
    run(bus, CreateArc(center=pt(1, 1), radius=12, start_angle=0, sweep_angle=90))
    constrain(bus, ConstraintType.COINCIDENT, curve("e1"), curve("e2"))
    arc = circle(bus, "e2")
    assert (arc.center, arc.radius) == (pt(0, 0), 10.0)
    assert dof(bus) == 3 + 5 - 3


# --- Concentric -----------------------------------------------------------------------------


def test_concentric_circles_move_the_second_centre() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=5))
    run(bus, CreateCircle(center=pt(3, 4), radius=2))
    constrain(bus, ConstraintType.CONCENTRIC, curve("e1"), curve("e2"))
    assert geometry(bus, "e2") == Circle(center=pt(0, 0), radius=2)
    assert geometry(bus, "e1") == Circle(center=pt(0, 0), radius=5)
    assert dof(bus) == 6 - 2
    # Parametric: moving one centre moves the other with it.
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"center": pt(7, -2)}))
    assert circle(bus, "e2").center == pt(7, -2)


def test_concentric_with_a_point_moves_the_circle_to_it() -> None:
    bus = Bus(kernel=None)
    run(bus, CreatePoint(position=pt(1, 1)))
    run(bus, CreateArc(center=pt(5, 5), radius=2, start_angle=0, sweep_angle=180))
    constrain(bus, ConstraintType.CONCENTRIC, ref("e1", "point"), curve("e2"))
    assert circle(bus, "e2").center == pt(1, 1)
    assert geometry(bus, "e1") == Point(position=pt(1, 1))


# --- Horizontal and vertical ---------------------------------------------------------------


def test_horizontal_line_levels_about_its_middle() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 4)))
    constrain(bus, ConstraintType.HORIZONTAL, curve("e1"))
    assert line(bus, "e1") == Line(start=pt(0, 2), end=pt(10, 2))
    assert dof(bus) == 3


def test_horizontal_line_keeps_a_fixed_end() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 4)))
    constrain(bus, ConstraintType.FIX, ref("e1", "start"))
    constrain(bus, ConstraintType.HORIZONTAL, curve("e1"))
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(10, 0))


def test_horizontal_points() -> None:
    bus = Bus(kernel=None)
    run(bus, CreatePoint(position=pt(0, 0)))
    run(bus, CreatePoint(position=pt(5, 3)))
    constrain(bus, ConstraintType.HORIZONTAL, ref("e1", "point"), ref("e2", "point"))
    assert geometry(bus, "e2") == Point(position=pt(5, 0))


def test_vertical_line_and_its_edits() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(4, 10)))
    constrain(bus, ConstraintType.VERTICAL, curve("e1"))
    assert line(bus, "e1") == Line(start=pt(2, 0), end=pt(2, 10))
    # Moving one end sideways takes the other with it.
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"end": pt(7, 12)}))
    assert line(bus, "e1") == Line(start=pt(7, 0), end=pt(7, 12))


def test_vertical_points() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=1))
    run(bus, CreateCircle(center=pt(3, 8), radius=1))
    constrain(bus, ConstraintType.VERTICAL, ref("e1", "center"), ref("e2", "center"))
    assert circle(bus, "e2").center == pt(0, 8)


@pytest.mark.parametrize("type_", [ConstraintType.HORIZONTAL, ConstraintType.VERTICAL])
def test_rectangle_sides_are_already_level(type_: ConstraintType) -> None:
    bus = Bus(kernel=None)
    run(bus, CreateRectangle(corner=pt(0, 0), width=10, height=5))
    error = rejected(bus.execute(CreateConstraint(type=type_, refs=(ref("e1", "bottom"),))))
    assert error.errors[0].code is ErrorCode.CONSTRAINT_NOT_APPLICABLE
    assert "always horizontal or vertical" in error.errors[0].message


# --- Parallel and perpendicular ------------------------------------------------------------


def test_parallel_turns_the_second_line_and_stays_parallel_after_edits() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreateLine(start=pt(0, 5), end=pt(10, 8)))
    constrain(bus, ConstraintType.PARALLEL, curve("e1"), curve("e2"))
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(10, 0))
    assert abs(sine(line(bus, "e1"), line(bus, "e2"))) <= TOL
    assert dof(bus) == 7
    # Changing the first line's angle turns the second to match; the first keeps its start.
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"end": pt(10, 10)}))
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(10, 10))
    assert abs(sine(line(bus, "e1"), line(bus, "e2"))) <= TOL


def test_parallel_to_a_rectangle_side() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateRectangle(corner=pt(0, 0), width=10, height=5))
    run(bus, CreateLine(start=pt(0, 10), end=pt(10, 12)))
    constrain(bus, ConstraintType.PARALLEL, ref("e1", "bottom"), curve("e2"))
    e2 = line(bus, "e2")
    assert e2.start.y == pytest.approx(e2.end.y, abs=TOL)
    assert geometry(bus, "e1") == Rectangle(corner=pt(0, 0), width=10, height=5)


def test_perpendicular() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreateLine(start=pt(5, 0), end=pt(7, 10)))
    constrain(bus, ConstraintType.PERPENDICULAR, curve("e1"), curve("e2"))
    assert abs(cosine(line(bus, "e1"), line(bus, "e2"))) <= TOL
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(10, 0))
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"end": pt(10, 6)}))
    assert abs(cosine(line(bus, "e1"), line(bus, "e2"))) <= TOL


# --- Tangent --------------------------------------------------------------------------------


def test_tangent_line_and_circle_keeps_the_side_the_circle_was_on() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(-10, 5), end=pt(10, 5)))
    run(bus, CreateCircle(center=pt(0, 0), radius=4))
    constrain(bus, ConstraintType.TANGENT, curve("e1"), curve("e2"))
    c = circle(bus, "e2")
    assert line(bus, "e1") == Line(start=pt(-10, 5), end=pt(10, 5))
    assert c.center.y < 5  # still below the line
    assert 5 - c.center.y == pytest.approx(c.radius, abs=TOL)
    assert dof(bus) == 7 - 1
    # Parametric: raising the line lifts the circle with it (or grows it) to stay tangent.
    run(bus, MoveEntities(ids=(EntityId("e1"),), dx=0, dy=3))
    c = circle(bus, "e2")
    assert 8 - c.center.y == pytest.approx(c.radius, abs=TOL)


def test_tangent_arc_and_line() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateArc(center=pt(0, 0), radius=5, start_angle=0, sweep_angle=180))
    run(bus, CreateLine(start=pt(-10, 7), end=pt(10, 6)))
    constrain(bus, ConstraintType.TANGENT, curve("e1"), curve("e2"))
    assert abs(offset(circle(bus, "e1").center, line(bus, "e2"))) == pytest.approx(5, abs=TOL)
    assert geometry(bus, "e1") == Arc(center=pt(0, 0), radius=5, start_angle=0, sweep_angle=180)


def test_tangent_circles_outside_each_other() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=5))
    run(bus, CreateCircle(center=pt(12, 0), radius=5))
    constrain(bus, ConstraintType.TANGENT, curve("e1"), curve("e2"))
    a, b = circle(bus, "e1"), circle(bus, "e2")
    assert math.hypot(b.center.x, b.center.y) == pytest.approx(a.radius + b.radius, abs=TOL)


def test_tangent_circles_one_inside_the_other() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=10))
    run(bus, CreateCircle(center=pt(3, 0), radius=5))
    constrain(bus, ConstraintType.TANGENT, curve("e1"), curve("e2"))
    a, b = circle(bus, "e1"), circle(bus, "e2")
    assert math.hypot(b.center.x, b.center.y) == pytest.approx(a.radius - b.radius, abs=TOL)


# --- Equal ----------------------------------------------------------------------------------


def test_equal_lines() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreateLine(start=pt(0, 5), end=pt(6, 5)))
    constrain(bus, ConstraintType.EQUAL, curve("e1"), curve("e2"))
    assert length(line(bus, "e2")) == pytest.approx(10, abs=TOL)
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(10, 0))
    # Parametric: lengthening one lengthens the other.
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"end": pt(30, 0)}))
    assert length(line(bus, "e2")) == pytest.approx(30, abs=1e-8)


def test_equal_rectangle_sides_make_a_square() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateRectangle(corner=pt(0, 0), width=10, height=4))
    constrain(bus, ConstraintType.EQUAL, ref("e1", "bottom"), ref("e1", "left"))
    square = geometry(bus, "e1")
    assert isinstance(square, Rectangle)
    assert square.width == pytest.approx(square.height, abs=TOL)


def test_equal_radii() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=5))
    run(bus, CreateArc(center=pt(20, 0), radius=3, start_angle=10, sweep_angle=100))
    constrain(bus, ConstraintType.EQUAL, curve("e1"), curve("e2"))
    assert circle(bus, "e2").radius == 5.0
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"radius": 8.0}))
    assert circle(bus, "e2").radius == pytest.approx(8.0, abs=TOL)


# --- Midpoint -------------------------------------------------------------------------------


def test_midpoint_of_a_line_moves_the_point() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreatePoint(position=pt(4, 2)))
    constrain(bus, ConstraintType.MIDPOINT, ref("e2", "point"), curve("e1"))
    assert geometry(bus, "e2") == Point(position=pt(5, 0))
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(10, 0))
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"end": pt(20, 10)}))
    assert geometry(bus, "e2") == Point(position=pt(10, 5))


def test_midpoint_of_an_arc() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateArc(center=pt(0, 0), radius=10, start_angle=0, sweep_angle=90))
    run(bus, CreatePoint(position=pt(6, 6)))
    constrain(bus, ConstraintType.MIDPOINT, curve("e1"), ref("e2", "point"))
    assert at(bus, ref("e2", "point")) == at(bus, ref("e1", "mid"))


def test_midpoint_of_a_rectangle_side() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateRectangle(corner=pt(0, 0), width=10, height=4))
    run(bus, CreatePoint(position=pt(4, 6)))
    constrain(bus, ConstraintType.MIDPOINT, ref("e1", "top"), ref("e2", "point"))
    assert geometry(bus, "e2") == Point(position=pt(5, 4))


# --- Symmetric ------------------------------------------------------------------------------


def test_symmetric_points_mirror_the_second_about_the_line() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, -10), end=pt(0, 10)))
    run(bus, CreatePoint(position=pt(-3, 1)))
    run(bus, CreatePoint(position=pt(4, 2)))
    constrain(bus, ConstraintType.SYMMETRIC, curve("e1"), ref("e2", "point"), ref("e3", "point"))
    assert geometry(bus, "e3") == Point(position=pt(3, 1))
    assert geometry(bus, "e2") == Point(position=pt(-3, 1))
    # Parametric: moving the first point moves its mirror image.
    run(bus, ModifyEntity(id=EntityId("e2"), changes={"position": pt(-7, 4)}))
    assert geometry(bus, "e3") == Point(position=pt(7, 4))


def test_symmetric_circles() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, -10), end=pt(0, 10)))
    run(bus, CreateCircle(center=pt(-5, 0), radius=2))
    run(bus, CreateCircle(center=pt(4, 1), radius=3))
    constrain(bus, ConstraintType.SYMMETRIC, curve("e2"), curve("e3"), curve("e1"))
    assert geometry(bus, "e3") == Circle(center=pt(5, 0), radius=2)


# --- Fix ------------------------------------------------------------------------------------


def test_fixed_point_blocks_moves_and_edits_that_would_move_it() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    constrain(bus, ConstraintType.FIX, ref("e1", "start"))
    assert bus.queries.solve_status().entity_dof[EntityId("e1")] == 2
    move = rejected(bus.execute(MoveEntities(ids=(EntityId("e1"),), dx=1, dy=0)))
    assert move.errors[0].code is ErrorCode.CONSTRAINT_CONFLICT
    assert move.errors[0].ids == ("e1", "e2")
    edit = rejected(bus.execute(ModifyEntity(id=EntityId("e1"), changes={"start": pt(1, 1)})))
    assert edit.errors[0].ids == ("e1", "e2")
    # The free end still moves.
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"end": pt(3, 4)}))
    assert line(bus, "e1") == Line(start=pt(0, 0), end=pt(3, 4))


def test_fixed_circle_and_rectangle_side() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=5))
    run(bus, CreateRectangle(corner=pt(10, 0), width=10, height=5))
    constrain(bus, ConstraintType.FIX, curve("e1"))
    constrain(bus, ConstraintType.FIX, ref("e2", "bottom"))
    status = bus.queries.solve_status()
    assert status.entity_dof == {"e1": 0, "e2": 1}  # the rectangle's height is still free
    assert isinstance(
        bus.execute(ModifyEntity(id=EntityId("e1"), changes={"radius": 6.0})), Rejected
    )
    run(bus, ModifyEntity(id=EntityId("e2"), changes={"height": 9.0}))
    assert isinstance(
        bus.execute(ModifyEntity(id=EntityId("e2"), changes={"width": 9.0})), Rejected
    )


def test_fixing_twice_adds_nothing() -> None:
    bus = Bus(kernel=None)
    run(bus, CreatePoint(position=pt(1, 2)))
    constrain(bus, ConstraintType.FIX, ref("e1", "point"))
    again = rejected(
        bus.execute(CreateConstraint(type=ConstraintType.FIX, refs=(ref("e1", "point"),)))
    )
    assert again.errors[0].code is ErrorCode.CONSTRAINT_REDUNDANT
    assert again.errors[0].ids == ("e2",)


# --- Normal ---------------------------------------------------------------------------------


def test_normal_line_passes_through_the_centre() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateCircle(center=pt(0, 0), radius=3))
    run(bus, CreateLine(start=pt(0, 5), end=pt(10, 5)))
    constrain(bus, ConstraintType.NORMAL, curve("e1"), curve("e2"))
    assert offset(pt(0, 0), line(bus, "e2")) == pytest.approx(0, abs=TOL)
    assert geometry(bus, "e1") == Circle(center=pt(0, 0), radius=3)
    # Parametric: moving the circle drags the line through its new centre.
    run(bus, ModifyEntity(id=EntityId("e1"), changes={"center": pt(2, 7)}))
    assert offset(pt(2, 7), line(bus, "e2")) == pytest.approx(0, abs=TOL)


# --- Pierce ---------------------------------------------------------------------------------


def test_pierce_is_reported_as_unsupported_not_faked() -> None:
    bus = Bus(kernel=None)
    run(bus, CreatePoint(position=pt(0, 0)))
    run(bus, CreateLine(start=pt(0, 0), end=pt(1, 1)))
    before = bus.document
    error = rejected(
        bus.execute(
            CreateConstraint(type=ConstraintType.PIERCE, refs=(ref("e1", "point"), curve("e2")))
        )
    ).errors[0]
    assert error.code is ErrorCode.CONSTRAINT_UNSUPPORTED
    assert "3D curves" in error.message
    assert bus.document == before


# --- Curvature ------------------------------------------------------------------------------


def test_curvature_between_lines_makes_them_collinear_and_joined() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreateLine(start=pt(10.5, 0.5), end=pt(20, 3)))
    constrain(bus, ConstraintType.CURVATURE, curve("e1"), curve("e2"))
    e2 = line(bus, "e2")
    assert e2.start == pt(10, 0)
    assert e2.end.y == pytest.approx(0, abs=TOL)
    assert dof(bus) == 8 - 3


def test_curvature_between_arcs_puts_them_on_one_circle() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateArc(center=pt(0, 0), radius=10, start_angle=0, sweep_angle=90))
    run(bus, CreateArc(center=pt(0.5, 0.2), radius=9, start_angle=100, sweep_angle=60))
    constrain(bus, ConstraintType.CURVATURE, curve("e1"), curve("e2"))
    arc = circle(bus, "e2")
    assert isinstance(arc, Arc)
    assert arc.center.x == pytest.approx(0, abs=TOL)
    assert arc.center.y == pytest.approx(0, abs=TOL)
    assert arc.radius == pytest.approx(10, abs=TOL)
    assert arc.start_angle == pytest.approx(90, abs=TOL)  # joined where the first one ends
    assert dof(bus) == 10 - 4


def test_curvature_between_a_line_and_an_arc_is_refused_with_the_reason() -> None:
    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    run(bus, CreateArc(center=pt(10, 5), radius=5, start_angle=270, sweep_angle=90))
    error = rejected(
        bus.execute(
            CreateConstraint(type=ConstraintType.CURVATURE, refs=(curve("e1"), curve("e2")))
        )
    ).errors[0]
    assert error.code is ErrorCode.CONSTRAINT_NOT_APPLICABLE
    assert "curvature is 0" in error.message


# --- Across types ---------------------------------------------------------------------------


def test_the_stored_constraint_uses_the_canonical_order() -> None:
    bus = Bus(kernel=None)
    run(bus, CreatePoint(position=pt(4, 2)))
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 0)))
    result = constrain(bus, ConstraintType.MIDPOINT, ref("e1", "point"), curve("e2"))
    stored = Constraint(type=ConstraintType.MIDPOINT, refs=(curve("e2"), ref("e1", "point")))
    assert bus.document.entities[EntityId("e3")] == stored
    assert result.command == CreateConstraint(
        type=ConstraintType.MIDPOINT, refs=stored.refs, id=EntityId("e3")
    )
    assert result.label == "Add Midpoint Constraint"


def test_constraints_follow_their_geometry_out_when_it_is_deleted() -> None:
    from caliper.contracts.commands import DeleteEntities

    bus = Bus(kernel=None)
    run(bus, CreateLine(start=pt(0, 0), end=pt(10, 1)))
    run(bus, CreateLine(start=pt(0, 5), end=pt(10, 5)))
    constrain(bus, ConstraintType.PARALLEL, curve("e1"), curve("e2"))
    constrain(bus, ConstraintType.HORIZONTAL, curve("e2"))
    result = run(bus, DeleteEntities(ids=(EntityId("e2"),)))
    assert result.delta.removed == {"e2", "e3", "e4"}
    assert set(bus.document.entities) == {"e1"}
    bus.undo()
    assert set(bus.document.entities) == {"e1", "e2", "e3", "e4"}
