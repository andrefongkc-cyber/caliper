"""The assistant: a request in, a model driving Caliper's tools, a reviewable change out.

One `ask` is one turn. The request goes to the model with a summary of the document; the
model calls tools; each call runs in a fresh `Workspace` on a copy of the document and its
outcome goes back to the model, until the model answers without calling a tool. The turn
ends with the commands the model ran (already resolved and replayable on the document it
started from), the checks it ran, and what it said. Nothing here changes the user's document:
the caller shows the turn for review and applies its commands as one undoable step.

The conversation carries over between turns, so a follow-up can say "make it wider".
"""

import json
import os
from collections.abc import Callable, Collection
from dataclasses import dataclass

from caliper.ai.claude import ClaudeModel
from caliper.ai.context import describe
from caliper.ai.model import (
    Message,
    Model,
    ModelError,
    Stop,
    ToolOutcome,
    ToolResults,
    UserTurn,
)
from caliper.ai.tools import CONVENTIONS, TOOLS, Workspace
from caliper.contracts.commands import Command
from caliper.contracts.document import Document, EntityId
from caliper.contracts.queries import Expectation

SYSTEM = f"""\
You are the assistant inside Caliper, a parametric 2D sketcher. You change the user's sketch \
only by calling Caliper's tools: one tool per Caliper command, plus tools to inspect the \
sketch, measure, and check. Caliper validates every command; a rejected command changes \
nothing and says why, so read the error, fix the arguments, and try again.

{CONVENTIONS}

Work in small steps: act, look at the result, then continue. When the request states a size, \
position, or distance, confirm it with run_check. Your changes are not applied yet: they are \
shown to the user, who accepts or rejects them as one step. When you are done, say briefly \
what you did, and say so plainly if you couldn't do some of it.\
"""

MAX_REPLIES = 16
"""Model replies per turn. A turn that needs more stops and says so."""


@dataclass(frozen=True, slots=True)
class Turn:
    request: str
    base: Document
    """The document the turn started from. Its commands apply to this document only."""
    result: Document
    steps: tuple[ToolOutcome, ...]
    reply: str
    commands: tuple[Command, ...]
    """Resolved commands, in order: applied to `base`, they give `result`."""
    labels: tuple[str, ...]
    checks: tuple[Expectation, ...]
    error: str | None = None

    @property
    def label(self) -> str:
        """For the undo menu: the single change's own label, or one for several."""
        return self.labels[0] if len(self.labels) == 1 else "Assistant Changes"


class Assistant:
    def __init__(self, model: Model, *, max_replies: int = MAX_REPLIES, context_limit: int = 40):
        self.model = model
        self.max_replies = max_replies
        self.context_limit = context_limit
        self.conversation: list[Message] = []
        self._last_seen: Document | None = None

    def reset(self) -> None:
        """Forget the conversation, e.g. when another document is opened."""
        self.conversation.clear()
        self._last_seen = None

    def ask(
        self,
        request: str,
        document: Document,
        selection: Collection[EntityId] = (),
        *,
        on_step: Callable[[ToolOutcome], None] | None = None,
    ) -> Turn:
        workspace = Workspace(document, frozenset(selection))
        context = describe(
            document,
            selection=selection,
            limit=self.context_limit,
            changes_since=self._last_seen,
        )
        self._last_seen = document
        self.conversation.append(
            UserTurn(f"{request}\n\nThe sketch now:\n{json.dumps(context, sort_keys=True)}")
        )
        steps: list[ToolOutcome] = []
        reply_text, error = "", None
        for _ in range(self.max_replies):
            try:
                reply = self.model.reply(SYSTEM, self.conversation, TOOLS)
            except ModelError as e:
                error = f"Couldn't get an answer from {self.model.name}: {e}"
                break
            self.conversation.append(reply)
            reply_text = reply.text
            if reply.stop is Stop.REFUSED:
                error = f"{self.model.name} declined this request."
                break
            if reply.stop is Stop.LIMIT and not reply.calls:
                error = "The answer was cut off before it finished."
                break
            if not reply.calls:
                break
            outcomes = tuple(workspace.call(call) for call in reply.calls)
            for outcome in outcomes:
                steps.append(outcome)
                if on_step is not None:
                    on_step(outcome)
            self.conversation.append(ToolResults(outcomes))
        else:
            error = f"Stopped after {self.max_replies} steps without finishing."
        return Turn(
            request=request,
            base=document,
            result=workspace.document,
            steps=tuple(steps),
            reply=reply_text,
            commands=workspace.commands,
            labels=workspace.labels,
            checks=workspace.checks,
            error=error,
        )


def from_environment() -> Assistant | None:
    """The assistant the environment asks for, or None.

    `CALIPER_ASSISTANT=claude` turns on Claude (`CALIPER_AI_MODEL` picks the model). Off by
    default, so nothing calls a paid API unless someone asked for it.
    """
    if os.environ.get("CALIPER_ASSISTANT", "").strip().lower() != "claude":
        return None
    return Assistant(ClaudeModel())
