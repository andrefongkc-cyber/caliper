"""Selection, hover, drag-to-move, box select, and delete."""

import pytest
from PySide6.QtCore import Qt

from caliper.contracts.commands import CreateCircle, CreateRectangle, DeleteEntities, MoveEntities
from caliper.contracts.document import Point2

SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def shapes(window) -> tuple[str, str]:
    session = window.session
    rect = session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50))
    circle = session.execute(CreateCircle(center=Point2(x=200, y=25), radius=25))
    bus = session.bus
    bus.sent.clear()
    return rect.created_ids[0], circle.created_ids[0]


def test_clicking_a_shape_or_its_inside_selects_it_and_empty_space_clears(
    window, driver, shapes
) -> None:
    rect, _ = shapes
    driver.click(0, 20)
    assert window.session.selection == {rect}
    driver.click(50, 25)  # inside the rectangle: closed shapes pick from their interior too
    assert window.session.selection == {rect}
    driver.click(-40, -40)  # outside everything
    assert window.session.selection == set()


def test_shift_click_toggles(window, driver, shapes) -> None:
    rect, circle = shapes
    driver.click(0, 20)
    driver.click(225, 25, SHIFT)
    assert window.session.selection == {rect, circle}
    driver.click(0, 20, SHIFT)
    assert window.session.selection == {circle}


def test_plain_click_inside_a_multi_selection_narrows_it(window, driver, shapes) -> None:
    rect, circle = shapes
    window.session.set_selection(frozenset({rect, circle}))
    driver.click(225, 25)
    assert window.session.selection == {circle}


def test_hover_follows_the_pointer_in_select_mode_only(window, driver, shapes) -> None:
    rect, _ = shapes
    driver.move(100, 10)
    assert window.session.hover == rect
    driver.move(60, 25)
    assert window.session.hover == rect  # inside the rectangle counts as over it
    driver.move(-40, -40)
    assert window.session.hover is None
    driver.tool("Line")
    driver.move(100, 10)
    assert window.session.hover is None


def test_drag_moves_the_selection_with_one_command_on_release(window, driver, bus, shapes) -> None:
    rect, _ = shapes
    driver.move(0, 20)
    driver.press(0, 20)
    for x in (5, 10, 20, 30):
        driver.move(x, 30)
        assert bus.sent == []
    assert window.controller.active.busy
    driver.release(30, 30)
    assert bus.sent == [MoveEntities(ids=(rect,), dx=30.0, dy=10.0)]


def test_escape_during_a_drag_moves_nothing(window, driver, bus, shapes) -> None:
    driver.move(0, 20)
    driver.press(0, 20)
    driver.move(40, 40)
    driver.key(Qt.Key.Key_Escape)
    driver.release(40, 40)
    assert bus.sent == []
    assert window.controller.active.name == "Select"


def test_delete_sends_the_selection(window, driver, bus, shapes) -> None:
    rect, circle = shapes
    window.session.set_selection(frozenset({circle, rect}))
    window.delete_action.trigger()
    assert bus.sent == [DeleteEntities(ids=tuple(sorted((rect, circle))))]


def test_delete_is_disabled_with_nothing_selected(window, shapes) -> None:
    assert not window.delete_action.isEnabled()
    window.session.set_selection(frozenset({shapes[0]}))
    assert window.delete_action.isEnabled()


def test_undo_drops_deleted_ids_from_the_selection(window, shapes) -> None:
    _, circle = shapes
    window.session.set_selection(frozenset({circle}))
    window.undo_action.trigger()
    assert window.session.selection == set()


def test_delete_removes_the_selection_and_undo_restores_it(window, shapes) -> None:
    rect, circle = shapes
    window.session.set_selection(frozenset({rect, circle}))
    window.delete_action.trigger()
    assert window.session.document.entities == {}
    assert window.session.selection == set()
    window.undo_action.trigger()
    assert set(window.session.document.entities) == {rect, circle}


def test_box_select_left_to_right_selects_only_whats_inside(window, driver, shapes) -> None:
    rect, _ = shapes
    driver.drag([(-20, -20), (60, 40), (130, 70)])  # covers the rectangle, not the circle
    assert window.session.selection == {rect}
    assert not window.controller.active.busy


def test_box_select_right_to_left_also_selects_what_it_touches(window, driver, shapes) -> None:
    rect, circle = shapes
    driver.drag([(240, 30), (150, 30), (90, 30)])  # starts outside, crosses circle and rectangle
    assert window.session.selection == {rect, circle}


def test_dragging_from_inside_a_shape_moves_it(window, driver, shapes) -> None:
    rect, _ = shapes
    driver.move(50, 25)
    driver.press(50, 25)
    driver.move(60, 35)
    driver.release(60, 35)
    assert window.session.document.entities[rect].corner == Point2(x=10.0, y=10.0)


def test_command_drag_boxes_from_inside_a_shape(window, driver, shapes) -> None:
    rect, circle = shapes
    command = Qt.KeyboardModifier.ControlModifier  # ⌘ on macOS
    driver.move(50, 25)
    driver.press(50, 25, command)  # inside the rectangle
    driver.move(10, 10)
    driver.move(-30, -20)  # right to left: a crossing box
    driver.release(-30, -20, command)
    assert window.session.selection == {rect}
    assert circle not in window.session.selection
    assert window.session.document.entities[rect].corner == Point2(x=0.0, y=0.0)  # not moved


def test_drag_moves_the_selection(window, driver, shapes) -> None:
    rect, _ = shapes
    driver.move(0, 20)
    driver.press(0, 20)
    driver.move(15, 25)
    driver.move(30, 30)
    driver.release(30, 30)
    assert window.session.document.entities[rect].corner == Point2(x=30.0, y=10.0)
    assert window.undo_action.text() == "Undo Move Rectangle"
