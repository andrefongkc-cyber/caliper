"""The assistant's tools: Caliper's commands and queries, run in a scratch workspace."""

import dataclasses
import json
from typing import get_args

import pytest

from caliper.ai.model import ToolCall
from caliper.ai.tools import TOOLS, Workspace
from caliper.contracts.commands import (
    Command,
    CreateConstraint,
    CreateRectangle,
)
from caliper.contracts.document import (
    ConstraintType,
    Document,
    EntityId,
    Feature,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.queries import Expectation, Metric
from caliper.engine.commands.bus import Bus
from caliper.engine.io import codec, script, snapshot

E1 = EntityId("e1")


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="c", name=name, arguments=arguments)


def plate() -> Document:
    bus = Bus()
    bus.execute(CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0))
    return bus.document


# --- The tool list follows the contract -------------------------------------------------


def test_every_command_is_a_tool_with_a_schema_for_its_fields() -> None:
    tools = {t.name: t for t in TOOLS}
    for command in get_args(Command):
        tool = tools[command.kind]
        names = {f.name for f in dataclasses.fields(command)}
        schema = tool.input_schema
        assert set(schema["properties"]) == names  # type: ignore[arg-type]
        assert schema["additionalProperties"] is False
        json.dumps(schema)  # plain JSON, ready for any model API


def test_tool_names_are_unique_and_safe_for_model_apis() -> None:
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))
    assert all(n.replace("_", "").isalnum() and len(n) <= 64 for n in names)


# --- Commands ---------------------------------------------------------------------------


def test_a_command_tool_runs_the_command_and_reports_what_changed() -> None:
    workspace = Workspace(Document.empty())
    outcome = workspace.call(
        call("create_rectangle", corner={"x": 20, "y": 0}, width=100, height=50)
    )
    assert not outcome.is_error
    assert outcome.content["created"] == ["e1"]  # type: ignore[index]
    assert outcome.content["changed"]["added"]["e1"]["width"] == 100.0  # type: ignore[index]
    rectangle = workspace.document.entities[E1]
    assert rectangle == Rectangle(corner=Point2(x=20.0, y=0.0), width=100.0, height=50.0)
    # The resolved command: ids filled in and ints made floats, as replay records it.
    assert workspace.commands == (
        CreateRectangle(corner=Point2(x=20.0, y=0.0), width=100.0, height=50.0, id=E1),
    )


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        (
            "create_rectangle",
            {"corner": {"x": 0, "y": 0}, "width": -5, "height": 1},
            "value.not_positive",
        ),
        (
            "create_rectangle",
            {"corner": {"x": 0, "y": 0}, "width": "wide", "height": 1},
            "value.wrong_type",
        ),
        ("create_rectangle", {"width": 5, "height": 1}, "missing field(s): corner"),
        (
            "create_rectangle",
            {"corner": {"x": 0, "y": 0}, "width": 5, "height": 1, "colour": "red"},
            "unknown field(s): colour",
        ),
        ("modify_entity", {"id": "e9", "changes": {"width": 5}}, "entity.not_found"),
        ("delete_everything", {}, "no tool named"),
    ],
    ids=[
        "negative width",
        "wrong type",
        "missing field",
        "unknown field",
        "no such entity",
        "no such tool",
    ],
)
def test_an_invalid_action_is_rejected_and_changes_nothing(
    name: str, arguments: dict[str, object], expected: str
) -> None:
    workspace = Workspace(plate())
    before = workspace.document
    outcome = workspace.call(call(name, **arguments))
    assert outcome.is_error
    assert expected in json.dumps(outcome.content)
    assert workspace.document is before
    assert workspace.commands == ()


def test_arguments_that_are_not_an_object_are_refused() -> None:
    workspace = Workspace(plate())
    outcome = workspace.call(ToolCall(id="c", name="create_point", arguments=[1, 2]))  # type: ignore[arg-type]
    assert outcome.is_error
    assert workspace.commands == ()


def test_a_command_that_changes_nothing_is_not_recorded() -> None:
    workspace = Workspace(plate())
    outcome = workspace.call(call("modify_entity", id="e1", changes={"width": 120}))
    assert not outcome.is_error
    assert workspace.commands == ()


def test_undo_takes_back_this_turns_last_change_only() -> None:
    workspace = Workspace(plate())
    workspace.call(call("modify_entity", id="e1", changes={"width": 90}))
    workspace.call(call("create_circle", center={"x": 10, "y": 10}, radius=3))
    undone = workspace.call(call("undo"))
    assert undone.content["undone"] == "Create Circle"  # type: ignore[index]
    assert [c.kind for c in workspace.commands] == ["modify_entity"]
    workspace.call(call("undo"))
    assert workspace.document == plate()
    # The plate came from the user's document, not this turn: it stays.
    nothing = workspace.call(call("undo"))
    assert nothing.is_error
    assert E1 in workspace.document.entities


