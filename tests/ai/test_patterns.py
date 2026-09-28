"""Mirror and linear pattern: one tool call, made of Caliper's own commands, undone as one."""

import itertools
import json
import math

import pytest

from caliper.ai import patterns
from caliper.ai.model import ToolCall
from caliper.ai.tools import CONVENTIONS, TOOLS, Workspace
from caliper.contracts.commands import CreateCircle, CreateLine
from caliper.contracts.document import (
    Arc,
    Circle,
    Document,
    EntityId,
    Line,
    Point,
    Point2,
    Rectangle,
)
from caliper.engine.commands.bus import Bus
from caliper.engine.io import codec, script, snapshot
from caliper.engine.io.canonical import JSON


def call(workspace: Workspace, name: str, **arguments: object) -> JSON:
    outcome = workspace.call(ToolCall(id="c", name=name, arguments=arguments))
    assert not outcome.is_error, outcome.content
    return outcome.content


def refused(workspace: Workspace, name: str, **arguments: object) -> str:
    before = workspace.document
    outcome = workspace.call(ToolCall(id="c", name=name, arguments=arguments))
    assert outcome.is_error
    assert workspace.document == before  # nothing left of it
    return json.dumps(outcome.content)


def ref(entity: str, feature: str) -> dict[str, str]:
    return {"entity": entity, "feature": feature}


def created(content: JSON) -> str:
    assert isinstance(content, dict)
    ids = content["created"]
    assert isinstance(ids, list)
    return str(ids[0])


def dof(workspace: Workspace) -> int:
    status = call(workspace, "solve_status")
    assert isinstance(status, dict)
    assert status["conflicting"] == []
    assert status["redundant"] == []
    return int(status["dof"])  # type: ignore[arg-type]


def entity(workspace: Workspace, id: object) -> object:
    return workspace.document.entities[EntityId(str(id))]


def close(p: Point2, x: float, y: float) -> bool:
    return math.isclose(p.x, x, abs_tol=1e-9) and math.isclose(p.y, y, abs_tol=1e-9)


def laid_out() -> Workspace:
    """A fixed origin (e1) and a vertical construction centreline at x = 120 (e3), fully
    constrained: the layout the 002 plate's symmetric features hang on."""
    w = Workspace(Document.empty())
    call(w, "create_point", position={"x": 0, "y": 0})
    call(w, "create_constraint", type="fix", refs=[ref("e1", "point")])
    call(
        w,
        "create_line",
        start={"x": 120, "y": 0},
        end={"x": 120, "y": 160},
        construction=True,
    )
    call(w, "create_constraint", type="vertical", refs=[ref("e3", "curve")])
    call(w, "create_constraint", type="horizontal", refs=[ref("e1", "point"), ref("e3", "start")])
    for a, b, orientation, value in (
        (ref("e1", "point"), ref("e3", "start"), "horizontal", 120),
        (ref("e3", "start"), ref("e3", "end"), "aligned", 160),
    ):
        call(
            w,
            "create_distance_dimension",
            a=a,
            b=b,
            orientation=orientation,
            offset=5,
            value=value,
        )
    assert dof(w) == 0
    return w


def slot(w: Workspace) -> tuple[str, str, str, str]:
    """The 002 plate's left slot, fully constrained: a 40 by 10 rectangle centred at (60, 120)
    with semicircle ends, their centres held vertical with the corners (C-2's workaround).
    Returns the rectangle, the two arcs, and the centre-to-centre dimension."""
    r = created(call(w, "create_rectangle", corner={"x": 40, "y": 115}, width=40, height=10))
    left = created(
        call(w, "create_arc", center={"x": 40, "y": 120}, radius=5, start_angle=90, sweep_angle=180)
    )
    right = created(
        call(
            w, "create_arc", center={"x": 80, "y": 120}, radius=5, start_angle=270, sweep_angle=180
        )
    )
    for arc, start, end in (
        (left, "top_left", "bottom_left"),
        (right, "bottom_right", "top_right"),
    ):
        call(w, "create_constraint", type="coincident", refs=[ref(arc, "start"), ref(r, start)])
        call(w, "create_constraint", type="coincident", refs=[ref(arc, "end"), ref(r, end)])
    call(w, "create_constraint", type="vertical", refs=[ref(left, "center"), ref(r, "top_left")])
    call(w, "create_constraint", type="vertical", refs=[ref(right, "center"), ref(r, "top_right")])
    dimensions = [
        created(
            call(
                w,
                "create_distance_dimension",
                a=a,
                b=b,
                orientation=orientation,
                offset=8,
                value=value,
            )
        )
        for a, b, orientation, value in (
            (ref(left, "center"), ref(right, "center"), "horizontal", 40),
            (ref(r, "bottom_left"), ref(r, "top_left"), "vertical", 10),
            (ref("e1", "point"), ref(left, "center"), "horizontal", 40),
            (ref("e1", "point"), ref(left, "center"), "vertical", 120),
        )
    ]
    assert dof(w) == 0
    return r, left, right, dimensions[0]


