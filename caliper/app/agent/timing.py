"""How long a Claude Desktop task took, measured by Caliper: the Timing section's numbers.

Caliper sees a task only through its MCP tool calls. It can't see when you send the prompt in
Claude Desktop, or Claude's last message after its final call. So a run starts either when you
press Start run (as you send the prompt), or by itself at Claude's first call; and it ends at
the end of its latest call. The next run starts on Start run, when another document is
opened, or at a call after `IDLE` seconds with none. Every call is timed inside Caliper, from
when it arrives until its answer is ready. Definitions of each field: docs/mcp.md, Timing.

Qt-free: the MCP host reports calls, proposals, and accepts; the clock is injected for tests.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

IDLE = 180.0
"""Seconds with no MCP call after which the next call starts a new run."""
NA = "N/A"
FIELDS = (
    "Total run",
    "MCP/tool calls",
    "First response",
    "Longest tool call",
    "Proposal creation",
    "Accept",
)
"""The fields every test's timing.md has after Date, in order, named exactly so."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    """One run's measurements, in seconds. None where Caliper couldn't measure it."""

    date: date
    total: float | None
    calls: int
    first_response: float | None
    longest_call: float | None
    proposal_creation: float | None
    accept: float | None


@dataclass(slots=True, kw_only=True)
class _Run:
    date: date
    marked: float | None
    """When Start run was pressed; None when the first call started the run."""
    first_call: float | None = None
    last_done: float | None = None
    calls: int = 0
    longest: float | None = None
    built: float | None = None
    """Seconds spent building proposals that have closed, in this run."""
    building: tuple[float, float] | None = None
    """The pending proposal's time in this run: its first change's start, its last's end."""
    accept: float | None = None


class RunTimer:
    def __init__(
        self,
        clock: Callable[[], float] = time.perf_counter,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._clock = clock
        self._today = today
        self._run: _Run | None = None
        self._open = False
        """Whether the next call joins the current run (subject to `IDLE`)."""
        self._proposal_run: _Run | None = None
        """The run the pending proposal was last changed in: its Accept counts there."""

    def start(self) -> None:
        """Start run: a new run from now, so First response can be measured."""
        self._run = _Run(date=self._today(), marked=self._clock())
        self._open = True

    def end(self) -> None:
        """Another document was opened: the next call starts a new run. A run started with
        Start run and still waiting for its first call carries on."""
        if self._run is not None and self._run.calls:
            self._open = False

    def arrived(self) -> float:
        """A call arrived. Returns the time to pass to `finished`."""
        now = self._clock()
        run = self._run
        idle = run is not None and run.last_done is not None and now - run.last_done >= IDLE
        if run is None or not self._open or idle:
            run = self._run = _Run(date=self._today(), marked=None)
            self._open = True
        if run.first_call is None:
            run.first_call = now
        return now

    def finished(self, arrived: float, *, changed: bool) -> None:
        """The call that arrived at `arrived` has its answer. `changed`: it changed the
        pending proposal (or made one)."""
        run = self._run
        assert run is not None, "finished without arrived"
        now = self._clock()
        run.calls += 1
        run.last_done = now
        took = now - arrived
        run.longest = took if run.longest is None else max(run.longest, took)
        if changed:
            began = arrived if run.building is None else run.building[0]
            run.building = (began, now)
            self._proposal_run = run

    def accepted(self, seconds: float) -> None:
        """The user accepted the pending proposal; applying it took `seconds`."""
        run = self._proposal_run or self._run
        if run is not None:
            run.accept = seconds if run.accept is None else run.accept + seconds

    def proposal_ended(self) -> None:
        """The pending proposal is gone: accepted, rejected, or dropped."""
        run, self._proposal_run = self._proposal_run, None
        if run is not None and run.building is not None:
            began, ended = run.building
            run.built = (run.built or 0.0) + ended - began
            run.building = None

    @property
    def timing(self) -> Timing | None:
        """The current run, or None before the first. It stays after the run ends."""
        run = self._run
        if run is None:
            return None
        began = run.marked if run.marked is not None else run.first_call
        creation = run.built
        if run.building is not None:
            creation = (creation or 0.0) + run.building[1] - run.building[0]
        return Timing(
            date=run.date,
            total=None if began is None or run.last_done is None else run.last_done - began,
            calls=run.calls,
            first_response=(
                None
                if run.marked is None or run.first_call is None
                else run.first_call - run.marked
            ),
            longest_call=run.longest,
            proposal_creation=creation,
            accept=run.accept,
        )


# --- Showing it --------------------------------------------------------------------------


def minutes(seconds: float | None) -> str:
    """`Xm XXs`, to the nearest second."""
    if seconds is None:
        return NA
    whole = round(seconds)
    return f"{whole // 60}m {whole % 60:02d}s"


def seconds(value: float | None) -> str:
    """`X.Xs`, to a tenth of a second."""
    return NA if value is None else f"{value:.1f}s"


def rows(timing: Timing) -> list[tuple[str, str]]:
    """The `FIELDS` and their values, in order."""
    values = (
        minutes(timing.total),
        str(timing.calls),
        seconds(timing.first_response),
        seconds(timing.longest_call),
        minutes(timing.proposal_creation),
        seconds(timing.accept),
    )
    return list(zip(FIELDS, values, strict=True))


def markdown(timing: Timing) -> str:
    """The run as a test folder's timing.md, exactly (docs/mcp.md, Timing)."""
    (total, calls, first, longest, creation, accept) = (
        f"{name}: {value}" for name, value in rows(timing)
    )
    return (
        f"# Timing\n\nDate: {timing.date.isoformat()}\n\n{total}\n\n{calls}\n\n"
        f"{first}\n{longest}\n\n{creation}\n{accept}\n"
    )
