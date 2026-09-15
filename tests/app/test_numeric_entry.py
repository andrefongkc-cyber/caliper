"""Typing exact values while drawing."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.contracts.commands import CreateCircle, CreateLine, CreateRectangle
from caliper.contracts.document import Point2


def type_keys(qtbot, text: str) -> None:
    """Type into whatever has focus, the way a user would."""
    for char in text:
        if char == "\t":
            qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        elif char == "\n":
            qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Return)
        else:
            qtbot.keyClicks(QApplication.focusWidget(), char)


def start(driver, tool: str, x: float, y: float, towards: tuple[float, float]) -> None:
    driver.canvas.setFocus()
    driver.tool(tool)
    driver.click(x, y)
    driver.move(*towards)


def test_rectangle_from_typed_width_and_height(window, driver, bus, qtbot) -> None:
    start(driver, "Rectangle", 10, 10, towards=(30, 30))
    type_keys(qtbot, "120\t50\n")
    assert bus.sent == [CreateRectangle(corner=Point2(x=10.0, y=10.0), width=120.0, height=50.0)]
    assert not window.canvas.entry.isVisible()
    assert not window.controller.active.busy


def test_first_digit_opens_the_entry_with_that_digit(window, driver, qtbot) -> None:
    start(driver, "Rectangle", 0, 0, towards=(20, 20))
    type_keys(qtbot, "7")
    entry = window.canvas.entry
    assert entry.isVisible()
    assert [f.text() for f in entry.fields] == ["7", ""]
    assert QApplication.focusWidget() is entry.fields[0]


def test_typed_sizes_extend_toward_the_pointer(driver, bus, qtbot) -> None:
    start(driver, "Rectangle", 50, 50, towards=(20, 10))  # pointer down-left of the corner
    type_keys(qtbot, "30\t20\n")
    assert bus.sent == [CreateRectangle(corner=Point2(x=20.0, y=30.0), width=30.0, height=20.0)]


def test_preview_follows_typed_values_before_commit(window, driver, bus, qtbot) -> None:
    start(driver, "Rectangle", 0, 0, towards=(20, 20))
    type_keys(qtbot, "80")
    tool = window.controller.active
    assert tool.current == Point2(x=80.0, y=20.0)  # width typed, height still follows pointer
    assert bus.sent == []


def test_untyped_fields_follow_the_pointer(driver, bus, qtbot) -> None:
    start(driver, "Rectangle", 0, 0, towards=(40, 25))
    type_keys(qtbot, "90\n")
    assert bus.sent == [CreateRectangle(corner=Point2(x=0.0, y=0.0), width=90.0, height=25.0)]


def test_escape_closes_the_entry_then_cancels_the_tool(window, driver, bus, qtbot) -> None:
    start(driver, "Rectangle", 0, 0, towards=(20, 20))
    type_keys(qtbot, "5")
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert not window.canvas.entry.isVisible()
    assert window.controller.active.busy
    assert window.controller.active.typed is None
    driver.key(Qt.Key.Key_Escape)
    assert not window.controller.active.busy
    assert bus.sent == []


def test_invalid_values_keep_the_entry_open(window, driver, bus, qtbot) -> None:
    start(driver, "Circle", 0, 0, towards=(10, 0))
    type_keys(qtbot, "-4\n")
    assert bus.sent == []
    assert window.canvas.entry.isVisible()
    assert "above 0" in window.statusBar().currentMessage()


def test_text_that_isnt_a_number_is_marked(window, driver, qtbot) -> None:
    start(driver, "Circle", 0, 0, towards=(10, 0))
    type_keys(qtbot, "1-")
    assert window.canvas.entry.fields[0].property("invalid") is True


def test_circle_radius(driver, bus, qtbot) -> None:
    start(driver, "Circle", 5, 5, towards=(9, 9))
    type_keys(qtbot, "12.5\n")
    assert bus.sent == [CreateCircle(center=Point2(x=5.0, y=5.0), radius=12.5)]


def test_line_length_and_angle_are_exact_at_right_angles(driver, bus, qtbot) -> None:
    start(driver, "Line", 0, 0, towards=(10, 10))
    type_keys(qtbot, "40\t90\n")
    assert bus.sent == [CreateLine(start=Point2(x=0.0, y=0.0), end=Point2(x=0.0, y=40.0))]


def test_line_length_keeps_the_pointer_direction(driver, bus, qtbot) -> None:
    start(driver, "Line", 0, 0, towards=(30, 0))
    type_keys(qtbot, "25\n")
    assert bus.sent == [CreateLine(start=Point2(x=0.0, y=0.0), end=Point2(x=25.0, y=0.0))]


def test_clicking_while_values_are_typed_confirms_them(driver, bus, qtbot) -> None:
    start(driver, "Rectangle", 0, 0, towards=(20, 20))
    type_keys(qtbot, "60\t30")
    driver.click(20, 20)
    assert bus.sent == [CreateRectangle(corner=Point2(x=0.0, y=0.0), width=60.0, height=30.0)]


def test_digits_do_nothing_when_no_operation_is_in_progress(window, driver, bus, qtbot) -> None:
    driver.canvas.setFocus()
    driver.tool("Rectangle")
    driver.key(Qt.Key.Key_5)
    assert not window.canvas.entry.isVisible()
    driver.tool("Select")
    driver.key(Qt.Key.Key_5)
    assert not window.canvas.entry.isVisible()


def test_shortcut_letters_type_into_the_field_instead_of_switching_tools(
    window, driver, qtbot
) -> None:
    start(driver, "Rectangle", 0, 0, towards=(20, 20))
    type_keys(qtbot, "1")
    qtbot.keyClicks(QApplication.focusWidget(), "l")
    assert window.controller.active.name == "Rectangle"
    assert window.canvas.entry.fields[0].text() == "1l"


def test_the_hint_mentions_typing(window, driver) -> None:
    start(driver, "Rectangle", 0, 0, towards=(20, 20))
    assert "type width and height" in window.hint_label.text()


def test_tab_wraps_within_the_entry(window, driver, qtbot) -> None:
    start(driver, "Rectangle", 0, 0, towards=(20, 20))
    type_keys(qtbot, "1\t2\t")
    fields = window.canvas.entry.fields
    assert QApplication.focusWidget() is fields[0]
    assert fields[0].selectedText() == "1"
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Backtab)
    assert QApplication.focusWidget() is fields[1]
