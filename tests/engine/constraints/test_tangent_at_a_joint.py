"""Tangency where an arc meets a line or another arc end to end: a fillet, a slot, an S-bend.

At such a joint the arc's end is coincident with the other curve, and "the centre is `r`
from the line" has the same gradient there as that coincidence, so a rank test called the
tangency redundant although it removes a degree of freedom (the arc's end angle). The
solver writes it at the joint instead: the radius to the shared point is perpendicular to
the line, or lies on one line with the other arc's radius.
"""

import math

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateArc,
    CreateConstraint,
    CreateLine,
    FilletCorner,
    ModifyEntity,
    Rejected,
)
from caliper.contracts.document import Arc, ConstraintType, EntityId, Feature, Line, Point2, Ref
from caliper.contracts.errors import ErrorCode
from caliper.engine.commands.bus import Bus

E = EntityId
C = ConstraintType


def ref(entity: str, feature: str) -> Ref:
    return Ref(entity=E(entity), feature=Feature(feature))


def curve(entity: str) -> Ref:
    return ref(entity, "curve")


def run(bus: Bus, command: Command) -> Applied:
    result = bus.execute(command)
    assert isinstance(result, Applied), result
    return result


def filleted_corner() -> Bus:
    """e1 up the right side, e2 left along the top, rounded R25 at their corner by e3, the
    arc's ends joined to the lines (e4, e5) and the lines kept square (e6, e7)."""
    bus = Bus()
    run(bus, CreateLine(start=Point2(x=200, y=0), end=Point2(x=200, y=125)))
    run(bus, CreateLine(start=Point2(x=200, y=125), end=Point2(x=0, y=125)))
    run(bus, FilletCorner(a=E("e1"), b=E("e2"), radius=25))
    run(bus, CreateConstraint(type=C.COINCIDENT, refs=(ref("e3", "start"), ref("e1", "end"))))
    run(bus, CreateConstraint(type=C.COINCIDENT, refs=(ref("e3", "end"), ref("e2", "start"))))
    run(bus, CreateConstraint(type=C.VERTICAL, refs=(curve("e1"),)))
    run(bus, CreateConstraint(type=C.HORIZONTAL, refs=(curve("e2"),)))
    return bus


def offset_from(line: Line, point: Point2) -> float:
    dx, dy = line.end.x - line.start.x, line.end.y - line.start.y
    return abs(dx * (point.y - line.start.y) - dy * (point.x - line.start.x)) / math.hypot(dx, dy)


def test_tangency_at_a_fillet_joint_is_accepted_and_removes_a_degree_of_freedom() -> None:
    bus = filleted_corner()
    before = bus.queries.solve_status().dof
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e3"), curve("e1"))))
    assert bus.queries.solve_status().dof == before - 1
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e3"), curve("e2"))))
    assert bus.queries.solve_status().dof == before - 2
    assert bus.queries.solve_status().redundant == ()


def test_the_rounded_corner_stays_tangent_when_the_lines_move() -> None:
    bus = filleted_corner()
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e3"), curve("e1"))))
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e3"), curve("e2"))))
    run(bus, ModifyEntity(id=E("e2"), changes={"start": Point2(x=210, y=140)}))
    doc = bus.document
    arc, right, top = doc.entities[E("e3")], doc.entities[E("e1")], doc.entities[E("e2")]
    assert isinstance(arc, Arc)
    assert isinstance(right, Line)
    assert isinstance(top, Line)
    assert math.isclose(offset_from(right, arc.center), arc.radius, abs_tol=1e-6)
    assert math.isclose(offset_from(top, arc.center), arc.radius, abs_tol=1e-6)
    # The joints stayed joined: each line still ends where the arc does.
    start = bus.queries.feature_point(ref("e3", "start"))
    end = bus.queries.feature_point(ref("e3", "end"))
    assert isinstance(start, Point2)
    assert isinstance(end, Point2)
    assert math.isclose(start.x, right.end.x, abs_tol=1e-6)
    assert math.isclose(start.y, right.end.y, abs_tol=1e-6)
    assert math.isclose(end.x, top.start.x, abs_tol=1e-6)
    assert math.isclose(end.y, top.start.y, abs_tol=1e-6)


