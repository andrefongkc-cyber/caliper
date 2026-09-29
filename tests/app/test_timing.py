"""The Timing section's numbers: how runs start and end, and what each field measures.

A fake clock stands in for time, so every value here is exact. How the MCP host feeds the
timer, and the section in the window, are tested in test_mcp.py.
"""

from datetime import date
from pathlib import Path

import pytest

from caliper.app.agent.estimate import INFORMED, PRIOR_WEIGHT, PRIORS, Kind
from caliper.app.agent.timing import (
    IDLE,
    QUIET,
    RunTimer,
    Timing,
    markdown,
    minutes,
    seconds,
    timing_file,
)

DAY = date(2026, 9, 26)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def timer(clock: Clock) -> RunTimer:
    return RunTimer(clock=clock, today=lambda: DAY)


def call(
    timer: RunTimer,
    clock: Clock,
    took: float,
    *,
    after: float = 0.0,
    changed: bool = False,
    tool: str | None = None,
) -> None:
    """One MCP call to `tool`: `after` seconds of quiet, then `took` seconds inside Caliper."""
    clock.advance(after)
    arrived = timer.arrived()
    clock.advance(took)
    timer.finished(arrived, changed=changed, tool=tool)


def now(timer: RunTimer) -> Timing:
    timing = timer.timing
    assert timing is not None
    return timing


# --- MCP calls --------------------------------------------------------------------------


def test_there_is_no_run_before_the_first_call(timer: RunTimer) -> None:
    assert timer.timing is None


def test_a_call_is_timed_from_its_arrival_to_its_answer(timer: RunTimer, clock: Clock) -> None:
    call(timer, clock, 0.3)
    timing = now(timer)
    assert timing.calls == 1
    assert timing.longest_call == pytest.approx(0.3)
    assert timing.date == DAY


def test_several_calls_are_counted_and_the_longest_is_found(timer: RunTimer, clock: Clock) -> None:
    call(timer, clock, 0.2)
    call(timer, clock, 1.5, after=5)
    call(timer, clock, 0.4, after=5)
    timing = now(timer)
    assert timing.calls == 3
    assert timing.longest_call == pytest.approx(1.5)


def test_without_start_run_the_run_is_timed_from_the_first_call(
    timer: RunTimer, clock: Clock
) -> None:
    call(timer, clock, 0.2, after=30)  # the quiet before the first call isn't seen
    call(timer, clock, 1.5, after=5)
    call(timer, clock, 0.4, after=5)
    timing = now(timer)
    assert timing.total == pytest.approx(0.2 + 5 + 1.5 + 5 + 0.4)
    assert timing.first_response is None  # when the prompt was sent is unknown


def test_start_run_times_the_first_response_and_the_whole_run(
    timer: RunTimer, clock: Clock
) -> None:
    timer.start()
    call(timer, clock, 0.2, after=7.3)
    call(timer, clock, 0.4, after=20)
    timing = now(timer)
    assert timing.first_response == pytest.approx(7.3)
    assert timing.total == pytest.approx(7.3 + 0.2 + 20 + 0.4)


def test_a_run_started_by_hand_shows_nothing_measured_until_claude_calls(
    timer: RunTimer, clock: Clock
) -> None:
    timer.start()
    clock.advance(4)
    timing = now(timer)
    assert (timing.calls, timing.total, timing.first_response, timing.longest_call) == (
        0,
        None,
        None,
        None,
    )


# --- Proposals and Accept ---------------------------------------------------------------


def test_proposal_creation_runs_from_the_first_change_to_the_last(
    timer: RunTimer, clock: Clock
) -> None:
    call(timer, clock, 0.1)  # a look first: not part of the proposal
    call(timer, clock, 0.5, after=2, changed=True)
    call(timer, clock, 0.2, after=10)  # a look in between counts: it's building time
    call(timer, clock, 0.5, after=3, changed=True)
    call(timer, clock, 0.1, after=4)  # a look after the last change doesn't
    assert now(timer).proposal_creation == pytest.approx(0.5 + 10 + 0.2 + 3 + 0.5)
    timer.accepted(0.4)
    timer.proposal_ended()
    timing = now(timer)
    assert timing.proposal_creation == pytest.approx(14.2)
    assert timing.accept == pytest.approx(0.4)


def test_a_run_with_no_proposal_has_no_creation_or_accept_time(
    timer: RunTimer, clock: Clock
) -> None:
    call(timer, clock, 0.1)
    call(timer, clock, 0.1, after=3)
    timing = now(timer)
    assert timing.proposal_creation is None
    assert timing.accept is None


