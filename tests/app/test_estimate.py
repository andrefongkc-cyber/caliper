"""Time left: what each kind of call is expected to cost, and how what's shown moves.

How the run timer feeds it, and what the Timing panel shows, are in test_timing.py and
test_mcp.py.
"""

import pytest

from caliper.app.agent.estimate import (
    INFORMED,
    PRIOR_WEIGHT,
    PRIORS,
    REFRESH,
    SMOOTHING,
    Estimate,
    Kind,
    kind,
)

OTHER, CHECK, REPEAT = PRIORS[Kind.OTHER], PRIORS[Kind.CHECK], PRIORS[Kind.REPEAT]


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("mirror_entities", Kind.REPEAT),
        ("linear_pattern", Kind.REPEAT),
        ("circular_pattern", Kind.REPEAT),
        ("run_check", Kind.CHECK),
        ("create_line", Kind.OTHER),
        ("solve_status", Kind.OTHER),
        (None, Kind.OTHER),
    ],
)
def test_calls_are_sorted_by_what_they_cost(tool: str | None, expected: Kind) -> None:
    assert kind(tool) is expected


# --- What a call costs ------------------------------------------------------------------


def test_with_nothing_measured_a_call_costs_its_prior() -> None:
    estimate = Estimate()
    assert estimate.cost(Kind.REPEAT) == REPEAT
    assert estimate.cost(Kind.CHECK) == CHECK
    assert estimate.cost(None) == OTHER


def test_measured_calls_move_the_cost_from_the_prior_to_the_run_s_own() -> None:
    estimate = Estimate()
    for _ in range(5):
        estimate.measure(Kind.OTHER, 3.0)
    assert estimate.cost(Kind.OTHER) == pytest.approx((15.0 + PRIOR_WEIGHT * OTHER) / 10)
    assert estimate.cost(Kind.CHECK) == CHECK  # each kind by its own calls
    for _ in range(5):
        estimate.measure(Kind.CHECK, 0.5)
    # A call of unknown kind costs what the run's calls have, on average.
    assert estimate.cost(None) == pytest.approx((15.0 + 2.5 + PRIOR_WEIGHT * OTHER) / 15)


def test_the_estimate_is_rough_until_enough_calls_are_measured() -> None:
    estimate = Estimate()
    for _ in range(INFORMED - 1):
        estimate.measure(Kind.OTHER, 1.0)
    assert not estimate.informed
    estimate.measure(Kind.CHECK, 1.0)
    assert estimate.informed


# --- The figure -------------------------------------------------------------------------


def test_there_is_no_figure_without_a_plan_or_with_all_of_it_done() -> None:
    estimate = Estimate()
    assert estimate.figure(0.0) is None
    estimate.planned(2, None, None)
    estimate.called(Kind.OTHER)
    estimate.called(Kind.CHECK)  # without a split, any call counts
    assert estimate.figure(0.0) is None


def test_a_split_plan_prices_each_kind_and_counts_each_off() -> None:
    estimate = Estimate()
    estimate.planned(20, 2, 8)
    assert estimate.figure(0.0) == pytest.approx(2 * REPEAT + 8 * CHECK + 10 * OTHER)
    for _ in range(9):  # one more check than planned: no fewer than none left
        estimate.called(Kind.CHECK)
    assert estimate.figure(0.0) == pytest.approx(2 * REPEAT + 10 * OTHER)


def test_time_spent_waiting_counts_toward_the_next_call_only() -> None:
    estimate = Estimate()
    estimate.planned(10, None, None)
    assert estimate.figure(1.0) == pytest.approx(10 * OTHER - 1.0)
    # Claude has thought for longer than a call takes: the nine after it still take theirs.
    assert estimate.figure(60.0) == pytest.approx(9 * OTHER)


# --- What's shown -----------------------------------------------------------------------


def test_what_is_shown_counts_down_between_refreshes() -> None:
    estimate = Estimate()
    estimate.planned(10, None, None)
    assert estimate.shown(100.0, 0.0) == pytest.approx(10 * OTHER)
    assert estimate.shown(105.0, 5.0) == pytest.approx(10 * OTHER - 5)


def test_a_refresh_moves_what_is_shown_part_of_the_way_to_the_new_figure() -> None:
    estimate = Estimate()
    estimate.planned(20, None, None)
    estimate.shown(0.0, 0.0)  # 34 s
    for _ in range(5):  # calls far quicker than the prior
        estimate.measure(Kind.OTHER, 0.2)
        estimate.called(Kind.OTHER)
    # Before the refresh the countdown carries on; at it, it moves halfway.
    assert estimate.shown(REFRESH - 1, 0.0) == pytest.approx(20 * OTHER - (REFRESH - 1))
    figure = estimate.figure(0.0)
    assert figure is not None
    current = 20 * OTHER - REFRESH
    assert estimate.shown(REFRESH, 0.0) == pytest.approx(current + SMOOTHING * (figure - current))


def test_a_repeat_finishing_refreshes_what_is_shown_at_once() -> None:
    estimate = Estimate()
    estimate.planned(10, 1, None)
    estimate.shown(0.0, 0.0)
    estimate.measure(Kind.REPEAT, 20.0)
    estimate.called(Kind.REPEAT)
    shown = 9 * OTHER + REPEAT - 1.0
    figure = 9 * OTHER
    assert estimate.shown(1.0, 0.0) == pytest.approx(shown + SMOOTHING * (figure - shown))


def test_a_new_plan_or_a_figure_far_out_of_line_replaces_what_is_shown() -> None:
    estimate = Estimate()
    estimate.planned(10, None, None)
    estimate.shown(0.0, 0.0)
    estimate.planned(100, None, None)
    assert estimate.shown(1.0, 0.0) == pytest.approx(100 * OTHER)  # a new plan: at once
    for _ in range(30):  # the run turns out three times as slow as the prior
        estimate.measure(Kind.OTHER, 5.1)
    figure = estimate.figure(0.0)
    assert figure is not None
    assert estimate.shown(2.0, 0.0) == pytest.approx(figure)  # stale: at once


def test_what_is_shown_never_goes_below_nothing() -> None:
    estimate = Estimate()
    estimate.planned(1, None, None)
    estimate.shown(0.0, 0.0)
    assert estimate.shown(30.0, 30.0) == 0.0
