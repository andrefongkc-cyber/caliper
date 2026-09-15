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


def prepare(plan: Plan, base: Document, user_checks: tuple[Expectation, ...]) -> Proposal:
    scratch = Bus(base)
    errors: list[Error] = []
    for command in plan.commands:
        result = scratch.execute(command)
        if isinstance(result, Rejected):
            errors.extend(result.errors)
            break
        assert isinstance(result, Applied)
    before, after = Bus(base).queries, scratch.queries
    checks = tuple(
        CheckChange(expectation=e, before=before.check(e), after=after.check(e), agent=agent)
        for agent, group in ((True, plan.checks), (False, user_checks))
        for e in group
    )
    return Proposal(
        plan=plan, base=base, result=scratch.document, errors=tuple(errors), checks=checks
    )
