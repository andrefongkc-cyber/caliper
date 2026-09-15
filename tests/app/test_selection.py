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


def test_click_selects_the_outline_and_empty_space_clears(window, driver, shapes) -> None:
    rect, _ = shapes
    driver.click(0, 20)
    assert window.session.selection == {rect}
    driver.click(50, 25)  # inside the rectangle, away from its edges: a miss
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
    assert window.session.hover is None
    driver.tool("Line")
    driver.move(100, 10)
    assert window.session.hover is None


@pytest.mark.bus(stub_unbuilt=True)
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


@pytest.mark.bus(stub_unbuilt=True)
def test_escape_during_a_drag_moves_nothing(window, driver, bus, shapes) -> None:
    driver.move(0, 20)
    driver.press(0, 20)
    driver.move(40, 40)
    driver.key(Qt.Key.Key_Escape)
    driver.release(40, 40)
    assert bus.sent == []
    assert window.controller.active.name == "Select"


@pytest.mark.bus(stub_unbuilt=True)
def test_delete_sends_the_selection(window, driver, bus, shapes) -> None:
    rect, circle = shapes
    window.session.set_selection(frozenset({circle, rect}))
    window.delete_action.trigger()
    assert bus.sent == [DeleteEntities(ids=tuple(sorted((rect, circle))))]


@pytest.mark.bus(missing=("DeleteEntities",))
def test_delete_reports_the_missing_engine_piece_instead_of_crashing(window, shapes) -> None:
    rect, _ = shapes
    window.session.set_selection(frozenset({rect}))
    window.delete_action.trigger()
    assert window.statusBar().currentMessage() == "Delete isn't in the engine yet"
    assert rect in window.session.document.entities


def test_delete_is_disabled_with_nothing_selected(window, shapes) -> None:
    assert not window.delete_action.isEnabled()
    window.session.set_selection(frozenset({shapes[0]}))
    assert window.delete_action.isEnabled()


@pytest.mark.bus(missing=("entities_in_box",))
def test_box_select_reports_the_missing_engine_piece(window, driver, shapes) -> None:
    driver.drag([(-20, -20), (60, 40), (120, 70)])
    assert window.statusBar().currentMessage() == "Box selection isn't in the engine yet"
    assert not window.controller.active.busy


def test_undo_drops_deleted_ids_from_the_selection(window, shapes) -> None:
    _, circle = shapes
    window.session.set_selection(frozenset({circle}))
    window.undo_action.trigger()
    assert window.session.selection == set()
