"""Proposals: a change prepared on a scratch copy, reviewed, then applied as one undo step.

Whoever makes the proposal (the scripted agent, the in-app assistant, an MCP client) only
produces commands. Checks are among them: a check is stored in the document (C-1), so an
agent adds one with `CreateCheck` like any other change. This module runs the commands against
a copy of the document, so the review shows exactly what accepting would do: the resulting
geometry, each command's result, and every check before and after, the agent's and the
user's. Nothing touches the real document until `accept`. Qt-free.
"""

import re
from dataclasses import dataclass

from caliper.contracts.commands import Applied, Command, Rejected
from caliper.contracts.document import Document, EntityId, Expectation
from caliper.contracts.errors import Error
from caliper.contracts.queries import CheckResult
from caliper.engine.commands.bus import Bus
from caliper.engine.commands.handlers import Executed


@dataclass(frozen=True, slots=True)
class Plan:
    """What an agent wants to do, before anything runs."""

    label: str
    """Title case, used as the undo label: "Add Corner Holes"."""
    explanation: str
    commands: tuple[Command, ...]
    """Its checks too: `CreateCheck` after the commands that make what they measure."""
    executed: Executed | None = None
    """How `commands` already ran, when a workspace ran them: accepting then commits those
    outcomes instead of validating and solving every command again."""


@dataclass(frozen=True, slots=True)
class CheckChange:
    id: EntityId
    expectation: Expectation
    """As it stands after the proposal."""
    before: CheckResult
    after: CheckResult
    agent: bool
    """True for checks the proposal adds or edits, False for checks the user already had."""


@dataclass(frozen=True, slots=True)
class Proposal:
    plan: Plan
    base: Document
    """The document the proposal was prepared against."""
    result: Document
    errors: tuple[Error, ...]
    """Why a command was rejected. A proposal with errors can't be accepted."""
    checks: tuple[CheckChange, ...]
    removed_checks: tuple[Expectation, ...] = ()
    """Checks the user had that the proposal deletes."""

    @property
    def acceptable(self) -> bool:
        return not self.errors and all(c.after.passed for c in self.checks if c.agent)

    @property
    def broken_checks(self) -> tuple[CheckChange, ...]:
        """User checks that pass now and would fail after accepting."""
        return tuple(
            c for c in self.checks if not c.agent and c.before.passed and not c.after.passed
        )


def prepare(plan: Plan, base: Document, *, result: Document | None = None) -> Proposal:
    """`result`, when given, is what `plan.commands` already did to `base` on a scratch bus:
    an assistant's or MCP client's workspace, whose commands are resolved and all applied.
    It is used as it is. Replay is deterministic, so running the commands again would only
    rebuild the same document, and for a large proposal that replay was nearly all the
    time each change took. Accepting runs every command through the session's bus, which
    commits `plan.executed` rather than solving each command again."""
    errors: list[Error] = []
    if result is None:
        scratch = Bus(base)
        for command in plan.commands:
            outcome = scratch.execute(command)
            if isinstance(outcome, Rejected):
                errors.extend(outcome.errors)
                break
            assert isinstance(outcome, Applied)
        result = scratch.document
    before, after = Bus(base).queries, Bus(result).queries
    checks = tuple(
        CheckChange(
            id=id,
            expectation=e,
            before=before.check(e),
            after=after.check(e),
            agent=base.entities.get(id) != e,
        )
        for id, e in _checks(result)
    )
    removed = tuple(e for id, e in _checks(base) if id not in result.entities)
    return Proposal(
        plan=plan,
        base=base,
        result=result,
        errors=tuple(errors),
        checks=checks,
        removed_checks=removed,
    )


def _checks(document: Document) -> list[tuple[EntityId, Expectation]]:
    """The document's checks in the order they were made (e2 before e10)."""
    found = [(id, e) for id, e in document.entities.items() if isinstance(e, Expectation)]
    return sorted(
        found, key=lambda item: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", item[0])]
    )
