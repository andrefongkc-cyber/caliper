"""The assistant's loop: request, model, tools, results, and a reviewable turn."""

import json
from typing import Any

import pytest

from caliper.ai.agent import SYSTEM, Assistant, from_environment
from caliper.ai.model import (
    Message,
    ModelError,
    Reply,
    Stop,
    ToolResults,
    UserTurn,
)
from caliper.ai.tools import TOOLS
from caliper.contracts.commands import CreateRectangle, ModifyEntity
from caliper.contracts.document import Document, EntityId, Point2, Rectangle
from caliper.contracts.queries import Expectation, Metric
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot

E1 = EntityId("e1")
RECTANGLE = ("create_rectangle", {"corner": {"x": 20, "y": 0}, "width": 100, "height": 50})


def last_results(conversation: list[Message]) -> list[object]:
    results = conversation[-1]
    assert isinstance(results, ToolResults)
    return [outcome.content for outcome in results.outcomes]


def test_a_request_becomes_a_command_the_model_verifies_then_explains(scripted: Any) -> None:
    model = scripted(
        [
            scripted.calls(RECTANGLE, text="I'll add the rectangle."),
            scripted.calls(
                (
                    "run_check",
                    {"metric": "bbox_width", "expected": 100, "tolerance": 1e-6, "ids": ["e1"]},
                )
            ),
            scripted.answer(
                "Added a 100 x 50 rectangle with its corner 20 mm right of the origin."
            ),
        ]
    )
    turn = Assistant(model).ask(
        "Make a 100 by 50 rectangle 20 mm right of the origin", Document.empty()
    )
    assert turn.error is None
    assert turn.reply.startswith("Added a 100 x 50 rectangle")
    assert turn.commands == (
        CreateRectangle(corner=Point2(x=20.0, y=0.0), width=100.0, height=50.0, id=E1),
    )
    assert turn.label == "Create Rectangle"
    assert turn.checks == (
        Expectation(metric=Metric.BBOX_WIDTH, expected=100.0, tolerance=1e-6, ids=(E1,)),
    )
    assert turn.result.entities[E1] == Rectangle(
        corner=Point2(x=20.0, y=0.0), width=100.0, height=50.0
    )
    assert turn.base == Document.empty()  # the user's document is untouched
    # The model saw the system prompt, every tool, and the sketch with the request.
    system, conversation, tools = model.requests[0]
    assert system == SYSTEM
    assert [t.name for t in tools] == [t.name for t in TOOLS]
    first = conversation[0]
    assert isinstance(first, UserTurn)
    assert "20 mm right of the origin" in first.text
    assert '"entity_count": 0' in first.text


def test_a_multi_step_request_uses_each_result_for_the_next_step(scripted: Any) -> None:
    def constrain_what_was_created(conversation: list[Message]) -> Reply:
        (created,) = last_results(conversation)
        (id,) = created["created"]  # type: ignore[index]
        return scripted.calls(
            ("create_constraint", {"type": "fix", "refs": [{"entity": id, "feature": "center"}]})
        )

    model = scripted(
        [
            scripted.calls(
                ("create_rectangle", {"corner": {"x": -50, "y": -25}, "width": 100, "height": 50})
            ),
            constrain_what_was_created,
            scripted.calls(("solve_status", {})),
            scripted.answer("Centred a 100 x 50 rectangle on the origin and fixed its centre."),
        ]
    )
    turn = Assistant(model).ask(
        "Centre a 100x50 rectangle on the origin and fix it", Document.empty()
    )
    assert [c.kind for c in turn.commands] == ["create_rectangle", "create_constraint"]
    assert turn.label == "Assistant Changes"
    assert turn.steps[-1].content["dof"] == 2  # type: ignore[index]  # w and h can still change
    assert turn.error is None