# --- The tools --------------------------------------------------------------------------


def test_both_tools_are_offered_to_every_model_and_the_conventions_point_to_them() -> None:
    names = [t.name for t in TOOLS]
    assert "mirror_entities" in names
    assert "linear_pattern" in names
    for spec in (patterns.MIRROR, patterns.PATTERN):
        json.dumps(spec.input_schema)
    assert "mirror_entities" in CONVENTIONS
    assert "linear_pattern" in CONVENTIONS


# --- Mirror -----------------------------------------------------------------------------


def test_a_mirrored_slot_is_fully_constrained_and_follows_its_original() -> None:
    w = laid_out()
    r, left, right, length = slot(w)
    made = call(w, "mirror_entities", ids=[r, left, right], axis="e3")
    assert isinstance(made, dict)
    copies = made["copies"]
    assert isinstance(copies, dict)
    assert dof(w) == 0  # the copies need nothing of their own
    assert entity(w, copies[r]) == Rectangle(corner=Point2(x=160, y=115), width=40, height=10)
    # A mirror reverses an arc: the left end's copy is the right slot's right end.
    assert entity(w, copies[left]) == Arc(
        center=Point2(x=200, y=120), radius=5, start_angle=270, sweep_angle=180
    )
    assert entity(w, copies[right]) == Arc(
        center=Point2(x=160, y=120), radius=5, start_angle=90, sweep_angle=180
    )
    # Lengthen the original slot: the copy lengthens too, mirrored about the same line.
    call(w, "modify_entity", id=length, changes={"value": 50})
    copy = entity(w, copies[r])
    assert isinstance(copy, Rectangle)
    assert copy.width == pytest.approx(50)
    assert copy.corner.x == pytest.approx(240 - 40 - 50)


def test_a_mirrored_half_star_meets_itself_on_the_line_and_adds_no_freedom() -> None:
    w = laid_out()
    points = [
        (
            120 + (25 if k % 2 == 0 else 12) * math.cos(math.radians(90 + 15 * k)),
            80 + (25 if k % 2 == 0 else 12) * math.sin(math.radians(90 + 15 * k)),
        )
        for k in range(13)
    ]
    lines = [
        created(
            call(
                w,
                "create_line",
                start={"x": points[k][0], "y": points[k][1]},
                end={"x": points[k + 1][0], "y": points[k + 1][1]},
            )
        )
        for k in range(12)
    ]
    for a, b in itertools.pairwise(lines):
        call(w, "create_constraint", type="coincident", refs=[ref(a, "end"), ref(b, "start")])
    call(
        w, "create_constraint", type="coincident", refs=[ref(lines[0], "start"), ref("e3", "curve")]
    )
    call(
        w, "create_constraint", type="coincident", refs=[ref(lines[-1], "end"), ref("e3", "curve")]
    )
    free = dof(w)
    made = call(w, "mirror_entities", ids=[*lines, "e3"], axis="e3")
    assert isinstance(made, dict)
    assert made["skipped"] == {"e3": "the axis itself"}
    copies = made["copies"]
    assert isinstance(copies, dict)
    assert len(copies) == 12
    assert len(made["constraints"]) == 24  # type: ignore[arg-type]  # two ends each
    assert dof(w) == free
    top, first = entity(w, lines[0]), entity(w, copies[lines[0]])
    assert isinstance(top, Line)
    assert isinstance(first, Line)
    assert close(first.start, top.start.x, top.start.y)  # the halves meet at the top point
    assert close(first.end, 240 - top.end.x, top.end.y)


