"""An arc through three points (AI-9), and an outline of joined segments (AI-10)."""

import json
import math

import pytest

from caliper.ai.construct import arc_through
from caliper.ai.model import ToolCall
from caliper.ai.tools import CONVENTIONS, TOOLS, Workspace
from caliper.contracts.document import Arc, Document, EntityId, Line, Point2
from caliper.engine.io.canonical import JSON


def call(w: Workspace, name: str, **arguments: object) -> dict[str, JSON]:
    outcome = w.call(ToolCall(id="c", name=name, arguments=arguments))
    assert not outcome.is_error, outcome.content
    assert isinstance(outcome.content, dict)
    return outcome.content


def refused(w: Workspace, name: str, **arguments: object) -> str:
    before = w.document
    outcome = w.call(ToolCall(id="c", name=name, arguments=arguments))
    assert outcome.is_error
    assert w.document == before
    return json.dumps(outcome.content)


def pt(x: float, y: float) -> dict[str, float]:
    return {"x": x, "y": y}


def ends(arc: Arc) -> tuple[Point2, Point2]:
    def at(degrees: float) -> Point2:
        t = math.radians(degrees)
        return Point2(
            x=arc.center.x + arc.radius * math.cos(t), y=arc.center.y + arc.radius * math.sin(t)
        )

    return at(arc.start_angle), at(arc.start_angle + arc.sweep_angle)


def near(p: Point2, x: float, y: float) -> bool:
    return math.isclose(p.x, x, abs_tol=1e-9) and math.isclose(p.y, y, abs_tol=1e-9)


def dof(w: Workspace) -> int:
    status = call(w, "solve_status")
    assert status["conflicting"] == []
    assert status["redundant"] == []
    return int(status["dof"])  # type: ignore[arg-type]


# --- An arc through three points ----------------------------------------------------------


def test_both_tools_are_offered_and_the_conventions_point_to_them() -> None:
    names = {t.name for t in TOOLS}
    for name in ("create_arc_through_points", "create_outline"):
        assert name in names
        assert name in CONVENTIONS


def test_the_d_cutout_s_arc_is_worked_out_from_its_three_points() -> None:
    # Both stress-plate runs worked this centre out by hand (AI-9).
    arc = arc_through(Point2(x=50, y=140), Point2(x=35, y=150), Point2(x=20, y=140))
    assert arc is not None
    assert not arc.reversed
    assert near(arc.center, 35, 133.75)
    assert arc.radius == pytest.approx(16.25)
    assert arc.start_angle == pytest.approx(math.degrees(math.atan2(6.25, 15)))
    assert arc.sweep_angle == pytest.approx(180 - 2 * math.degrees(math.atan2(6.25, 15)))


def test_an_arc_clockwise_from_start_to_end_is_stored_the_other_way_round() -> None:
    arc = arc_through(Point2(x=20, y=140), Point2(x=35, y=150), Point2(x=50, y=140))
    assert arc is not None
    assert arc.reversed
    stored = Arc(
        center=arc.center,
        radius=arc.radius,
        start_angle=arc.start_angle,
        sweep_angle=arc.sweep_angle,
    )
    first, last = ends(stored)
    assert near(first, 50, 140)  # its start is the end given
    assert near(last, 20, 140)


@pytest.mark.parametrize(
    "points",
    [
        ((0, 0), (1, 1), (2, 2)),  # on one line
        ((0, 0), (0, 0), (2, 5)),  # two the same
    ],
    ids=["collinear", "repeated"],
)
def test_no_arc_through_points_on_a_line_or_repeated(points) -> None:
    assert arc_through(*(Point2(x=x, y=y) for x, y in points)) is None


def test_the_tool_makes_one_arc_and_says_which_end_is_which() -> None:
    w = Workspace(Document.empty())
    made = call(
        w,
        "create_arc_through_points",
        start=pt(20, 140),
        through=pt(35, 150),
        end=pt(50, 140),
    )
    (id,) = made["created"]  # type: ignore[misc]
    arc = w.document.entities[EntityId(str(id))]
    assert isinstance(arc, Arc)
    first, last = ends(arc)
    assert near(first, 50, 140)
    assert near(last, 20, 140)
    assert "its start is the end you gave" in str(made["note"])
    assert call(w, "undo")["undone"] == "Create Arc"
    assert w.document.entities == {}


