"""An outside client's changes, held for the user's review: what MCP calls do inside Caliper.

A client such as Claude Desktop calls Caliper's tools one at a time, over MCP, and never says
when it's done. So its changes collect in one draft: a `Workspace` on the document as it was
when the first change came in, the same scratch bus the in-app assistant uses. After each call
that changes the draft, the shell shows the whole draft as a proposal, and the user accepts it
(one undo step) or rejects it, in Caliper. The client can't accept anything.

The draft ends when the user accepts or closes it, changes the sketch, or opens another
document. The client's next call then starts from the user's document as it is, and its result
comes with a note saying what happened, so the client isn't left guessing. Calls that only
look (inspect, measure, ...) run on the draft if there is one, else on the document. A check
is a change (it's stored in the sketch, C-1), so a check run with nothing pending starts a
draft of its own, for the user to accept like any other.
Qt-free: the shell feeds it the document and says when a proposal closes.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum

from caliper.ai.model import ToolCall, ToolOutcome
from caliper.ai.tools import Workspace
from caliper.contracts.commands import Command
from caliper.contracts.document import Document, EntityId
from caliper.engine.commands.handlers import Executed


class Ended(StrEnum):
    """Why a draft ended. Each value is the note the client's next call gets."""

    ACCEPTED = "The user accepted your pending changes in Caliper; they are now part of the sketch."
    CLOSED = "The user closed your pending changes without applying them; the sketch is as it was."
    CHANGED = (
        "The user changed the sketch in Caliper, so your pending changes were dropped "
        "without being applied. Look at the sketch again before continuing."
    )
    OPENED = (
        "Another document was opened in Caliper, so your pending changes were dropped "
        "without being applied. Look at the sketch again before continuing."
    )


@dataclass(frozen=True, slots=True)
class Answer:
    outcome: ToolOutcome
    note: str | None = None
    """What happened to the client's earlier changes since its last call, if anything."""
    changed: bool = False
    """The draft's commands changed: the shell should show it again."""


class Draft:
    def __init__(self) -> None:
        self._workspace: Workspace | None = None
        self._note: str | None = None

    @property
    def workspace(self) -> Workspace | None:
        """The pending changes, or None when there are none."""
        return self._workspace

    @property
    def base(self) -> Document | None:
        return None if self._workspace is None else self._workspace.base

    @property
    def commands(self) -> tuple[Command, ...]:
        return () if self._workspace is None else self._workspace.commands

    @property
    def executed(self) -> Executed | None:
        """How the commands ran, for accepting them without solving each one again."""
        return None if self._workspace is None else self._workspace.executed

    @property
    def label(self) -> str:
        """For the undo menu: see `Workspace.label`."""
        return "Assistant Changes" if self._workspace is None else self._workspace.label

    def end(self, why: Ended) -> None:
        """The pending changes are gone (accepted, closed, or overtaken). The latest reason
        wins, so a more specific one given later replaces a generic one."""
        if self._workspace is None and self._note is None:
            return  # nothing pending and nothing to tell
        self._workspace = None
        self._note = why.value

    def call(
        self,
        document: Document,
        selection: Collection[EntityId],
        name: str,
        arguments: Mapping[str, object],
        *,
        call_id: str = "mcp",
        sketch: EntityId | None = None,
    ) -> Answer:
        """Run one tool call against the user's `document` as it is now."""
        if self._workspace is not None and self._workspace.base is not document:
            self.end(Ended.CHANGED)
        note, self._note = self._note, None
        call = ToolCall(id=call_id, name=name, arguments=arguments)
        workspace = self._workspace
        if workspace is None:
            # A fresh look at the document. It becomes the draft only if a change lands.
            workspace = Workspace(document, frozenset(selection), sketch)
        before = workspace.commands
        outcome = workspace.call(call)
        if self._workspace is None:
            if workspace.commands:  # a command, a mirror or pattern, a check, ...
                self._workspace = workspace
                return Answer(outcome, note, changed=True)
            return Answer(outcome, note)
        changed = workspace.commands != before
        if not workspace.commands:
            self._workspace = None  # the client undid all of it: nothing is pending
        return Answer(outcome, note, changed=changed)
