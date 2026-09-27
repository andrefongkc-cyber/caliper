"""The Timing section's numbers: how runs start and end, and what each field measures.

A fake clock stands in for time, so every value here is exact. How the MCP host feeds the
timer, and the section in the window, are tested in test_mcp.py.
"""

from datetime import date
from pathlib import Path

import pytest

from caliper.app.agent.timing import (
    IDLE,
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


def call(timer: RunTimer, clock: Clock, took: float, *, after: float = 0.0, changed=False) -> None:
    """One MCP call: `after` seconds of quiet, then `took` seconds inside Caliper."""
    clock.advance(after)
    arrived = timer.arrived()
    clock.advance(took)
    timer.finished(arrived, changed=changed)


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


def test_a_run_with_no_proposal_stops_ticking_after_a_quiet_spell(
    timer: RunTimer, clock: Clock
) -> None:
    call(timer, clock, 0.2)
    clock.advance(IDLE - 1)
    assert now(timer).live
    clock.advance(2)
    assert not now(timer).live


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
