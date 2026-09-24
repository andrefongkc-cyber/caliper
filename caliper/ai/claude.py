"""Claude as the assistant's model, through the official Anthropic SDK.

The SDK is optional: nothing else in Caliper imports it, and it is loaded only when this
model first answers. Credentials are whatever the SDK finds (`ANTHROPIC_API_KEY`, or a
profile from `ant auth login`); Caliper never reads or stores a key. `CALIPER_AI_MODEL`
chooses another model.

Requests use adaptive thinking, and server-side refusal fallbacks: if Claude's safety
classifiers decline, the API reruns the request on Anthropic's recommended fallback model
instead of returning a refusal. A reply goes back to the API exactly as it came, so its
reasoning carries into the next step.
"""

import importlib
import importlib.util
import json
import os
from collections.abc import Sequence
from typing import Any

from caliper.ai.model import (
    Message,
    ModelError,
    Reply,
    Stop,
    ToolCall,
    ToolResults,
    ToolSpec,
    UserTurn,
)

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 16000
FALLBACKS_BETA = "server-side-fallback-2026-07-01"


def installed() -> bool:
    """Whether the Anthropic SDK is available to this interpreter."""
    return importlib.util.find_spec("anthropic") is not None


class ClaudeModel:
    def __init__(self, client: Any = None, model: str | None = None) -> None:
        """`client` stands in for `anthropic.Anthropic()`, which is made on first use."""
        self._client = client
        self._name = model or os.environ.get("CALIPER_AI_MODEL") or DEFAULT_MODEL

    @property
    def name(self) -> str:
        return self._name

    def reply(
        self, system: str, conversation: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Reply:
        client = self._client if self._client is not None else self._connect()
        try:
            response = client.beta.messages.create(
                model=self._name,
                max_tokens=MAX_TOKENS,
                thinking={"type": "adaptive"},
                betas=[FALLBACKS_BETA],
                fallbacks="default",
                system=system,
                tools=[
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": dict(t.input_schema),
                    }
                    for t in tools
                ],
                messages=messages(conversation),
            )
        except Exception as e:  # the SDK's error classes, translated below
            raise ModelError(_explain(e)) from e
        return parse(response)

    def _connect(self) -> Any:
        if not installed():
            raise ModelError("the Anthropic SDK isn't installed (pip install anthropic)")
        anthropic = importlib.import_module("anthropic")
        self._client = anthropic.Anthropic()
        return self._client


def messages(conversation: Sequence[Message]) -> list[dict[str, object]]:
    """The conversation in the Messages API's shape. Consecutive user messages are allowed;
    the API reads them as one turn."""
    shaped: list[dict[str, object]] = []
    for message in conversation:
        match message:
            case UserTurn(text=text):
                shaped.append({"role": "user", "content": text})
            case Reply(text=text, calls=calls, native=native):
                content = native if native is not None else _content(text, calls)
                shaped.append({"role": "assistant", "content": content})
            case ToolResults(outcomes=outcomes):
                # Every result of one reply goes back in one message.
                shaped.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": o.call.id,
                                "content": json.dumps(o.content, sort_keys=True),
                                "is_error": o.is_error,
                            }
                            for o in outcomes
                        ],
                    }
                )
    return shaped


def _content(text: str, calls: Sequence[ToolCall]) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = [{"type": "text", "text": text}] if text else []
    blocks += [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": dict(c.arguments)} for c in calls
    ]
    return blocks


def parse(response: Any) -> Reply:
    """A Messages API response as a `Reply`, keeping its content to send back unchanged."""
    content = list(response.content)
    if response.stop_reason == "refusal":
        return Reply(text="", stop=Stop.REFUSED, native=content)
    text = "\n\n".join(block.text for block in content if block.type == "text").strip()
    calls = tuple(
        ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
        for block in content
        if block.type == "tool_use"
    )
    if response.stop_reason == "max_tokens":
        stop = Stop.LIMIT
    elif calls:
        stop = Stop.TOOLS
    else:
        stop = Stop.END
    return Reply(text=text, calls=calls, stop=stop, native=content)


def _explain(error: Exception) -> str:
    """A short reason for the user, by the SDK's error class when the SDK is there."""
    if installed():
        anthropic = importlib.import_module("anthropic")
        if isinstance(error, anthropic.AuthenticationError):
            return "no valid Anthropic credentials (set ANTHROPIC_API_KEY or run ant auth login)"
        if isinstance(error, anthropic.RateLimitError):
            return "rate limited; try again in a moment"
        if isinstance(error, anthropic.APIStatusError):
            return f"the API answered {error.status_code}: {error.message}"
        if isinstance(error, anthropic.APIConnectionError):
            return "couldn't reach the API; check the network connection"
    return f"{type(error).__name__}: {error}"