def test_a_rejected_proposal_keeps_its_creation_time_and_has_no_accept(
    timer: RunTimer, clock: Clock
) -> None:
    call(timer, clock, 0.5, changed=True)
    call(timer, clock, 0.5, after=9, changed=True)
    timer.proposal_ended()  # rejected: no accepted() first
    timing = now(timer)
    assert timing.proposal_creation == pytest.approx(10.0)
    assert timing.accept is None


def test_a_pending_proposal_has_no_accept_time_yet(timer: RunTimer, clock: Clock) -> None:
    call(timer, clock, 0.5, changed=True)
    assert now(timer).accept is None


def test_proposals_accepted_along_the_way_add_up(timer: RunTimer, clock: Clock) -> None:
    call(timer, clock, 1.0, changed=True)
    timer.accepted(0.2)
    timer.proposal_ended()
    call(timer, clock, 2.0, after=5, changed=True)
    timer.accepted(0.3)
    timer.proposal_ended()
    timing = now(timer)
    assert timing.proposal_creation == pytest.approx(3.0)  # the quiet in between isn't
    assert timing.accept == pytest.approx(0.5)


# --- Separate tasks ---------------------------------------------------------------------


def test_start_run_begins_a_new_run(timer: RunTimer, clock: Clock) -> None:
    call(timer, clock, 1.0, changed=True)
    timer.accepted(0.4)
    timer.proposal_ended()
    timer.start()
    timing = now(timer)
    assert (timing.calls, timing.proposal_creation, timing.accept) == (0, None, None)
    call(timer, clock, 0.2, after=3)
    assert now(timer).calls == 1


def test_a_call_after_a_quiet_spell_starts_a_new_run(timer: RunTimer, clock: Clock) -> None:
    call(timer, clock, 3.0, changed=True)
    call(timer, clock, 0.1, after=IDLE - 1)  # Claude thinking: the same run
    assert now(timer).calls == 2
    call(timer, clock, 0.2, after=IDLE + 1)  # a new task
    timing = now(timer)
    assert timing.calls == 1
    assert timing.longest_call == pytest.approx(0.2)
    assert timing.total == pytest.approx(0.2)
    assert timing.proposal_creation is None


def test_opening_another_document_ends_the_run(timer: RunTimer, clock: Clock) -> None:
    call(timer, clock, 1.0)
    timer.end()
    assert now(timer).calls == 1  # still shown, to be recorded
    call(timer, clock, 0.2, after=2)
    assert now(timer).calls == 1


def test_start_run_survives_opening_a_document_before_the_first_call(
    timer: RunTimer, clock: Clock
) -> None:
    timer.start()
    timer.end()  # File → New for the test, after pressing Start run
    call(timer, clock, 0.2, after=6)
    assert now(timer).first_response == pytest.approx(6.0)


def test_a_slow_first_call_still_joins_the_run_start_run_began(
    timer: RunTimer, clock: Clock
) -> None:
    timer.start()
    call(timer, clock, 0.2, after=IDLE + 60)
    assert now(timer).first_response == pytest.approx(IDLE + 60)


def test_each_run_is_dated_the_day_it_started(clock: Clock) -> None:
    days = iter([date(2026, 9, 26), date(2026, 9, 27)])
    timer = RunTimer(clock=clock, today=lambda: next(days))
    call(timer, clock, 0.1)
    assert now(timer).date == date(2026, 9, 26)
    call(timer, clock, 0.1, after=IDLE + 1)
    assert now(timer).date == date(2026, 9, 27)


# --- Live time -------------------------------------------------------------------------


def test_a_run_ticks_while_its_proposal_is_pending(timer: RunTimer, clock: Clock) -> None:
    call(timer, clock, 1.0, changed=True)
    clock.advance(20)  # Claude writes its reply; you review
    timing = now(timer)
    assert timing.live
    assert timing.elapsed == pytest.approx(21.0)
    assert timing.total == pytest.approx(1.0)  # the recorded value doesn't move


def test_accept_or_reject_stops_the_time(timer: RunTimer, clock: Clock) -> None:
    call(timer, clock, 1.0, changed=True)
    clock.advance(20)
    timer.proposal_ended()
    clock.advance(20)
    timing = now(timer)
    assert not timing.live
    assert timing.elapsed is None
    assert timing.total == pytest.approx(1.0)


def test_a_call_after_an_accept_part_way_starts_the_time_again(
    timer: RunTimer, clock: Clock
) -> None:
    call(timer, clock, 1.0, changed=True)
    timer.accepted(0.3)
    timer.proposal_ended()
    call(timer, clock, 1.0, after=5, changed=True)
    assert now(timer).live


