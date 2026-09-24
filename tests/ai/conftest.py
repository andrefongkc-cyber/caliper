"""A stand-in model for the assistant's tests: it replays replies written in advance."""

from collections.abc import Callable, Sequence

import pytest

from caliper.ai.model import Message, Reply, Stop, ToolCall, ToolSpec

type Script = Sequence[Reply | Callable[[Sequence[Message]], Reply]]


class ScriptedModel:
    """Answers with each scripted reply in turn. A callable sees the conversation so far,
    so a reply can depend on what the tools returned."""

    name = "scripted"

    def __init__(self, script: Script) -> None:
        self.script = list(script)
        self.requests: list[tuple[str, list[Message], list[ToolSpec]]] = []

    def reply(
        self, system: str, conversation: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Reply:
        self.requests.append((system, list(conversation), list(tools)))
        if not self.script:
            raise AssertionError("the model was asked more often than the test expected")
        step = self.script.pop(0)
        return step(conversation) if callable(step) else step

    @staticmethod
    def calls(*calls: tuple[str, dict[str, object]], text: str = "") -> Reply:
        """A reply calling tools: `calls(("create_rectangle", {...}), ...)`."""
        return Reply(
            text=text,
            calls=tuple(
                ToolCall(id=f"call{i}", name=name, arguments=arguments)
                for i, (name, arguments) in enumerate(calls)
            ),
            stop=Stop.TOOLS,
        )

    @staticmethod
    def answer(text: str) -> Reply:
        return Reply(text=text)


@pytest.fixture
def scripted() -> type[ScriptedModel]:
    return ScriptedModel