def test_a_rejected_action_goes_back_to_the_model_which_can_retry(scripted: Any) -> None:
    def retry(conversation: list[Message]) -> Reply:
        (rejected,) = last_results(conversation)
        assert rejected["rejected"][0]["code"] == "value.not_positive"  # type: ignore[index]
        return scripted.calls(("create_circle", {"center": {"x": 0, "y": 0}, "radius": 5}))

    model = scripted(
        [
            scripted.calls(("create_circle", {"center": {"x": 0, "y": 0}, "radius": -5})),
            retry,
            scripted.answer("Added a circle of radius 5."),
        ]
    )
    turn = Assistant(model).ask("circle radius 5", Document.empty())
    assert [s.is_error for s in turn.steps] == [True, False]
    assert [c.kind for c in turn.commands] == ["create_circle"]


def test_a_model_that_never_finishes_is_stopped(scripted: Any) -> None:
    model = scripted([scripted.calls(("solve_status", {}))] * 3)
    turn = Assistant(model, max_replies=3).ask("loop forever", Document.empty())
    assert turn.error == "Stopped after 3 steps without finishing."
    assert len(turn.steps) == 3


def test_model_failures_and_refusals_end_the_turn_with_a_reason(scripted: Any) -> None:
    class Unreachable:
        name = "offline"

        def reply(self, system: object, conversation: object, tools: object) -> Reply:
            raise ModelError("no network")

    turn = Assistant(Unreachable()).ask("anything", Document.empty())
    assert turn.error == "Couldn't get an answer from offline: no network"
    assert turn.commands == ()
    refused = Assistant(scripted([Reply(text="", stop=Stop.REFUSED)])).ask("x", Document.empty())
    assert refused.error == "scripted declined this request."
    cut = Assistant(scripted([Reply(text="Half an ans", stop=Stop.LIMIT)])).ask(
        "x", Document.empty()
    )
    assert cut.error == "The answer was cut off before it finished."


def test_the_conversation_carries_over_and_hears_about_changes_between_turns(scripted: Any) -> None:
    model = scripted(
        [
            scripted.calls(RECTANGLE),
            scripted.answer("Added it."),
            scripted.calls(("modify_entity", {"id": "e1", "changes": {"width": 140}})),
            scripted.answer("Widened it."),
        ]
    )
    assistant = Assistant(model)
    first = assistant.ask("add a rectangle", Document.empty())
    # The user accepts: their document now holds the first turn's result.
    bus = Bus(first.base)
    for command in first.commands:
        bus.execute(command)
    second = assistant.ask("make it 140 wide", bus.document)
    assert second.commands == (ModifyEntity(id=E1, changes={"width": 140.0}),)
    request = assistant.conversation[4]
    assert isinstance(request, UserTurn)
    context = json.loads(request.text.split("The sketch now:\n", 1)[1])
    assert context["changes_since_your_last_turn"]["added"] == ["e1"]
    assert len(model.requests[2][1]) == 5  # both turns, in order
    assistant.reset()
    assert assistant.conversation == []


def test_accepting_a_turn_replays_to_the_same_file_and_undoes_in_one_step(scripted: Any) -> None:
    model = scripted(
        [
            scripted.calls(
                RECTANGLE, ("create_circle", {"center": {"x": 40, "y": 20}, "radius": 4})
            ),
            scripted.answer("Done."),
        ]
    )
    turn = Assistant(model).ask("plate with a hole", Document.empty())
    bus = Bus(turn.base)
    with bus.transaction(turn.label):
        for command in turn.commands:
            bus.execute(command)
    assert snapshot.dumps(bus.document) == snapshot.dumps(turn.result)
    assert snapshot.loads(snapshot.dumps(bus.document)) == turn.result
    assert bus.undo_label == "Assistant Changes"
    bus.undo()
    assert bus.document == Document.empty()
    bus.redo()
    assert bus.document == turn.result


def test_the_assistant_is_off_unless_the_environment_asks_for_claude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CALIPER_ASSISTANT", raising=False)
    assert from_environment() is None
    monkeypatch.setenv("CALIPER_ASSISTANT", "something-else")
    assert from_environment() is None
    monkeypatch.setenv("CALIPER_ASSISTANT", "Claude")
    monkeypatch.setenv("CALIPER_AI_MODEL", "claude-opus-5")
    assistant = from_environment()
    assert assistant is not None
    assert assistant.model.name == "claude-opus-5"
