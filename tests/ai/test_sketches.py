"""The AI tools in a part with more than one sketch (ADR 0011): drawing names the sketch,
repeats copy into the originals' sketch, and the document summary lists the part's sketches.
A part with one sketch reads as it always did."""

from caliper.ai.context import describe
from caliper.ai.model import ToolCall
from caliper.ai.tools import TOOLS, Workspace
from caliper.contracts.commands import CreateCircle, CreateLine, CreateSketch
from caliper.contracts.document import FIRST_SKETCH, Document, EntityId, Plane, Point2
from caliper.engine import part
from caliper.engine.commands.bus import Bus


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="c", name=name, arguments=arguments)


def two_sketches() -> tuple[Document, EntityId, EntityId, EntityId]:
    """A circle in the first sketch, and in a second (on XZ) a circle and a vertical line."""
    bus = Bus()
    bus.execute(CreateCircle(center=Point2(x=0.0, y=0.0), radius=1.0))
    (second,) = bus.execute(CreateSketch(plane=Plane.XZ)).created_ids  # type: ignore[union-attr]
    (seed,) = bus.execute(  # type: ignore[union-attr]
        CreateCircle(center=Point2(x=10.0, y=0.0), radius=2.0, sketch=second)
    ).created_ids
    (axis,) = bus.execute(  # type: ignore[union-attr]
        CreateLine(
            start=Point2(x=0.0, y=-5.0), end=Point2(x=0.0, y=5.0), construction=True, sketch=second
        )
    ).created_ids
    return bus.document, second, seed, axis


def made_in(workspace: Workspace, before: Document) -> set[EntityId | None]:
    added = workspace.document.entities.keys() - before.entities.keys()
    return {part.sketch_of(workspace.document, id) for id in added}


def test_a_mirror_copies_into_the_sketch_of_what_it_mirrors() -> None:
    document, second, seed, axis = two_sketches()
    workspace = Workspace(document)
    outcome = workspace.call(call("mirror_entities", ids=[seed], axis=axis))
    assert not outcome.is_error, outcome.content
    assert made_in(workspace, document) == {second}


def test_patterns_copy_into_the_sketch_of_their_originals() -> None:
    document, second, seed, axis = two_sketches()
    linear = Workspace(document)
    outcome = linear.call(call("linear_pattern", ids=[seed], count=3, spacing=10.0))
    assert not outcome.is_error, outcome.content
    assert made_in(linear, document) == {second}
    circular = Workspace(document)
    center = {"entity": axis, "feature": "mid"}  # in the second sketch too
    outcome = circular.call(call("circular_pattern", ids=[seed], center=center, count=3))
    assert not outcome.is_error, outcome.content
    assert made_in(circular, document) == {second}


def test_a_repeat_of_two_sketches_geometry_is_refused() -> None:
    document, _, seed, _ = two_sketches()
    workspace = Workspace(document)
    outcome = workspace.call(call("linear_pattern", ids=["e1", seed], count=2, spacing=5.0))
    assert outcome.is_error
    assert "more than one sketch" in str(outcome.content)
    assert workspace.document is document or workspace.document == document


def test_drawing_tools_draw_in_the_sketch_named() -> None:
    document, second, _, _ = two_sketches()
    workspace = Workspace(document)
    points = [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.0}, {"x": 5.0, "y": 5.0}]
    outcome = workspace.call(call("create_outline", points=points, closed=True, sketch=second))
    assert not outcome.is_error, outcome.content
    start, through, end = points
    arc = call("create_arc_through_points", start=start, through=through, end=end, sketch=second)
    assert not workspace.call(arc).is_error
    assert made_in(workspace, document) == {second}
    # Left out in a part with two sketches, it's refused: say which.
    unnamed = workspace.call(call("create_outline", points=points, closed=True))
    assert unnamed.is_error
    assert "sketch.required" in str(unnamed.content)
    tools = {t.name: t for t in TOOLS}
    for name in ("create_outline", "create_arc_through_points", "create_line"):
        assert "sketch" in tools[name].input_schema["properties"]  # type: ignore[operator]