def test_the_commands_replay_on_the_original_document_to_the_same_bytes() -> None:
    base = plate()
    workspace = Workspace(base)
    workspace.call(call("create_circle", center={"x": 10, "y": 10}, radius=3))
    workspace.call(
        call(
            "create_constraint",
            type="horizontal",
            refs=[{"entity": "e1", "feature": "center"}, {"entity": "e2", "feature": "center"}],
        )
    )
    workspace.call(call("modify_entity", id="e1", changes={"width": 140}))
    # Through a command script's JSON, as a file history or a replay would carry them.
    text = json.dumps(
        {"format": script.FORMAT, "schema_version": 1, "commands": codec.encode(workspace.commands)}
    )
    replayed = Bus(base)
    for command in script.loads(text):
        replayed.execute(command)
    assert snapshot.dumps(replayed.document) == snapshot.dumps(workspace.document)
    assert snapshot.loads(snapshot.dumps(workspace.document)) == workspace.document


# --- Queries ----------------------------------------------------------------------------


def test_inspecting_entities_gives_fields_points_and_constraints() -> None:
    bus = Bus(plate())
    bus.execute(
        CreateConstraint(type=ConstraintType.FIX, refs=(Ref(entity=E1, feature=Feature.CENTER),))
    )
    workspace = Workspace(bus.document)
    found = workspace.call(call("inspect_entities", ids=["e1", "e9"])).content
    assert found["e1"]["width"] == 120.0  # type: ignore[index]
    assert found["e1"]["points"]["top_right"] == {"x": 120.0, "y": 50.0}  # type: ignore[index]
    assert found["e1"]["constrained_by"] == ["e2"]  # type: ignore[index]
    assert "no entity" in found["e9"]["error"]  # type: ignore[index]


def test_a_check_is_measured_and_remembered_for_review() -> None:
    workspace = Workspace(plate())
    passed = workspace.call(
        call("run_check", metric="bbox_width", expected=120, tolerance=0.001, ids=["e1"])
    )
    failed = workspace.call(
        call("run_check", metric="bbox_height", expected=60, tolerance=0.001, ids=["e1"])
    )
    broken = workspace.call(call("run_check", metric="volume", expected=1, tolerance=0))
    assert (passed.content["passed"], passed.content["actual"]) == (True, 120.0)  # type: ignore[index]
    assert (failed.content["passed"], failed.content["actual"]) == (False, 50.0)  # type: ignore[index]
    assert broken.content["error"]["code"] == "value.out_of_range"  # type: ignore[index]
    again = workspace.call(
        call("run_check", metric="bbox_width", expected=120, tolerance=0.001, ids=["e1"])
    )
    assert again.content["passed"] is True  # type: ignore[index]
    assert workspace.checks == (
        Expectation(metric=Metric.BBOX_WIDTH, expected=120.0, tolerance=0.001, ids=(E1,)),
        Expectation(metric=Metric.BBOX_HEIGHT, expected=60.0, tolerance=0.001, ids=(E1,)),
    )


def test_distance_status_and_applicable_constraints() -> None:
    workspace = Workspace(plate())
    distance = workspace.call(
        call(
            "measure_distance",
            a={"entity": "e1", "feature": "bottom_left"},
            b={"entity": "e1", "feature": "top_right"},
        )
    ).content
    assert (distance["dx"], distance["dy"]) == (120.0, 50.0)  # type: ignore[index]
    assert workspace.call(call("solve_status")).content["state"] == "under"  # type: ignore[index]
    options = workspace.call(
        call("applicable_constraints", refs=[{"entity": "e1", "feature": "center"}])
    ).content
    fix = next(o for o in options if o["type"] == "fix")  # type: ignore[union-attr, index]
    assert fix["fits"] is True
    bad = workspace.call(
        call(
            "measure_distance",
            a={"entity": "e1", "feature": "nose"},
            b={"entity": "e1", "feature": "center"},
        )
    )
    assert bad.is_error


def test_inspecting_the_document_matches_the_context_summary() -> None:
    workspace = Workspace(plate(), selection=frozenset({E1}))
    summary = workspace.call(call("inspect_document", limit=5)).content
    assert summary["selection"] == ["e1"]  # type: ignore[index]
    assert summary["entities"][0]["id"] == "e1"  # type: ignore[index]
    assert workspace.call(call("inspect_document", limit=0)).is_error
