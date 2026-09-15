"""Alignment guides from acquired feature points."""

import pytest
from PySide6.QtCore import QPointF, Qt

from caliper.app.tools.base import SnapKind
from caliper.app.viewport.inference import MAX_ACQUIRED, acquire, align
from caliper.contracts.commands import CreateLine, CreateRectangle
from caliper.contracts.document import Point2

P = Point2


def test_align_snaps_each_axis_independently() -> None:
    acquired = [P(x=100, y=0), P(x=0, y=50)]
    point, guides = align(P(x=99.6, y=49.7), P(x=100, y=50), acquired, tolerance=1)
    assert point == P(x=100, y=50)
    assert guides == ((P(x=100, y=0), point), (P(x=0, y=50), point))


def test_align_keeps_the_base_when_nothing_is_in_reach() -> None:
    point, guides = align(P(x=30.4, y=20.2), P(x=30, y=20), [P(x=100, y=0)], tolerance=1)
    assert point == P(x=30, y=20)
    assert guides == ()


def test_align_picks_the_closest_candidate() -> None:
    acquired = [P(x=10.0, y=0), P(x=10.6, y=5)]
    point, _ = align(P(x=10.5, y=90), P(x=10.5, y=90), acquired, tolerance=1)
    assert point.x == 10.6


def test_acquire_is_most_recent_first_and_bounded() -> None:
    points: list[Point2] = []
    for i in range(MAX_ACQUIRED + 2):
        points = acquire(points, P(x=i, y=0))
    points = acquire(points, P(x=MAX_ACQUIRED, y=0))
    assert len(points) == MAX_ACQUIRED
    assert points[0] == P(x=MAX_ACQUIRED, y=0)
    assert len(set(points)) == len(points)


@pytest.mark.bus(complete_queries=True)
def test_hovering_a_corner_then_moving_in_line_snaps_and_guides(window, driver) -> None:
    # Off-grid sizes (the grid is 5 mm here), so only a guide can produce these values.
    window.session.execute(CreateRectangle(corner=P(x=0, y=0), width=101.3, height=51.3))
    driver.move(101.3, 51.3)  # hover the top-right corner: acquired
    assert window.canvas.acquired == [P(x=101.3, y=51.3)]
    driver.move(160.3, 51.7)  # out to the right, roughly level with it
    pointer = window.canvas._pointer
    assert pointer.snap is SnapKind.GUIDE
    assert pointer.point == P(x=160.0, y=51.3)  # x on the grid, y on the guide
    assert pointer.guides == ((P(x=101.3, y=51.3), pointer.point),)


@pytest.mark.bus(complete_queries=True)
def test_drawing_uses_the_aligned_point(window, driver, bus) -> None:
    window.session.execute(CreateRectangle(corner=P(x=0, y=0), width=101.3, height=51.3))
    bus.sent.clear()
    driver.tool("Line")
    driver.move(101.3, 51.3)
    driver.move(101.6, 90)  # straight above the corner
    driver.press(101.6, 90)
    driver.move(101.1, 130)
    driver.release(101.1, 130)
    (line,) = bus.sent
    assert isinstance(line, CreateLine)
    assert (line.start.x, line.end.x) == (101.3, 101.3)


@pytest.mark.bus(complete_queries=True)
def test_option_suspends_guides_too(window, driver) -> None:
    window.session.execute(CreateRectangle(corner=P(x=0, y=0), width=100, height=50))
    driver.move(100, 50)
    alt = Qt.KeyboardModifier.AltModifier
    pointer = window.canvas.pointer_at(QPointF(driver.at(160.3, 50.4)), alt)
    assert pointer.snap is SnapKind.NONE
    assert pointer.guides == ()


@pytest.mark.bus(complete_queries=True)
def test_acquired_points_are_forgotten_when_the_document_changes(window, driver) -> None:
    window.session.execute(CreateRectangle(corner=P(x=0, y=0), width=100, height=50))
    driver.move(100, 50)
    window.session.execute(CreateRectangle(corner=P(x=300, y=0), width=10, height=10))
    assert window.canvas.acquired == []


def test_without_point_queries_nothing_is_acquired(window, driver) -> None:
    window.session.execute(CreateRectangle(corner=P(x=0, y=0), width=100, height=50))
    driver.move(100, 50)
    assert window.canvas.acquired == []
