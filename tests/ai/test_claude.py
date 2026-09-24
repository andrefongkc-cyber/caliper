"""The Claude adapter, against a stand-in for the SDK client: no SDK or network needed."""

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from caliper.ai import claude
from caliper.ai.claude import ClaudeModel, messages, parse
from caliper.ai.model import (
    ModelError,
    Reply,
    Stop,
    ToolCall,
    ToolOutcome,
    ToolResults,
    ToolSpec,
    UserTurn,
)

TOOL = ToolSpec(name="solve_status", description="Status.", input_schema={"type": "object"})


def block(**fields: object) -> SimpleNamespace:
    return SimpleNamespace(**fields)


@dataclass
class FakeClient:
    """Records each request and answers with the next canned response."""

    responses: list[Any]
    requests: list[dict[str, Any]] = field(default_factory=list)

    @property
    def beta(self) -> SimpleNamespace:
        return SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def response(stop: str, *content: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop, content=list(content))


def test_the_request_uses_the_default_model_adaptive_thinking_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CALIPER_AI_MODEL", raising=False)
    client = FakeClient([response("end_turn", block(type="text", text="Hi"))])
    reply = ClaudeModel(client).reply("system text", [UserTurn("hello")], [TOOL])
    (request,) = client.requests
    assert request["model"] == "claude-opus-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["fallbacks"] == "default"
    assert request["system"] == "system text"
    assert request["tools"] == [
        {"name": "solve_status", "description": "Status.", "input_schema": {"type": "object"}}
    ]
    assert request["messages"] == [{"role": "user", "content": "hello"}]
    assert reply == Reply(text="Hi", stop=Stop.END, native=[block(type="text", text="Hi")])


def test_the_model_can_be_chosen_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALIPER_AI_MODEL", "claude-sonnet-5")
    assert ClaudeModel(FakeClient([])).name == "claude-sonnet-5"
    assert ClaudeModel(FakeClient([]), model="claude-opus-4-8").name == "claude-opus-4-8"


def test_tool_calls_are_parsed_and_replies_go_back_exactly_as_they_came() -> None:
    thinking = block(type="thinking", thinking="", signature="sig")
    use = block(type="tool_use", id="toolu_1", name="solve_status", input={})
    reply = parse(response("tool_use", thinking, block(type="text", text="Checking."), use))
    assert reply.calls == (ToolCall(id="toolu_1", name="solve_status", arguments={}),)
    assert reply.stop is Stop.TOOLS
    assert reply.text == "Checking."
    outcome = ToolOutcome(call=reply.calls[0], content={"state": "under"})
    failed = ToolOutcome(
        call=ToolCall(id="toolu_2", name="undo", arguments={}),
        content={"error": "nothing"},
        is_error=True,
    )
    shaped = messages([UserTurn("status?"), reply, ToolResults((outcome, failed))])
    assert shaped[1] == {
        "role": "assistant",
        "content": [thinking, block(type="text", text="Checking."), use],
    }
    # Every result of one reply in one user message, errors marked.
    assert shaped[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": json.dumps({"state": "under"}),
                "is_error": False,
            },
            {
                "type": "tool_result",
                "tool_use_id": "toolu_2",
                "content": json.dumps({"error": "nothing"}),
                "is_error": True,
            },
        ],
    }


def test_a_reply_made_elsewhere_is_shaped_as_text_and_tool_use_blocks() -> None:
    reply = Reply(
        text="Adding.",
        calls=(ToolCall(id="c1", name="solve_status", arguments={"a": 1}),),
        stop=Stop.TOOLS,
    )
    assert messages([reply]) == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Adding."},
                {"type": "tool_use", "id": "c1", "name": "solve_status", "input": {"a": 1}},
            ],
        }
    ]


def test_refusals_and_cut_off_answers_are_recognised() -> None:
    assert parse(response("refusal")).stop is Stop.REFUSED
    cut = parse(response("max_tokens", block(type="text", text="Half")))
    assert (cut.stop, cut.text) == (Stop.LIMIT, "Half")


def test_a_failing_request_becomes_a_model_error() -> None:
    client = FakeClient([ConnectionError("down")])
    with pytest.raises(ModelError, match="ConnectionError: down"):
        ClaudeModel(client).reply("s", [UserTurn("x")], [TOOL])


def test_without_the_sdk_the_model_says_how_to_get_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude, "installed", lambda: False)
    with pytest.raises(ModelError, match="isn't installed"):
        ClaudeModel().reply("s", [UserTurn("x")], [TOOL])