@pytest.mark.parametrize("axis_end", [(200, 80), (40, 200)], ids=["slanted", "steep"])
def test_every_kind_mirrors_about_a_slanted_line_and_follows_the_original(
    axis_end: tuple[float, float],
) -> None:
    w = Workspace(Document.empty())
    axis = created(
        call(w, "create_line", start={"x": 0, "y": 0}, end={"x": axis_end[0], "y": axis_end[1]})
    )
    call(w, "create_constraint", type="fix", refs=[ref(axis, "curve")])
    ids = [
        created(call(w, "create_point", position={"x": 10, "y": 40})),
        created(call(w, "create_line", start={"x": 5, "y": 30}, end={"x": 25, "y": 60})),
        created(call(w, "create_circle", center={"x": 30, "y": 70}, radius=4)),
        created(
            call(
                w,
                "create_arc",
                center={"x": -20, "y": 50},
                radius=8,
                start_angle=20,
                sweep_angle=130,
            )
        ),
    ]
    for id in ids:
        call(
            w, "create_constraint", type="fix", refs=[ref(id, "curve" if id != ids[0] else "point")]
        )
    assert dof(w) == 0
    made = call(w, "mirror_entities", ids=ids, axis=axis)
    assert isinstance(made, dict)
    assert dof(w) == 0  # each copy fully held by its original and the line
    copies = made["copies"]
    assert isinstance(copies, dict)

    ux, uy = axis_end[0] / math.hypot(*axis_end), axis_end[1] / math.hypot(*axis_end)

    def mirrored(x: float, y: float) -> tuple[float, float]:
        along = x * ux + y * uy
        return 2 * along * ux - x, 2 * along * uy - y

    point = entity(w, copies[ids[0]])
    assert isinstance(point, Point)
    assert close(point.position, *mirrored(10, 40))
    line = entity(w, copies[ids[1]])
    assert isinstance(line, Line)
    assert close(line.start, *mirrored(5, 30))
    assert close(line.end, *mirrored(25, 60))
    circle = entity(w, copies[ids[2]])
    assert isinstance(circle, Circle)
    assert close(circle.center, *mirrored(30, 70))
    assert circle.radius == pytest.approx(4)
    arc, original = entity(w, copies[ids[3]]), entity(w, ids[3])
    assert isinstance(arc, Arc)
    assert isinstance(original, Arc)
    assert close(arc.center, *mirrored(-20, 50))
    assert arc.radius == pytest.approx(8)
    assert arc.sweep_angle == pytest.approx(130)
    start = math.radians(original.start_angle)
    end = math.radians(arc.start_angle + arc.sweep_angle)
    assert close(
        Point2(x=arc.center.x + 8 * math.cos(end), y=arc.center.y + 8 * math.sin(end)),
        *mirrored(-20 + 8 * math.cos(start), 50 + 8 * math.sin(start)),
    )


def test_a_mirrored_arc_is_held_whichever_way_its_chord_runs() -> None:
    # The D-cutout's arc: its chord is horizontal, so its centre is free up and down once its
    # ends are mirrored, and a level-with-x constraint would hold nothing.
    w = laid_out()
    arc = created(
        call(
            w,
            "create_arc",
            center={"x": 35, "y": 133.75},
            radius=16.25,
            start_angle=22.619865,
            sweep_angle=134.76027,
        )
    )
    call(w, "create_constraint", type="fix", refs=[ref(arc, "curve")])
    made = call(w, "mirror_entities", ids=[arc], axis="e3")
    assert isinstance(made, dict)
    assert dof(w) == 0
    copies = made["copies"]
    assert isinstance(copies, dict)
    copy = entity(w, copies[arc])
    assert isinstance(copy, Arc)
    assert close(copy.center, 205, 133.75)


def test_geometry_on_the_line_and_constraints_are_skipped() -> None:
    w = laid_out()
    on = created(call(w, "create_circle", center={"x": 120, "y": 50}, radius=5))
    off = created(call(w, "create_circle", center={"x": 100, "y": 50}, radius=5))
    made = call(w, "mirror_entities", ids=[on, off, "e2"], axis="e3")
    assert isinstance(made, dict)
    assert list(made["copies"]) == [off]  # type: ignore[arg-type]
    assert set(made["skipped"]) == {on, "e2"}  # type: ignore[arg-type]
    assert "nothing to mirror" in refused(w, "mirror_entities", ids=[on], axis="e3")


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"ids": ["e9"], "axis": "e3"}, "no entity 'e9'"),
        ({"ids": ["e1"], "axis": "e1"}, "axis must be a line; e1 is a point"),
        ({"ids": [], "axis": "e3"}, "ids must be a list"),
        ({"ids": ["e1"]}, "axis must be the id of a line"),
    ],
    ids=["no such entity", "axis not a line", "no ids", "no axis"],
)
def test_a_mirror_that_cant_be_made_is_refused(arguments: dict[str, object], expected: str) -> None:
    assert expected in refused(laid_out(), "mirror_entities", **arguments)


