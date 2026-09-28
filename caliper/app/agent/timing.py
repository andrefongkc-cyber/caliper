"""How long a Claude Desktop task took, measured by Caliper: the Timing section's numbers.

Caliper sees a task only through its MCP tool calls. It can't see when you send the prompt in
Claude Desktop, or Claude's last message after its final call. So a run starts either when you
press Start run (as you send the prompt), or by itself at Claude's first call; and it ends at
the end of its latest call. The next run starts on Start run, when another document is
opened, or at a call after `IDLE` seconds with none. Every call is timed inside Caliper, from
when it arrives until its answer is ready. Definitions of each field: docs/mcp.md, Timing.

While a run is live its elapsed time ticks on screen. It stops when Claude says it's done
(`progress` with no calls left), when the proposal is accepted or rejected (or dropped), when
another run starts or a document opens, or after a quiet spell: `QUIET` seconds with no call,
or `IDLE` while Claude's estimate says it has calls to go. Total run then settles on the
recorded value. Saving a drawing into a test folder writes that folder's 003-timing.md
(`timing_file`, test-runs-manual/README.md).

Time left: Claude can say how many calls a task will take (`progress`, the MCP tool
report_progress), and Caliper turns that into time at the run's own pace, or `PACE` until the
run has `PACE_CALLS` calls to measure it by. It counts down between calls and is worked out
again at each one.

Qt-free: the MCP host reports calls, proposals, and accepts; the clock is injected for tests.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

IDLE = 180.0
"""Seconds with no MCP call after which the next call starts a new run."""
QUIET = 60.0
"""Seconds with no MCP call after which the time stops, unless Claude's estimate says it has
calls to go: then it's thinking, not done, and the time runs until `IDLE`."""
PACE = 2.0
"""Seconds per call, for time left before a run has its own pace: test 002 from Claude
Desktop took 9m 17s for 281 calls."""
PACE_CALLS = 10
"""Calls a run needs before its own pace replaces `PACE`."""
NA = "N/A"
FIELDS = (
    "Total run",
    "MCP/tool calls",
    "First response",
    "Longest tool call",
    "Proposal creation",
    "Accept",
)
"""The fields every test's 003-timing.md has after Date, in order, named exactly so."""
TIMING_FILE = "003-timing.md"
TEST_FOLDER = re.compile(r"\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*")
"""A test folder's name: a three-digit number and a short hyphenated name."""


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
    elapsed: float | None = None
    """While the run is live: seconds since it started, to tick on screen. None once it's
    settled; `total` is the recorded value either way."""
    expected: int | None = None
    """How many calls Claude said the run would take in all, if it said."""
    left: float | None = None
    """While the run is live and short of the calls Claude said it expects: the estimated
    seconds until it's done, counting down between calls. Not recorded anywhere."""

    @property
    def live(self) -> bool:
        return self.elapsed is not None


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
    expected: int | None = None
    """The calls Claude expects the run to take, counting the one that said so."""


class RunTimer:
    def __init__(
        self,
        clock: Callable[[], float] = time.perf_counter,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._clock = clock
        self._today = today
        self._run: _Run | None = None
        self._live = False
        """Whether the current run's time is still running (see the module docstring)."""
        self._open = False
        """Whether the next call joins the current run (subject to `IDLE`)."""
        self._proposal_run: _Run | None = None
        """The run the pending proposal was last changed in: its Accept counts there."""

    def start(self) -> None:
        """Start run: a new run from now, so First response can be measured."""
        self._run = _Run(date=self._today(), marked=self._clock())
        self._open = self._live = True

    def end(self) -> None:
        """Another document was opened: the next call starts a new run. A run started with
        Start run and still waiting for its first call carries on."""
        if self._run is not None and self._run.calls:
            self._open = self._live = False

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
        self._live = True  # again, if an accept part-way had stopped it
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

    def progress(self, calls_left: int) -> None:
        """Claude, during a call, expects `calls_left` more after it; 0 means it's done, and
        the time stops (a later call starts it again, as after an accept part-way)."""
        run = self._run
        if run is None:
            return
        run.expected = run.calls + 1 + calls_left  # this call is counted when it finishes
        if calls_left == 0:
            self._live = False

    def proposal_ended(self) -> None:
        """The pending proposal is gone: accepted, rejected, or dropped. The time stops."""
        self._live = False
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
        now = self._clock()
        began = run.marked if run.marked is not None else run.first_call
        since = run.last_done if run.last_done is not None else began
        to_go = 0 if run.expected is None else max(run.expected - run.calls, 0)
        quiet = IDLE if run.calls == 0 or to_go else QUIET
        live = self._live and since is not None and now - since < quiet
        left = None
        if live and to_go and since is not None:
            left = max(since + to_go * self._pace(run) - now, 0.0)
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
            elapsed=now - began if live and began is not None else None,
            expected=run.expected,
            left=left,
        )

    @staticmethod
    def _pace(run: _Run) -> float:
        """Seconds per call: the run's own, from Claude's first call, once it has enough."""
        if run.calls < PACE_CALLS or run.first_call is None or run.last_done is None:
            return PACE
        return (run.last_done - run.first_call) / run.calls


def timing_file(drawing: Path) -> Path | None:
    """Where a test's timing goes when its drawing is saved in a test folder
    (`test-runs-manual/NNN-name/`, test-runs-manual/README.md); None anywhere else."""
    folder = drawing.parent
    if TEST_FOLDER.fullmatch(folder.name) and folder.parent.name.startswith("test-runs"):
        return folder / TIMING_FILE
    return None


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
    """The run as a test folder's 003-timing.md, exactly (docs/mcp.md, Timing)."""
    (total, calls, first, longest, creation, accept) = (
        f"{name}: {value}" for name, value in rows(timing)
    )
    return (
        f"# Timing\n\nDate: {timing.date.isoformat()}\n\n{total}\n\n{calls}\n\n"
        f"{first}\n{longest}\n\n{creation}\n{accept}\n"
    )
