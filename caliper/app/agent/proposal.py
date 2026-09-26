"""Proposals: a change prepared on a scratch copy, reviewed, then applied as one undo step.

Whoever makes the proposal (the scripted agent today, a real model later) only produces
commands and the checks that should hold afterwards. This module runs them against a copy of
the document, so the review shows exactly what accepting would do: the resulting geometry,
each command's result, and every check before and after, including the user's own checks.
Nothing touches the real document until `accept`. Qt-free.
"""

from dataclasses import dataclass

from caliper.contracts.commands import Applied, Command, Rejected
from caliper.contracts.document import Document
from caliper.contracts.errors import Error
from caliper.contracts.queries import CheckResult, Expectation
from caliper.engine.commands.bus import Bus


@dataclass(frozen=True, slots=True)
class Plan:
    """What an agent wants to do, before anything runs."""

    label: str
    """Title case, used as the undo label: "Add Corner Holes"."""
    explanation: str
    commands: tuple[Command, ...]
    checks: tuple[Expectation, ...] = ()
    """What should be true afterwards. May name ids the commands create."""


@dataclass(frozen=True, slots=True)
class CheckChange:
    expectation: Expectation
    before: CheckResult
    after: CheckResult
    agent: bool
    """True for the plan's own checks, False for checks the user already had."""


@dataclass(frozen=True, slots=True)
class Proposal:
    plan: Plan
    base: Document
    """The document the proposal was prepared against."""
    result: Document
    errors: tuple[Error, ...]
    """Why a command was rejected. A proposal with errors can't be accepted."""
    checks: tuple[CheckChange, ...]

    @property
    def acceptable(self) -> bool:
        return not self.errors and all(c.after.passed for c in self.checks if c.agent)

    @property
    def broken_checks(self) -> tuple[CheckChange, ...]:
        """User checks that pass now and would fail after accepting."""
        return tuple(
            c for c in self.checks if not c.agent and c.before.passed and not c.after.passed
        )


def prepare(
    plan: Plan,
    base: Document,
    user_checks: tuple[Expectation, ...],
    *,
    result: Document | None = None,
) -> Proposal:
    """`result`, when given, is what `plan.commands` already did to `base` on a scratch bus:
    an assistant's or MCP client's workspace, whose commands are resolved and all applied.
    It is used as it is. Replay is deterministic, so running the commands again would only
    rebuild the same document, and for a large proposal that replay was nearly all the
    time each change took. Accepting still runs every command through the session's bus."""
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
        CheckChange(expectation=e, before=before.check(e), after=after.check(e), agent=agent)
        for agent, group in ((True, plan.checks), (False, user_checks))
        for e in group
    )
    return Proposal(plan=plan, base=base, result=result, errors=tuple(errors), checks=checks)