def test_tangency_added_before_the_joint_is_accepted_too() -> None:
    bus = Bus()
    run(bus, CreateLine(start=Point2(x=200, y=0), end=Point2(x=200, y=125)))
    run(bus, CreateLine(start=Point2(x=200, y=125), end=Point2(x=0, y=125)))
    run(bus, FilletCorner(a=E("e1"), b=E("e2"), radius=25))
    before = bus.queries.solve_status().dof
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e3"), curve("e1"))))
    run(bus, CreateConstraint(type=C.COINCIDENT, refs=(ref("e3", "start"), ref("e1", "end"))))
    assert bus.queries.solve_status().dof == before - 3


def test_a_joint_on_the_line_itself_counts_too() -> None:
    # The arc's end lies on the line (point on curve) rather than at its endpoint.
    bus = Bus()
    run(bus, CreateLine(start=Point2(x=200, y=0), end=Point2(x=200, y=200)))
    run(bus, CreateArc(center=Point2(x=175, y=100), radius=25, start_angle=0, sweep_angle=90))
    run(bus, CreateConstraint(type=C.COINCIDENT, refs=(curve("e1"), ref("e2", "start"))))
    before = bus.queries.solve_status().dof
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e2"), curve("e1"))))
    assert bus.queries.solve_status().dof == before - 1


def test_a_tangency_that_really_is_implied_is_still_rejected() -> None:
    bus = filleted_corner()
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e3"), curve("e1"))))
    # The same tangency again adds nothing.
    again = bus.execute(CreateConstraint(type=C.TANGENT, refs=(curve("e1"), curve("e3"))))
    assert isinstance(again, Rejected)
    assert again.errors[0].code is ErrorCode.CONSTRAINT_REDUNDANT
    # Neither does tangency the joint already forces: on the horizontal top line, the centre
    # straight below the joint is exactly "the radius there is perpendicular to the line".
    run(bus, CreateConstraint(type=C.VERTICAL, refs=(ref("e3", "center"), ref("e2", "start"))))
    implied = bus.execute(CreateConstraint(type=C.TANGENT, refs=(curve("e3"), curve("e2"))))
    assert isinstance(implied, Rejected)
    assert implied.errors[0].code is ErrorCode.CONSTRAINT_REDUNDANT


def test_a_free_tangency_away_from_any_joint_is_unchanged() -> None:
    # A circle touching a line anywhere: the distance form, as before.
    bus = Bus()
    run(bus, CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=0)))
    run(bus, CreateArc(center=Point2(x=50, y=30), radius=20, start_angle=0, sweep_angle=180))
    before = bus.queries.solve_status().dof
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e1"), curve("e2"))))
    arc, line = bus.document.entities[E("e2")], bus.document.entities[E("e1")]
    assert isinstance(arc, Arc)
    assert isinstance(line, Line)
    assert math.isclose(offset_from(line, arc.center), arc.radius, abs_tol=1e-6)
    assert bus.queries.solve_status().dof == before - 1


def s_bend() -> Bus:
    """Two quarter arcs meeting end to end at (10, 10): e1 about the origin, e2 about (0, 20)."""
    bus = Bus()
    run(bus, CreateArc(center=Point2(x=0, y=0), radius=10, start_angle=0, sweep_angle=90))
    run(bus, CreateArc(center=Point2(x=0, y=20), radius=10, start_angle=270, sweep_angle=90))
    run(bus, CreateConstraint(type=C.COINCIDENT, refs=(ref("e1", "end"), ref("e2", "start"))))
    return bus


def test_tangency_where_two_arcs_meet_is_accepted_and_holds() -> None:
    bus = s_bend()
    before = bus.queries.solve_status().dof
    run(bus, CreateConstraint(type=C.TANGENT, refs=(curve("e1"), curve("e2"))))
    assert bus.queries.solve_status().dof == before - 1
    run(bus, ModifyEntity(id=E("e2"), changes={"radius": 15.0}))
    a, b = bus.document.entities[E("e1")], bus.document.entities[E("e2")]
    assert isinstance(a, Arc)
    assert isinstance(b, Arc)
    gap = math.hypot(b.center.x - a.center.x, b.center.y - a.center.y)
    assert math.isclose(gap, a.radius + b.radius, abs_tol=1e-6)  # still touching, outside
    again = bus.execute(CreateConstraint(type=C.TANGENT, refs=(curve("e2"), curve("e1"))))
    assert isinstance(again, Rejected)
    assert again.errors[0].code is ErrorCode.CONSTRAINT_REDUNDANT
