"""Time left in a Claude Desktop task, for the Timing panel: an estimate that improves as the
run goes on, and moves smoothly on screen.

Caliper sees a task only as tool calls. Claude says how many it plans (the MCP tool
report_progress), and, if it knows, how many of them are repeats (a mirror or pattern: one
call, hundreds of commands) and checks. What a call costs, from the end of the call before it,
is two things Caliper measures (`RunTimer`): the time between calls, which is Claude working
out the next call, the MCP round trip, and Caliper redrawing, and is most of it; and the time
inside Caliper, which is the command, the solve, and the proposal. Both depend on the kind of
call: checks come in quick batches; a pattern is a long solve that Claude thinks about first.

So the estimate is, for each kind, the calls to go times what one has cost so far in this run,
blended with a prior while there are few (`PRIORS`, `PRIOR_WEIGHT`). The first estimate is the
prior's, and the run's own figures take over as its calls are measured. While Claude is
between calls, the time it has spent counts toward the next one.

On screen it moves smoothly. It counts down between refreshes; every `REFRESH` seconds, and
when a repeat finishes, it moves `SMOOTHING` of the way to the new figure. A new plan from
Claude, or a figure clearly out of line with what's shown (`STALE`), replaces it at once. Until
`INFORMED` calls have been measured it's mostly the prior, so it's marked rough.

Qt-free, and it never reads the clock: `RunTimer` passes the time in.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from caliper.ai.tools import REPEAT_TOOLS


class Kind(StrEnum):
    """Calls that cost differently."""

    REPEAT = "repeat"
    """A mirror or pattern: a long solve, and some thought before it."""
    CHECK = "check"
    """run_check: quick to write and to run, and usually several in a row."""
    OTHER = "other"
    """Everything else: one command, or a look at the sketch."""


REPEATS = frozenset(spec.name for spec in REPEAT_TOOLS)


def kind(tool: str | None) -> Kind:
    if tool in REPEATS:
        return Kind.REPEAT
    return Kind.CHECK if tool == "run_check" else Kind.OTHER


PRIORS = {Kind.REPEAT: 5.0, Kind.CHECK: 0.8, Kind.OTHER: 1.7}
"""Seconds per call, from the end of the call before, until a run has its own figures. From
test 003 (Claude Code over MCP): 162 calls in 4m 40s, 1.7 s a call, with twelve checks sent
in one batch and three repeats, the longest 1.9 s inside Caliper. Rough by nature: the run's
own figures replace them."""
PRIOR_WEIGHT = 5.0
"""How many calls the prior counts as: with this many measured, the run's own figure is half
the estimate."""
INFORMED = 10
"""Calls measured before the estimate is more the run's than the prior's, and is no longer
marked rough."""
REFRESH = 15.0
"""Seconds between refreshes of what's shown."""
SMOOTHING = 0.5
"""How far a refresh moves what's shown toward the new figure."""
STALE = 0.5
STALE_SECONDS = 30.0
"""What's shown is replaced at once when the new figure differs from it by more than this
fraction of the figure, and by more than this many seconds; or when it has counted down to
nothing with calls still to go."""


@dataclass(slots=True)
class Estimate:
    """One run's estimate: what calls have cost, Claude's plan, and what's shown."""

    spent: dict[Kind, float] = field(default_factory=dict)
    """Seconds the measured calls of each kind took, each from the end of the call before."""
    measured: dict[Kind, int] = field(default_factory=dict)
    plan: dict[Kind, int] | None = None
    """The calls to go of each kind when Claude last said; None until it says."""
    split: bool = False
    """Whether Claude said which kinds. If not, the plan is all OTHER, priced at the average
    of every kind."""
    done: dict[Kind, int] = field(default_factory=dict)
    """Calls of each kind since the plan."""
    shown_end: float | None = None
    """The time what's shown counts down to."""
    refreshed: float = 0.0
    due: bool = False
    """Refresh at the next look: a repeat just finished."""
    replanned: bool = False
    """Replace what's shown at the next look: Claude just gave a new plan."""

    def measure(self, of: Kind, seconds: float) -> None:
        """A call of kind `of` finished `seconds` after the call before it did."""
        self.spent[of] = self.spent.get(of, 0.0) + seconds
        self.measured[of] = self.measured.get(of, 0) + 1
        if of is Kind.REPEAT:
            self.due = True

    def called(self, of: Kind) -> None:
        """A call of kind `of` counts toward the plan."""
        self.done[of] = self.done.get(of, 0) + 1

    def planned(self, calls_left: int, repeats: int | None, checks: int | None) -> None:
        """Claude's plan: `calls_left` more calls, of which `repeats` and `checks` if said."""
        repeats, checks = repeats or 0, checks or 0
        self.split = bool(repeats or checks)
        self.plan = {
            Kind.REPEAT: repeats,
            Kind.CHECK: checks,
            Kind.OTHER: max(calls_left - repeats - checks, 0),
        }
        self.done = {}
        self.replanned = True

    @property
    def informed(self) -> bool:
        return sum(self.measured.values()) >= INFORMED

    def cost(self, of: Kind | None) -> float:
        """Seconds one more call of kind `of` (None: of any kind) is expected to take."""
        kinds = list(Kind) if of is None else [of]
        spent = sum(self.spent.get(k, 0.0) for k in kinds)
        count = sum(self.measured.get(k, 0) for k in kinds)
        prior = PRIORS[Kind.OTHER if of is None else of]  # most calls are OTHER
        return (spent + PRIOR_WEIGHT * prior) / (count + PRIOR_WEIGHT)

    def to_go(self) -> dict[Kind | None, int]:
        """The calls left of each kind, or of any kind (None) when Claude didn't say."""
        if self.plan is None:
            return {}
        if not self.split:
            total = sum(self.plan.values()) - sum(self.done.values())
            return {None: max(total, 0)}
        return {k: max(n - self.done.get(k, 0), 0) for k, n in self.plan.items()}

    def figure(self, waited: float) -> float | None:
        """Seconds left, with `waited` seconds already spent since the last call; None
        without a plan, or with none of it left."""
        left = {k: n for k, n in self.to_go().items() if n}
        calls = sum(left.values())
        if not calls:
            return None
        total = sum(n * self.cost(k) for k, n in left.items())
        return total - min(waited, total / calls)  # the wait is toward the next call only

    def shown(self, now: float, waited: float) -> float | None:
        """Seconds left to show at `now`: the countdown, refreshed as described above."""
        figure = self.figure(waited)
        if figure is None:
            self.shown_end = None
            return None
        current = None if self.shown_end is None else self.shown_end - now
        if (
            current is None
            or self.replanned
            or current <= 0
            or abs(figure - current) > max(STALE * figure, STALE_SECONDS)
        ):
            self._show(now, figure)
        elif self.due or now - self.refreshed >= REFRESH:
            self._show(now, current + SMOOTHING * (figure - current))
        assert self.shown_end is not None
        return max(self.shown_end - now, 0.0)

    def _show(self, now: float, left: float) -> None:
        self.shown_end = now + left
        self.refreshed = now
        self.due = self.replanned = False
