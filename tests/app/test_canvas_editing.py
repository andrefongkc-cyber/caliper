"""Double-click an outline to edit its dimension in place."""

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from caliper.contracts.commands import CreateCircle, CreateLine, CreateRectangle, ModifyEntity
from caliper.contracts.document import Point2

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton


def double_click(driver, x: float, y: float) -> None:
    """What macOS sends for a double-click: press, release, double-click, release."""
    driver.move(x, y)
    position = QPointF(driver.at(x, y))
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, LEFT),
        (QEvent.Type.MouseButtonRelease, NONE),
        (QEvent.Type.MouseButtonDblClick, LEFT),
        (QEvent.Type.MouseButtonRelease, NONE),
    ):
        event = QMouseEvent(
            kind,
            position,
            driver.canvas.mapToGlobal(position),
            LEFT,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(driver.canvas, event)


def type_and_return(qtbot, text: str) -> None:
    field = QApplication.focusWidget()
    qtbot.keyClicks(field, text)
    qtbot.keyClick(field, Qt.Key.Key_Return)


def add(window, command) -> str:
    (entity_id,) = window.session.execute(command).created_ids
    window.session.bus.sent.clear()
    return entity_id


def test_double_click_top_edge_edits_width(window, driver, bus, qtbot) -> None:
    rect = add(window, CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50))
    double_click(driver, 50, 50)
    entry = window.canvas.entry
    assert entry.isVisible()
    assert [f.objectName() for f in entry.fields] == ["width"]
    assert entry.fields[0].text() == "100"
    assert entry.fields[0].selectedText() == "100"  # typing replaces the value
    type_and_return(qtbot, "120")
    assert bus.sent == [ModifyEntity(id=rect, changes={"width": 120.0})]
    assert window.undo_action.text() == "Undo Change Width"
    assert not entry.isVisible()
    assert window.session.selection == {rect}


def test_double_click_side_edits_height(window, driver, bus, qtbot) -> None:
    rect = add(window, CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50))
    double_click(driver, 100, 25)
    type_and_return(qtbot, "80")
    assert bus.sent == [ModifyEntity(id=rect, changes={"height": 80.0})]


def test_double_click_circle_edits_radius(window, driver, bus, qtbot) -> None:
    circle = add(window, CreateCircle(center=Point2(x=0, y=0), radius=20))
    double_click(driver, 20, 0)
    type_and_return(qtbot, "12.5")
    assert bus.sent == [ModifyEntity(id=circle, changes={"radius": 12.5})]


def test_rejected_value_keeps_the_entry_open_and_marked(window, driver, bus, qtbot) -> None:
    add(window, CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50))
    before = window.session.document
    double_click(driver, 50, 50)
    type_and_return(qtbot, "-5")
    assert window.session.document == before
    entry = window.canvas.entry
    assert entry.isVisible()
    assert entry.fields[0].property("invalid") is True
    assert window.statusBar().currentMessage() == "width must be greater than 0"


def test_escape_abandons_the_edit(window, driver, bus, qtbot) -> None:
    add(window, CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50))
    double_click(driver, 50, 50)
    qtbot.keyClicks(QApplication.focusWidget(), "999")
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert not window.canvas.entry.isVisible()
    assert bus.sent == []


def test_lines_and_empty_space_open_nothing(window, driver, bus) -> None:
    add(window, CreateLine(start=Point2(x=0, y=0), end=Point2(x=50, y=0)))
    double_click(driver, 25, 0)
    assert not window.canvas.entry.isVisible()
    double_click(driver, 200, 200)
    assert not window.canvas.entry.isVisible()
    assert bus.sent == []


def test_double_click_in_a_drawing_tool_still_places_points(window, driver, bus) -> None:
    driver.tool("Rectangle")
    driver.click(0, 0)
    double_click(driver, 40, 30)
    assert bus.sent == [CreateRectangle(corner=Point2(x=0.0, y=0.0), width=40.0, height=30.0)]
    assert not window.canvas.entry.isVisible()