def test_the_time_stops_by_itself_after_a_quiet_spell(timer: RunTimer, clock: Clock) -> None:
    # Claude has stopped calling: done, or it would have called by now. Total run was always
    # going to end at its last call, so that's what the time settles on.
    call(timer, clock, 0.2, changed=True)
    clock.advance(QUIET - 1)
    assert now(timer).live
    clock.advance(2)
    timing = now(timer)
    assert not timing.live
    assert timing.total == pytest.approx(0.2)
    call(timer, clock, 0.2, after=10)  # it was only thinking: the same run carries on
    assert now(timer).live
    assert now(timer).calls == 2


def test_before_the_first_call_the_time_waits_for_claude_as_long_as_a_run_lasts(
    timer: RunTimer, clock: Clock
) -> None:
    timer.start()
    clock.advance(IDLE - 1)  # planning a big task before its first call
    assert now(timer).live
    clock.advance(2)
    assert not now(timer).live


# --- Time left --------------------------------------------------------------------------
# The estimate's arithmetic and smoothing are in test_estimate.py; these check what the run
# feeds it and shows.


def progress(
    timer: RunTimer,
    clock: Clock,
    calls_left: int,
    *,
    after: float = 0.0,
    repeats: int | None = None,
    checks: int | None = None,
) -> None:
    """Claude's report_progress call, which takes no time inside Caliper."""
    clock.advance(after)
    arrived = timer.arrived()
    timer.progress(calls_left, repeats=repeats, checks=checks)
    timer.finished(arrived, changed=False, tool="report_progress")


def test_there_is_no_time_left_until_claude_says_how_many_calls(
    timer: RunTimer, clock: Clock
) -> None:
    call(timer, clock, 0.5, changed=True)
    assert now(timer).left is None
    assert now(timer).expected is None


def test_the_first_estimate_is_the_priors_and_is_marked_rough(
    timer: RunTimer, clock: Clock
) -> None:
    timer.start()
    progress(timer, clock, 30, after=4)
    timing = now(timer)
    assert timing.expected == 31  # counting the call that said so
    assert timing.left == pytest.approx(30 * PRIORS[Kind.OTHER])
    assert timing.rough
    clock.advance(1)
    assert now(timer).left == pytest.approx(30 * PRIORS[Kind.OTHER] - 1)  # counting down


def test_a_pattern_call_is_priced_and_counted_as_a_repeat(timer: RunTimer, clock: Clock) -> None:
    progress(timer, clock, 3, repeats=2)  # the first call: nothing before it to measure from
    call(timer, clock, 2.0, after=10.0, tool="linear_pattern")  # 12 s from the call before
    # One repeat and one other call to go, the repeat at the run's own cost blended with the
    # prior; what was shown (13.3 s) has run out, so the new figure shows at once.
    repeat = (12.0 + PRIOR_WEIGHT * PRIORS[Kind.REPEAT]) / (1 + PRIOR_WEIGHT)
    assert now(timer).left == pytest.approx(repeat + PRIORS[Kind.OTHER])


def test_a_check_is_priced_as_a_check(timer: RunTimer, clock: Clock) -> None:
    progress(timer, clock, 4, checks=3)
    call(timer, clock, 0.1, after=40.0, tool="run_check")  # stale: replaced at once
    check = (40.1 + PRIOR_WEIGHT * PRIORS[Kind.CHECK]) / (1 + PRIOR_WEIGHT)
    assert now(timer).left == pytest.approx(2 * check + PRIORS[Kind.OTHER])


def test_the_run_s_own_figures_take_over_and_the_estimate_is_no_longer_rough(
    timer: RunTimer, clock: Clock
) -> None:
    progress(timer, clock, 40)
    for _ in range(INFORMED):
        call(timer, clock, 0.5, after=2.5, tool="create_line")  # 3 s a call
        now(timer)  # the panel looks every second; here, after every call
    timing = now(timer)
    assert not timing.rough
    cost = (INFORMED * 3.0 + PRIOR_WEIGHT * PRIORS[Kind.OTHER]) / (INFORMED + PRIOR_WEIGHT)
    figure = (40 - INFORMED) * cost
    assert timing.left is not None
    # Smoothed toward it at each 15 s refresh, not jumped to it.
    assert 40 * PRIORS[Kind.OTHER] - 30 < timing.left <= figure


