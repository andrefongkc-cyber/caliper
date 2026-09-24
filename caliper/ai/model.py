"""What the assistant says to a model and hears back, whichever model it is.

A `Model` turns the conversation so far, and the tools it may call, into its next `Reply`:
some text and any tool calls for the assistant to run. Provider details (clients, keys,
request shapes, model names) live in each implementation, never here.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from caliper.engine.io.canonical import JSON


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool the model may call: its name, what it does, and a JSON Schema for its input."""

    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    """The model's own id for the call, echoed back with its outcome."""
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    call: ToolCall
    content: JSON
    is_error: bool = False


class Stop(StrEnum):
    END = "end"
    """The model answered and called no tools."""
    TOOLS = "tools"
    """The model wants its tool calls run."""
    LIMIT = "limit"
    """The reply was cut off at the model's output limit."""
    REFUSED = "refused"
    """The model declined the request."""


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    calls: tuple[ToolCall, ...] = ()
    stop: Stop = Stop.END
    native: object = None
    """The provider's own form of this reply, sent back unchanged on the next request (it can
    carry reasoning the provider needs to see again). None for models that don't need it."""


@dataclass(frozen=True, slots=True)
class UserTurn:
    text: str


@dataclass(frozen=True, slots=True)
class ToolResults:
    outcomes: tuple[ToolOutcome, ...]


type Message = UserTurn | Reply | ToolResults


class ModelError(RuntimeError):
    """The model couldn't be reached or answered unusably. The message says why, for display."""


class Model(Protocol):
    @property
    def name(self) -> str:
        """Shown to the user, e.g. "claude-opus-5"."""
        ...

    def reply(
        self, system: str, conversation: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Reply:
        """The next reply. Raises `ModelError` when there is none."""
        ...
