"""Tool modes: state-machine transitions and the commands each tool sends."""

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from caliper.contracts.commands import CreateArc, CreateCircle, CreateLine, CreateRectangle
from caliper.contracts.document import Point2


def test_starts_in_select(window) -> None:
    assert window.controller.active.name == "Select"
    assert window.tool_actions["Select"].isChecked()


@pytest.mark.parametrize(
    ("key", "name"),
    [
        (Qt.Key.Key_L, "Line"),
        (Qt.Key.Key_C, "Circle"),
        (Qt.Key.Key_A, "Arc"),
        (Qt.Key.Key_R, "Rectangle"),
        (Qt.Key.Key_D, "Dimension"),
    ],
)
def test_shortcut_keys_switch_tools(window, driver, key: Qt.Key, name: str) -> None:
    driver.canvas.setFocus()
    driver.key(key)
    assert window.controller.active.name == name
    assert window.tool_actions[name].isChecked()
    driver.key(Qt.Key.Key_S)
    assert window.controller.active.name == "Select"


def test_escape_cancels_the_operation_first_then_leaves_the_tool(window, driver, bus) -> None:
    driver.tool("Rectangle")
    driver.click(0, 0)
    assert window.controller.active.busy
    driver.key(Qt.Key.Key_Escape)
    assert window.controller.active.name == "Rectangle"
    assert not window.controller.active.busy
    driver.key(Qt.Key.Key_Escape)
    assert window.controller.active.name == "Select"
    assert bus.sent == []


def test_right_click_cancels(window, driver, qtbot) -> None:
    driver.tool("Line")
    driver.click(0, 0)
    qtbot.mouseClick(driver.canvas, Qt.MouseButton.RightButton, pos=driver.at(10, 10))
    assert not window.controller.active.busy


def test_switching_tools_abandons_the_operation(window, driver, bus) -> None:
    driver.tool("Circle")
    driver.click(0, 0)
    driver.tool("Line")
    driver.tool("Circle")
    assert not window.controller.active.busy
    driver.click(20, 0)
    assert bus.sent == []


def test_hint_follows_the_phase(window, driver) -> None:
    driver.tool("Rectangle")
    first = window.hint_label.text()
    driver.click(0, 0)
    assert window.hint_label.text() != first
    assert "opposite corner" in window.hint_label.text()


def test_rectangle_drag_sends_one_command_on_release(window, driver, bus) -> None:
    driver.tool("Rectangle")
    driver.move(100, 50)
    driver.press(100, 50)
    for x, y in ((80, 40), (40, 20), (0, 0)):
        driver.move(x, y)
        assert bus.sent == []  # the rubber band is a preview, not a command
    driver.release(0, 0)
    assert bus.sent == [CreateRectangle(corner=Point2(x=0.0, y=0.0), width=100.0, height=50.0)]
    assert not window.controller.active.busy


def test_rectangle_click_click(driver, bus) -> None:
    driver.tool("Rectangle")
    driver.click(10, 10)
    driver.move(40, 30)
    driver.click(40, 30)
    assert bus.sent == [CreateRectangle(corner=Point2(x=10.0, y=10.0), width=30.0, height=20.0)]


def test_zero_size_rectangle_sends_nothing_and_keeps_waiting(window, driver, bus) -> None:
    driver.tool("Rectangle")
    driver.drag([(10, 10), (50, 10)])  # zero height
    assert bus.sent == []
    assert window.controller.active.busy


def test_quick_second_click_counts_as_a_press(driver, bus) -> None:
    # A real fast second click arrives as DblClick then Release, with no Press.
    driver.tool("Line")
    driver.click(0, 0)
    driver.move(25, 0)
    position = QPointF(driver.at(25, 0))
    left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    for kind, buttons in (
        (QEvent.Type.MouseButtonDblClick, left),
        (QEvent.Type.MouseButtonRelease, none),
    ):
        event = QMouseEvent(
            kind,
            position,
            driver.canvas.mapToGlobal(position),
            left,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(driver.canvas, event)
    assert bus.sent == [CreateLine(start=Point2(x=0.0, y=0.0), end=Point2(x=25.0, y=0.0))]


def test_line(driver, bus) -> None:
    driver.tool("Line")
    driver.drag([(0, 0), (30, 20), (60, 40)])
    assert bus.sent == [CreateLine(start=Point2(x=0.0, y=0.0), end=Point2(x=60.0, y=40.0))]


def test_circle(driver, bus) -> None:
    driver.tool("Circle")
    driver.drag([(20, 20), (50, 60)])
    assert bus.sent == [CreateCircle(center=Point2(x=20.0, y=20.0), radius=50.0)]


def test_arc_is_center_start_end_counter_clockwise(window, driver, bus) -> None:
    driver.tool("Arc")
    driver.click(0, 0)
    driver.click(50, 0)
    assert window.controller.active.busy
    driver.click(0, 50)
    assert bus.sent == [
        CreateArc(center=Point2(x=0.0, y=0.0), radius=50.0, start_angle=0.0, sweep_angle=90.0)
    ]


def test_arc_ending_clockwise_of_the_start_sweeps_the_long_way(driver, bus) -> None:
    driver.tool("Arc")
    driver.click(0, 0)
    driver.click(50, 0)
    driver.click(0, -50)
    (command,) = bus.sent
    assert isinstance(command, CreateArc)
    assert command.sweep_angle == 270.0


def test_created_shapes_land_in_the_document(window, driver) -> None:
    driver.tool("Rectangle")
    driver.drag([(0, 0), (100, 50)])
    (entity,) = window.session.document.entities.values()
    assert (entity.width, entity.height) == (100.0, 50.0)
    assert window.undo_action.text() == "Undo Create Rectangle"


def test_option_suspends_grid_snapping(driver, bus) -> None:
    driver.tool("Line")
    alt = Qt.KeyboardModifier.AltModifier
    a, b = driver.at(0, 0), driver.at(12.2, 0)
    driver.qtbot.mousePress(driver.canvas, Qt.MouseButton.LeftButton, alt, a)
    driver.qtbot.mouseRelease(driver.canvas, Qt.MouseButton.LeftButton, alt, b)
    (command,) = bus.sent
    assert isinstance(command, CreateLine)
    assert command.end.x % 5 != 0  # not snapped to the 5 mm grid