def test_a_new_plan_replaces_what_is_shown_at_once(timer: RunTimer, clock: Clock) -> None:
    progress(timer, clock, 100)
    call(timer, clock, 1.0, tool="create_line")
    now(timer)
    progress(timer, clock, 5)  # the report itself isn't one of the five
    assert now(timer).expected == 3 + 5
    cost = (1.0 + 0.0 + PRIOR_WEIGHT * PRIORS[Kind.OTHER]) / (2 + PRIOR_WEIGHT)
    assert now(timer).left == pytest.approx(5 * cost)


def test_the_time_left_never_goes_below_nothing(timer: RunTimer, clock: Clock) -> None:
    progress(timer, clock, 1)
    clock.advance(30)  # thinking longer than the last call's worth
    timing = now(timer)
    assert timing.live
    assert timing.left == 0.0


def test_claude_still_short_of_its_estimate_is_thinking_not_done(
    timer: RunTimer, clock: Clock
) -> None:
    progress(timer, clock, 50)
    call(timer, clock, 0.5)
    clock.advance(QUIET + 30)  # a long think mid-task: the time keeps going
    assert now(timer).live
    clock.advance(IDLE)
    assert not now(timer).live


def test_past_its_estimate_there_is_no_time_left_and_a_quiet_spell_stops_the_time(
    timer: RunTimer, clock: Clock
) -> None:
    progress(timer, clock, 1)
    call(timer, clock, 0.5)
    call(timer, clock, 0.5)
    timing = now(timer)
    assert timing.live
    assert timing.left is None
    assert timing.expected is not None  # the panel says it's finishing
    clock.advance(QUIET + 1)
    assert not now(timer).live


def test_claude_saying_it_is_done_stops_the_time_at_once(timer: RunTimer, clock: Clock) -> None:
    timer.start()
    progress(timer, clock, 3, after=2)
    call(timer, clock, 1.0, after=1, changed=True)
    progress(timer, clock, 0, after=1)
    timing = now(timer)
    assert not timing.live
    assert timing.left is None
    assert timing.total == pytest.approx(5.0)  # to the end of the call that said so
    assert timing.calls == 3
    call(timer, clock, 1.0, after=5)  # a call after all: the time runs again
    assert now(timer).live


def test_start_run_forgets_the_last_estimate(timer: RunTimer, clock: Clock) -> None:
    progress(timer, clock, 10)
    timer.start()
    assert now(timer).expected is None
    assert now(timer).left is None


def test_start_run_ticks_from_the_press_and_another_document_stops_it(
    timer: RunTimer, clock: Clock
) -> None:
    timer.start()
    clock.advance(3)
    assert now(timer).elapsed == pytest.approx(3.0)
    call(timer, clock, 0.5)
    timer.end()
    assert not now(timer).live


# --- The test folder's file -------------------------------------------------------------


def test_a_drawing_in_a_test_folder_gets_its_timing_file() -> None:
    folder = Path("/x/test-runs-manual/002-stress-plate-build")
    assert timing_file(folder / "stress-plate-build.caliper") == folder / "003-timing.md"


@pytest.mark.parametrize(
    "drawing",
    [
        "/x/parts/002-plate/plate.caliper",  # not under test-runs
        "/x/test-runs-manual/plate.caliper",  # not in a test's own folder
        "/x/test-runs-manual/2-plate/plate.caliper",  # not a three-digit number
        "/x/test-runs-manual/002-Plate/plate.caliper",  # not lowercase
    ],
)
def test_other_drawings_get_no_timing_file(drawing: str) -> None:
    assert timing_file(Path(drawing)) is None


# --- How it reads -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [(None, "N/A"), (0.4, "0m 00s"), (42.4, "0m 42s"), (59.6, "1m 00s"), (247, "4m 07s")],
)
def test_minutes(value: float | None, text: str) -> None:
    assert minutes(value) == text


@pytest.mark.parametrize(
    ("value", "text"), [(None, "N/A"), (0.04, "0.0s"), (7.34, "7.3s"), (72.46, "72.5s")]
)
def test_seconds(value: float | None, text: str) -> None:
    assert seconds(value) == text


def test_the_copy_is_exactly_the_timing_md_format() -> None:
    timing = Timing(
        date=DAY,
        total=247.2,
        calls=57,
        first_response=None,
        longest_call=0.23,
        proposal_creation=231.0,
        accept=0.41,
    )
    assert markdown(timing) == (
        "# Timing\n"
        "\n"
        "Date: 2026-09-26\n"
        "\n"
        "Total run: 4m 07s\n"
        "\n"
        "MCP/tool calls: 57\n"
        "\n"
        "First response: N/A\n"
        "Longest tool call: 0.2s\n"
        "\n"
        "Proposal creation: 3m 51s\n"
        "Accept: 0.4s\n"
    )