def test_the_summary_lists_the_sketches_and_says_where_each_entity_is_only_when_it_matters() -> (
    None
):
    one = Bus()
    one.execute(CreateCircle(center=Point2(x=0.0, y=0.0), radius=1.0))
    summary = describe(one.document)
    assert summary["features"] == [{"id": FIRST_SKETCH, "kind": "sketch", "plane": "xy"}]
    assert all("sketch" not in e for e in summary["entities"])  # type: ignore[union-attr]
    document, second, seed, _ = two_sketches()
    summary = describe(document)
    assert [f["id"] for f in summary["features"]] == [FIRST_SKETCH, second]  # type: ignore[index, union-attr]
    shown = {e["id"]: e for e in summary["entities"]}  # type: ignore[index, union-attr]
    assert (shown["e1"]["sketch"], shown[seed]["sketch"]) == (FIRST_SKETCH, second)


def test_an_extrude_is_kept_in_the_proposal_though_it_changes_no_entity() -> None:
    """An extrude changes the part's features, not its entities: it must still count as a
    change, or the model's extrude would vanish from what the user is asked to accept."""
    bus = Bus()
    bus.execute(CreateCircle(center=Point2(x=0.0, y=0.0), radius=5.0))
    workspace = Workspace(bus.document)
    outcome = workspace.call(call("create_extrude", depth=3.0))
    assert not outcome.is_error, outcome.content
    assert [c.kind for c in workspace.commands] == ["create_extrude"]


def test_drawing_that_names_no_sketch_goes_into_the_one_being_edited() -> None:
    """The window draws into the sketch the user is editing; the AI's tools do the same, so
    a part with two sketches doesn't refuse every call that leaves the sketch out (F7)."""
    document, second, _, _ = two_sketches()
    workspace = Workspace(document, sketch=second)
    circle = workspace.call(call("create_circle", center={"x": 0.0, "y": 0.0}, radius=3.0))
    assert not circle.is_error, circle.content
    points = [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.0}, {"x": 5.0, "y": 5.0}]
    outline = workspace.call(call("create_outline", points=points, closed=True))
    assert not outline.is_error, outline.content
    assert made_in(workspace, document) == {second}
    # The resolved commands name it, so accepting or replaying them lands in the same place.
    drawn = [c for c in workspace.commands if isinstance(c, CreateCircle | CreateLine)]
    assert {c.sketch for c in drawn} == {second}
    # Named outright, the sketch named wins.
    first = workspace.call(
        call("create_circle", center={"x": 0.0, "y": 0.0}, radius=1.0, sketch=FIRST_SKETCH)
    )
    assert part.sketch_of(workspace.document, EntityId(first.content["created"][0])) == FIRST_SKETCH  # type: ignore[index]


def test_a_sketch_that_is_gone_is_not_drawn_into() -> None:
    """The window's sketch can be one the document no longer has (deleted, or undone, as the
    call comes in): then the call is as if no sketch were being edited, and says so."""
    document, _, _, _ = two_sketches()
    workspace = Workspace(document, sketch=EntityId("e99"))
    outcome = workspace.call(call("create_circle", center={"x": 0.0, "y": 0.0}, radius=3.0))
    assert outcome.is_error
    assert "sketch.required" in str(outcome.content)


def test_the_summary_says_which_sketch_is_being_edited_only_when_there_are_several() -> None:
    document, second, _, _ = two_sketches()
    assert describe(document, editing=second)["editing"] == second
    assert "editing" not in describe(document)
    one = Bus()
    assert "editing" not in describe(one.document, editing=FIRST_SKETCH)
    workspace = Workspace(document, sketch=second)
    inspected = workspace.call(call("inspect_document"))
    assert inspected.content["editing"] == second  # type: ignore[index]


def test_an_extrude_names_the_change_though_a_check_comes_with_it() -> None:
    """A check goes with what it checks: an extrude and its volume check is "Extrude" in the
    undo menu, not "Assistant Changes" (an extrude changes no entity, so it looked like one
    more check)."""
    bus = Bus()
    bus.execute(CreateCircle(center=Point2(x=0.0, y=0.0), radius=5.0))
    workspace = Workspace(bus.document)
    assert not workspace.call(call("create_extrude", depth=3.0)).is_error
    volume = call("run_check", metric="volume", expected=1.0, tolerance=1e9)
    assert not workspace.call(volume).is_error
    assert workspace.label == "Extrude"