def test_a_rectangle_only_mirrors_about_a_level_or_plumb_line() -> None:
    w = Workspace(Document.empty())
    axis = created(call(w, "create_line", start={"x": 0, "y": 0}, end={"x": 10, "y": 10}))
    box = created(call(w, "create_rectangle", corner={"x": 20, "y": 0}, width=5, height=3))
    assert "always axis-aligned" in refused(w, "mirror_entities", ids=[box], axis=axis)
    assert len(w.commands) == 2  # only the two creates


# --- Linear pattern ---------------------------------------------------------------------


def grid() -> tuple[Workspace, str, JSON]:
    """The 002 plate's hole grid: Ø6 at (60, 25), 5 by 4 at 30 mm, from a fixed origin."""
    w = Workspace(Document.empty())
    call(w, "create_point", position={"x": 0, "y": 0})
    call(w, "create_constraint", type="fix", refs=[ref("e1", "point")])
    hole = created(call(w, "create_circle", center={"x": 60, "y": 25}, radius=3))
    call(
        w,
        "create_dimension",
        refs=[ref(hole, "curve")],
        placement={"x": 70, "y": 35},
        value=6,
        type="diameter",
    )
    for orientation, value in (("horizontal", 60), ("vertical", 25)):
        call(
            w,
            "create_distance_dimension",
            a=ref("e1", "point"),
            b=ref(hole, "center"),
            orientation=orientation,
            offset=5,
            value=value,
        )
    made = call(w, "linear_pattern", ids=[hole], count=5, spacing=30, count2=4, spacing2=30)
    return w, hole, made


def centres(w: Workspace) -> list[tuple[float, float]]:
    return sorted(
        (round(e.center.x, 9), round(e.center.y, 9))
        for e in w.document.entities.values()
        if isinstance(e, Circle)
    )


def test_a_hole_grid_is_one_call_fully_constrained_from_one_spacing_each_way() -> None:
    w, hole, made = grid()
    assert isinstance(made, dict)
    assert dof(w) == 0
    assert centres(w) == sorted((60.0 + 30 * i, 25.0 + 30 * j) for i in range(5) for j in range(4))
    copies = made["copies"]
    assert isinstance(copies, dict)
    assert len(copies[hole]) == 19  # type: ignore[arg-type]
    assert len(made["construction"]) == 19  # type: ignore[arg-type]  # one joint per copy
    assert len(made["dimensions"]) == 2  # type: ignore[arg-type]  # the two spacings
    # Only the copies and their joints are shown in full; the constraints by id.
    changed = made["changed"]
    assert isinstance(changed, dict)
    assert len(changed["added"]) == 38  # type: ignore[arg-type]


def test_one_dimension_respaces_the_grid_and_one_diameter_sizes_every_hole() -> None:
    w, _, made = grid()
    assert isinstance(made, dict)
    across, up = made["dimensions"]  # type: ignore[misc]
    call(w, "modify_entity", id=across, changes={"value": 25})
    call(w, "modify_entity", id=up, changes={"value": 20})
    assert centres(w) == sorted((60.0 + 25 * i, 25.0 + 20 * j) for i in range(5) for j in range(4))
    call(w, "modify_entity", id="e4", changes={"value": 8})  # the seed's diameter
    radii = [e.radius for e in w.document.entities.values() if isinstance(e, Circle)]
    assert radii == [pytest.approx(4.0)] * 20


def test_the_whole_pattern_is_one_undo_step() -> None:
    w, _, _ = grid()
    before = len(w.commands)
    undone = call(w, "undo")
    assert isinstance(undone, dict)
    assert undone["undone"] == "Linear Pattern"
    assert len(w.commands) == 6
    assert before > 100
    assert len(centres(w)) == 1


def test_a_pattern_replays_on_the_original_document_to_the_same_bytes() -> None:
    w, _, _ = grid()
    text = json.dumps(
        {"format": script.FORMAT, "schema_version": 1, "commands": codec.encode(w.commands)}
    )
    replayed = Bus(w.base)
    for command in script.loads(text):
        replayed.execute(command)
    assert snapshot.dumps(replayed.document) == snapshot.dumps(w.document)


