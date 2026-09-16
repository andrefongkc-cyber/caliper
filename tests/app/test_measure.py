"""Measure tool: two feature points, the distance from the query, and its display."""

import pytest
from PySide6.QtCore import Qt

from caliper.app.tools.measure import MeasurePhase
from caliper.contracts.commands import CreateRectangle
from caliper.contracts.document import Point2


@pytest.fixture
def plate(window) -> None:
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    window.session.bus.sent.clear()


def test_measuring_two_corners_shows_the_width(window, driver, bus, plate) -> None:
    driver.tool("Measure")
    driver.click(0, 0)
    assert window.controller.active.phase is MeasurePhase.SECOND
    driver.click(120, 0)
    tool = window.controller.active
    assert tool.phase is MeasurePhase.SHOWN
    assert tool.result is not None
    assert tool.result.value == pytest.approx(120.0)
    assert window.statusBar().currentMessage() == ("Distance 120.000 mm · dx 120.000 · dy 0.000")
    assert bus.sent == []  # measuring never changes the document


def test_diagonal_reports_dx_and_dy(window, driver, plate) -> None:
    driver.tool("Measure")
    driver.click(0, 0)
    driver.click(120, 50)
    assert window.statusBar().currentMessage() == "Distance 130.000 mm · dx 120.000 · dy 50.000"


def test_the_result_is_painted_on_the_canvas(window, driver, plate) -> None:
    driver.tool("Measure")
    driver.click(0, 0)
    driver.click(120, 50)
    from caliper.app import theme

    image = window.canvas.grab().toImage()
    x, y = window.canvas.view.to_widget(Point2(x=30, y=12.5))  # on the measured diagonal
    colours = {
        image.pixelColor(round(x) + dx, round(y) + dy).name()
        for dx in range(-2, 3)
        for dy in range(-2, 3)
    }
    assert theme.SNAP.name() in colours


def test_escape_clears_the_measurement_then_leaves_the_tool(window, driver, plate) -> None:
    driver.tool("Measure")
    driver.click(0, 0)
    driver.click(120, 0)
    driver.key(Qt.Key.Key_Escape)
    assert window.controller.active.name == "Measure"
    assert window.controller.active.result is None
    driver.key(Qt.Key.Key_Escape)
    assert window.controller.active.name == "Select"


def test_a_click_off_any_point_explains_what_to_click(window, driver, plate) -> None:
    driver.tool("Measure")
    driver.click(30, 40)  # inside the plate, away from corners, midpoints, and the center
    assert window.controller.active.phase is MeasurePhase.FIRST
    assert "corner, center, end, or midpoint" in window.statusBar().currentMessage()


def test_a_new_click_after_a_result_starts_over(window, driver, plate) -> None:
    driver.tool("Measure")
    driver.click(0, 0)
    driver.click(120, 0)
    driver.click(120, 50)
    tool = window.controller.active
    assert tool.phase is MeasurePhase.SECOND
    assert tool.result is None


def test_measure_sits_in_its_own_tool_bar_group(window) -> None:
    actions = window.tool_bar.actions()
    names = [a.text() if not a.isSeparator() else "|" for a in actions]
    assert names[: names.index("Zoom to Fit")] == [
        "Select",
        "|",
        "Line",
        "Circle",
        "Arc",
        "Rectangle",
        "Fillet",
        "Dimension",
        "|",
        "Measure",
        "|",
    ]
