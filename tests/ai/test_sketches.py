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