def test_lines_and_rectangles_repeat_at_their_size_and_follow_it() -> None:
    w = Workspace(Document.empty())
    rib = created(call(w, "create_line", start={"x": 0, "y": 0}, end={"x": 3, "y": 12}))
    box = created(call(w, "create_rectangle", corner={"x": 10, "y": 0}, width=4, height=6))
    made = call(w, "linear_pattern", ids=[rib, box], count=3, spacing=15, angle=90)
    assert isinstance(made, dict)
    copies = made["copies"]
    assert isinstance(copies, dict)
    ribs = [entity(w, id) for id in copies[rib]]  # type: ignore[union-attr]
    assert ribs == [
        Line(start=Point2(x=0, y=15), end=Point2(x=3, y=27)),
        Line(start=Point2(x=0, y=30), end=Point2(x=3, y=42)),
    ]
    boxes = [entity(w, id) for id in copies[box]]  # type: ignore[union-attr]
    assert boxes[1] == Rectangle(corner=Point2(x=10, y=30), width=4, height=6)
    call(w, "modify_entity", id=box, changes={"width": 7})
    for id in copies[box]:  # type: ignore[union-attr]
        copy = entity(w, id)
        assert isinstance(copy, Rectangle)
        assert copy.width == pytest.approx(7)


def test_a_slanted_pattern_says_its_direction_is_free() -> None:
    w = Workspace(Document.empty())
    dot = created(call(w, "create_point", position={"x": 0, "y": 0}))
    call(w, "create_constraint", type="fix", refs=[ref(dot, "point")])
    made = call(w, "linear_pattern", ids=[dot], count=4, spacing=10, angle=30)
    assert isinstance(made, dict)
    assert "direction isn't held" in str(made["note"])
    assert dof(w) == 1  # the first joint can turn, and the row with it
    last = entity(w, made["copies"][dot][-1])  # type: ignore[index]
    assert isinstance(last, Point)
    assert close(last.position, 30 * math.cos(math.radians(30)), 30 * math.sin(math.radians(30)))


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"ids": ["e2"], "count": 3, "spacing": 5}, "can't be patterned"),
        ({"ids": ["e1"], "count": 1, "spacing": 5}, "make no copies"),
        ({"ids": ["e1"], "count": 3, "spacing": 0}, "spacing must be a distance"),
        ({"ids": ["e1"], "count": 2.5, "spacing": 5}, "count must be a whole number"),
        ({"ids": ["e1"], "spacing": 5}, "count must be a whole number"),
        (
            {"ids": ["e1"], "count": 2, "spacing": 5, "count2": 2, "spacing2": 5, "angle2": 180},
            "parallel",
        ),
        ({"ids": ["e1"], "count": 2, "spacing": 5, "count2": 2}, "spacing2 must be"),
        ({"ids": ["e1"], "count": 11, "spacing": 5, "count2": 11, "spacing2": 5}, "at most 100"),
        ({"ids": ["e9"], "count": 3, "spacing": 5}, "no entity 'e9'"),
    ],
    ids=[
        "arc",
        "no copies",
        "zero spacing",
        "fractional count",
        "no count",
        "parallel directions",
        "no second spacing",
        "too many",
        "no such entity",
    ],
)
def test_a_pattern_that_cant_be_made_is_refused(
    arguments: dict[str, object], expected: str
) -> None:
    w = Workspace(Document.empty())
    call(w, "create_circle", center={"x": 0, "y": 0}, radius=1)
    call(w, "create_arc", center={"x": 9, "y": 0}, radius=2, start_angle=0, sweep_angle=90)
    assert expected in refused(w, "linear_pattern", **arguments)
    assert len(w.commands) == 2


# --- All or nothing ---------------------------------------------------------------------


def test_a_step_caliper_rejects_takes_back_the_whole_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def half_done(document: Document, arguments: object, run: patterns.Run) -> patterns.Repeated:
        run(CreateCircle(center=Point2(x=0, y=0), radius=2))
        run(CreateLine(start=Point2(x=0, y=0), end=Point2(x=5, y=0)))
        run(CreateCircle(center=Point2(x=0, y=0), radius=-1))  # rejected
        raise AssertionError("not reached")

    monkeypatch.setattr(patterns, "mirror", half_done)
    w = laid_out()
    before = w.commands
    text = refused(w, "mirror_entities", ids=["e1"], axis="e3")
    assert "value.not_positive" in text
    assert "everything this call did" in text
    assert w.commands == before
    last = w.labels[-1]
    assert call(w, "undo")["undone"] == last  # type: ignore[index]  # it left no step behind