def test_drawing_in_a_part_with_no_sketch_makes_one_on_top_in_the_same_call() -> None:
    """The app's 3D tab starts with no sketch (ADR 0015). Claude's first drawing makes one on
    XY, as a new part always had, and one undo takes both back."""
    empty = part.no_sketch()
    workspace = Workspace(empty)
    outcome = workspace.call(call("create_circle", center={"x": 0.0, "y": 0.0}, radius=3.0))
    assert not outcome.is_error, outcome.content
    sketch, circle = workspace.commands
    assert isinstance(sketch, CreateSketch)
    assert sketch.plane is Plane.XY
    assert isinstance(circle, CreateCircle)
    assert circle.sketch == sketch.id
    assert "the part had no sketch" in outcome.content["sketch"]  # type: ignore[index]
    assert workspace.label == "Create Circle"  # the sketch goes with what's drawn in it
    assert not workspace.call(call("undo")).is_error
    assert workspace.document == empty


def test_a_drawing_tool_in_a_part_with_no_sketch_makes_one_sketch() -> None:
    workspace = Workspace(part.no_sketch())
    points = [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.0}, {"x": 5.0, "y": 5.0}]
    outcome = workspace.call(call("create_outline", points=points, closed=True))
    assert not outcome.is_error, outcome.content
    assert len(part.sketches(workspace.document)) == 1
    assert sum(isinstance(c, CreateSketch) for c in workspace.commands) == 1


def test_an_extrude_in_a_part_with_no_sketch_is_refused() -> None:
    workspace = Workspace(part.no_sketch())
    outcome = workspace.call(call("create_extrude", depth=3.0))
    assert outcome.is_error
    assert workspace.document == part.no_sketch()


# --- Sketches on faces (ADR 0016) --------------------------------------------------------


def plate_part() -> tuple[Workspace, str, str]:
    """Claude draws a 120 x 50 plate in a part with no sketch, and extrudes it 10 deep."""
    w = Workspace(part.no_sketch())
    outcome = w.call(
        call(
            "create_rectangle",
            corner={"x": 0.0, "y": 0.0},
            width=120.0,
            height=50.0,
        )
    )
    assert not outcome.is_error, outcome.content
    rectangle = outcome.content["created"][0]  # type: ignore[index]
    extrude = w.call(call("create_extrude", depth=10.0))
    assert not extrude.is_error, extrude.content
    return w, rectangle, extrude.content["created"][0]  # type: ignore[index]


def test_claude_lists_an_extrudes_faces_with_where_each_is() -> None:
    w, rectangle, extrude = plate_part()
    found = w.call(call("inspect_faces", extrude=extrude))
    assert not found.is_error, found.content
    faces = {f["face"]: f for f in found.content["faces"]}  # type: ignore[index]
    assert list(faces) == [
        "start",
        "end",
        *(f"side {rectangle}.{s}" for s in ("bottom", "right", "top", "left")),
    ]
    assert faces["end"]["origin"] == {"x": 0.0, "y": 0.0, "z": 10.0}
    assert faces["end"]["normal"] == {"x": 0.0, "y": 0.0, "z": 1.0}
    assert faces[f"side {rectangle}.right"]["normal"] == {"x": 1.0, "y": 0.0, "z": 0.0}
    wrong = w.call(call("inspect_faces", extrude=rectangle))
    assert wrong.is_error
    assert "entity.wrong_kind" in str(wrong.content)


def test_claude_sketches_on_the_top_face_draws_in_it_and_cuts_into_the_part() -> None:
    w, _, extrude = plate_part()
    made = w.call(call("create_sketch", plane={"feature": extrude, "face": "end"}))
    assert not made.is_error, made.content
    sketch = made.content["created"][0]  # type: ignore[index]
    assert made.content["placement"]["origin"] == {"x": 0.0, "y": 0.0, "z": 10.0}  # type: ignore[index]
    assert w.sketch == sketch
    hole = w.call(call("create_circle", center={"x": 60.0, "y": 25.0}, radius=5.0))
    assert not hole.is_error, hole.content
    (circle,) = hole.content["created"]  # type: ignore[index]
    assert w.document.entities[EntityId(circle)].sketch == sketch  # drawn in the new sketch
    cut = w.call(call("create_extrude", depth=4.0, sketch=sketch, operation="remove"))
    assert not cut.is_error, cut.content
    resolved = w.commands[-1]
    assert resolved.reversed is True  # type: ignore[union-attr]
    describe(w.document)  # the summary takes a face sketch in its stride


def test_claude_is_told_why_a_face_isnt_there() -> None:
    w, _, extrude = plate_part()
    refused = w.call(call("create_sketch", plane={"feature": extrude, "face": "side e99"}))
    assert refused.is_error
    assert "face.not_found" in str(refused.content)
    assert w.sketch is None
