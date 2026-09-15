"""Command palette: find by name, run actions, and fill typed command forms."""

import typing

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.app.command_schema import build, command_specs, matches
from caliper.contracts.commands import (
    Command,
    CreateCircle,
    CreateRectangle,
    DeleteEntities,
    MoveEntities,
)
from caliper.contracts.document import EntityId, Point2


def open_palette(window, qtbot) -> None:
    window.canvas.setFocus()
    window.palette_action.trigger()
    assert window.palette.isVisible()
    assert QApplication.focusWidget() is window.palette.search


def type_keys(qtbot, text: str) -> None:
    for char in text:
        widget = QApplication.focusWidget()
        if char == "\t":
            qtbot.keyClick(widget, Qt.Key.Key_Tab)
        elif char == "\n":
            qtbot.keyClick(widget, Qt.Key.Key_Return)
        else:
            qtbot.keyClicks(widget, char)


# --- Schema (no Qt) -----------------------------------------------------------------------


def test_every_command_type_is_listed_or_deliberately_left_out() -> None:
    listed = {spec.type for spec in command_specs()}
    left_out = set(typing.get_args(Command)) - listed
    assert {t.__name__ for t in left_out} == {
        "CreateDistanceDimension",  # needs feature references: the Dimension tool picks them
        "CreateRadialDimension",
        "ModifyEntity",  # the properties panel and on-canvas editing
    }


def test_rectangle_form_fields_come_from_the_dataclass() -> None:
    (spec,) = [s for s in command_specs() if s.type is CreateRectangle]
    assert spec.title == "Create Rectangle"
    assert [f.path for f in spec.fields] == ["corner.x", "corner.y", "width", "height"]
    command = build(spec, {"corner.x": 1, "corner.y": 2, "width": 3, "height": 4}, frozenset())
    assert command == CreateRectangle(corner=Point2(x=1, y=2), width=3, height=4)


def test_selection_commands_take_ids_from_the_selection() -> None:
    (spec,) = [s for s in command_specs() if s.type is MoveEntities]
    ids = frozenset({EntityId("e2"), EntityId("e1")})
    assert build(spec, {"dx": 5, "dy": 0}, ids) == MoveEntities(ids=("e1", "e2"), dx=5, dy=0)


@pytest.mark.parametrize(
    ("query", "title", "hit"),
    [
        ("rect", "Create Rectangle", True),
        ("rect cre", "Create Rectangle", True),
        ("circ", "Create Rectangle", False),
        ("", "Anything", True),
    ],
)
def test_matching(query: str, title: str, hit: bool) -> None:
    assert matches(query, title) is hit


# --- Palette ------------------------------------------------------------------------------


def test_cmd_k_rect_then_values_creates_one_rectangle(window, bus, qtbot) -> None:
    open_palette(window, qtbot)
    type_keys(qtbot, "create rect\n")
    assert window.palette.stack.currentIndex() == 1
    assert QApplication.focusWidget().objectName() == "width"  # first field without a default
    type_keys(qtbot, "120\t50\n")
    assert bus.sent == [CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0)]
    assert not window.palette.isVisible()
    assert QApplication.focusWidget() is window.canvas


def test_shortcut_opens_the_palette(window, driver, qtbot) -> None:
    driver.canvas.setFocus()
    qtbot.keyClick(window.canvas, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert window.palette.isVisible()


def test_typing_filters_and_shows_shortcuts(window, qtbot) -> None:
    open_palette(window, qtbot)
    type_keys(qtbot, "circle")
    assert window.palette.visible_titles() == ["Circle", "Create Circle"]
    from caliper.app.palette import DETAIL_ROLE

    assert window.palette.results.item(0).data(DETAIL_ROLE) == "C"
    assert window.palette.results.item(1).data(DETAIL_ROLE) == "center, radius"


def test_running_an_action_switches_tools(window, qtbot) -> None:
    open_palette(window, qtbot)
    type_keys(qtbot, "arc")
    qtbot.keyClick(window.palette.search, Qt.Key.Key_Return)
    assert window.controller.active.name == "Arc"
    assert not window.palette.isVisible()


def test_arrow_keys_skip_disabled_actions(window, qtbot) -> None:
    window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    open_palette(window, qtbot)
    type_keys(qtbot, "do")  # Undo Create Circle is enabled; Redo is not
    assert window.palette.visible_titles() == ["Undo Create Circle", "Redo"]
    qtbot.keyClick(window.palette.search, Qt.Key.Key_Down)
    assert window.palette.results.currentRow() == 0  # wrapped past the disabled Redo
    qtbot.keyClick(window.palette.search, Qt.Key.Key_Return)
    assert window.session.document.entities == {}


def test_rejected_command_marks_the_field(window, bus, qtbot) -> None:
    open_palette(window, qtbot)
    type_keys(qtbot, "create circle\n")
    type_keys(qtbot, "-3\n")
    assert window.palette.isVisible()
    assert window.palette.form_fields["radius"].property("invalid") is True
    assert "radius" in window.palette.error.text()
    assert window.session.document.entities == {}


def test_empty_field_is_caught_before_the_bus(window, bus, qtbot) -> None:
    open_palette(window, qtbot)
    type_keys(qtbot, "create circle\n\n")
    assert bus.sent == []
    assert window.palette.error.text() == "Enter a number"


def test_escape_goes_back_then_closes(window, qtbot) -> None:
    open_palette(window, qtbot)
    type_keys(qtbot, "create circle\n")
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert window.palette.stack.currentIndex() == 0
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert not window.palette.isVisible()


def test_selection_commands_need_a_selection(window, qtbot) -> None:
    open_palette(window, qtbot)
    type_keys(qtbot, "move entities\n")
    assert window.palette.stack.currentIndex() == 0
    assert "select something first" in window.palette.error.text()


def test_delete_command_with_no_fields_runs_immediately(window, bus, qtbot) -> None:
    (circle,) = window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5)).created_ids
    window.session.set_selection(frozenset({circle}))
    bus.sent.clear()
    open_palette(window, qtbot)
    type_keys(qtbot, "delete entities\n")
    assert bus.sent == [DeleteEntities(ids=(circle,))]


def test_titles_starting_with_the_query_rank_first(window, qtbot) -> None:
    window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    open_palette(window, qtbot)
    type_keys(qtbot, "cre")
    titles = window.palette.visible_titles()
    assert titles[0].startswith("Create")
    assert titles[-1] == "Undo Create Circle"


def test_list_shrinks_to_the_results(window, qtbot) -> None:
    open_palette(window, qtbot)
    type_keys(qtbot, "create rect")
    one_row = window.palette.results.height()
    qtbot.keyClick(window.palette.search, Qt.Key.Key_Backspace)
    type_keys(qtbot, "")
    window.palette.search.setText("create")
    assert window.palette.results.height() > one_row
    window.palette.search.setText("zzz")
    assert not window.palette.results.isVisible()


def test_move_command_moves_the_selection(window, qtbot) -> None:
    (circle,) = window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5)).created_ids
    window.session.set_selection(frozenset({circle}))
    open_palette(window, qtbot)
    type_keys(qtbot, "move entities\n5\t-2\n")
    assert not window.palette.isVisible()
    assert window.session.document.entities[circle].center == Point2(x=5.0, y=-2.0)
    assert window.undo_action.text() == "Undo Move Circle"