def test_three_points_on_a_line_are_refused() -> None:
    w = Workspace(Document.empty())
    assert "no arc goes through" in refused(
        w, "create_arc_through_points", start=pt(0, 0), through=pt(5, 5), end=pt(9, 9)
    )


# --- An outline ----------------------------------------------------------------------------


def test_a_closed_outline_of_lines_is_one_call_joined_all_round() -> None:
    w = Workspace(Document.empty())
    made = call(
        w, "create_outline", points=[pt(0, 0), pt(40, 0), pt(40, 20), pt(0, 20)], closed=True
    )
    lines = [w.document.entities[EntityId(str(id))] for id in made["created"]]  # type: ignore[union-attr]
    assert all(isinstance(line, Line) for line in lines)
    assert len(lines) == 4
    assert len(made["constraints"]) == 4  # type: ignore[arg-type]  # four joints
    assert dof(w) == 4 * 4 - 4 * 2
    for a, b in zip(lines, [*lines[1:], lines[0]], strict=True):
        assert isinstance(a, Line)
        assert isinstance(b, Line)
        assert a.end == b.start
    assert call(w, "undo")["undone"] == "Create Outline"  # all of it at once
    assert w.document.entities == {}


def test_a_slot_is_one_call_with_its_arcs_tangent() -> None:
    w = Workspace(Document.empty())
    made = call(
        w,
        "create_outline",
        points=[
            {"x": 0, "y": 0, "tangent": True},
            {"x": 40, "y": 0, "tangent": True},
            {"x": 40, "y": 10, "through": pt(45, 5), "tangent": True},
            {"x": 0, "y": 10, "tangent": True},
        ],
        closed=True,
        close_through=pt(-5, 5),
    )
    kinds = [type(w.document.entities[EntityId(str(id))]) for id in made["created"]]  # type: ignore[union-attr]
    assert kinds == [Line, Arc, Line, Arc]
    assert len(made["constraints"]) == 8  # type: ignore[arg-type]  # four joints, four tangencies
    # 2 lines and 2 arcs (18) less 4 joints (8) and 4 tangencies (4): placed (3), long,
    # wide, and the lines free to taper.
    assert dof(w) == 6
    call(
        w,
        "create_constraint",
        type="parallel",
        refs=[
            {"entity": str(made["created"][0]), "feature": "curve"},  # type: ignore[index]
            {"entity": str(made["created"][2]), "feature": "curve"},  # type: ignore[index]
        ],
    )
    assert dof(w) == 5


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"points": [pt(0, 0)]}, "at least two points"),
        ({"points": [pt(0, 0), pt(0, 0)]}, "starts and ends at the same point"),
        ({"points": [pt(0, 0), pt(5, 0)], "closed": True}, "at least three points"),
        ({"points": [pt(0, 0), pt(5, 0)], "close_through": pt(2, 2)}, "needs closed: true"),
        ({"points": [{"x": 0, "y": 0, "tangent": True}, pt(5, 0)]}, "where two segments meet"),
        ({"points": [pt(0, 0), {"x": 5, "y": 0, "tangent": True}, pt(5, 5)]}, "two lines"),
        ({"points": [pt(0, 0), {"x": 10, "y": 0, "through": pt(5, 0)}]}, "segment 0"),
        ({"points": [pt(0, 0), {"x": 1}]}, "points[1] needs finite x and y"),
    ],
    ids=[
        "one point",
        "no length",
        "closed two",
        "close_through open",
        "tangent at an end",
        "tangent lines",
        "flat arc",
        "no y",
    ],
)
def test_an_outline_that_cant_be_drawn_is_refused_whole(
    arguments: dict[str, object], expected: str
) -> None:
    w = Workspace(Document.empty())
    assert expected in refused(w, "create_outline", **arguments)
    assert w.commands == ()
