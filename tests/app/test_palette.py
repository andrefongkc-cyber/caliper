"""Command palette: find by name, run actions, and fill typed command forms."""

import typing

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.app.command_schema import build, command_specs, matches
from caliper.contracts.commands import (
    Command,
    CreateCircle,
    CreateLine,
    CreateRectangle,
    DeleteEntities,
    FilletCorner,
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


def test_fillet_takes_its_two_lines_from_the_selection() -> None:
    (spec,) = [s for s in command_specs() if s.type is FilletCorner]
    assert spec.title == "Fillet Corner"
    assert [f.path for f in spec.fields] == ["radius"]
    assert spec.selection_fields == ("a", "b")
    assert spec.wants == 2
    ids = frozenset({EntityId("e2"), EntityId("e1")})
    assert build(spec, {"radius": 5}, ids) == FilletCorner(a="e1", b="e2", radius=5)


def test_fillet_from_the_palette_rounds_the_corner(window, qtbot) -> None:
    session = window.session
    (first,) = session.execute(
        CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=0))
    ).created_ids
    (second,) = session.execute(
        CreateLine(start=Point2(x=100, y=0), end=Point2(x=100, y=80))
    ).created_ids
    session.set_selection(frozenset({first, second}))
    open_palette(window, qtbot)
    type_keys(qtbot, "fillet corner\n20\n")
    assert not window.palette.isVisible()
    arc = [e for e in session.document.entities.values() if e.kind == "arc"]
    assert len(arc) == 1
    assert arc[0].radius == 20.0
    assert window.undo_action.text() == "Undo Fillet Corner"


def test_fillet_needs_exactly_two_shapes_selected(window, qtbot) -> None:
    (only,) = window.session.execute(
        CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=0))
    ).created_ids
    window.session.set_selection(frozenset({only}))
    open_palette(window, qtbot)
    type_keys(qtbot, "fillet corner\n")
    assert window.palette.stack.currentIndex() == 0
    assert "needs exactly 2 selected, not 1" in window.palette.error.text()


def test_fillet_radius_that_doesnt_fit_explains_the_limit(window, qtbot) -> None:
    session = window.session
    (first,) = session.execute(
        CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=0))
    ).created_ids
    (second,) = session.execute(
        CreateLine(start=Point2(x=100, y=0), end=Point2(x=100, y=80))
    ).created_ids
    session.set_selection(frozenset({first, second}))
    open_palette(window, qtbot)
    type_keys(qtbot, "fillet corner\n500\n")
    assert window.palette.isVisible()
    assert "must stay under 80" in window.palette.error.text()
    assert window.palette.form_fields["radius"].property("invalid") is True


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


def test_typing_fillet_picks_the_tool_over_the_command(window, qtbot) -> None:
    open_palette(window, qtbot)
    type_keys(qtbot, "fillet")
    assert window.palette.visible_titles() == ["Fillet", "Fillet Corner"]
    qtbot.keyClick(window.palette.search, Qt.Key.Key_Return)
    assert window.controller.active.name == "Fillet"


# --- The palette docked in the sidebar ---------------------------------------------------


def panel_titles(window) -> list[str]:
    return window.command_panel.visible_titles()


def test_the_sidebar_lists_commands_under_properties(window) -> None:
    panel = window.command_panel
    assert window.commands_dock.widget() is panel
    assert window.dockWidgetArea(window.commands_dock) == Qt.DockWidgetArea.RightDockWidgetArea
    assert panel.isVisible()
    properties = window.properties_dock.geometry()
    commands = window.commands_dock.geometry()
    checks = window.checks_dock.geometry()
    assert properties.bottom() < commands.top()
    assert commands.bottom() < checks.top()


def test_the_sidebar_lists_everything_the_palette_does_without_opening_it(window, qtbot) -> None:
    sidebar = panel_titles(window)
    assert "Rectangle" in sidebar
    assert "Create Rectangle" in sidebar
    open_palette(window, qtbot)
    assert sorted(sidebar) == sorted(window.palette.visible_titles())


def test_typing_in_the_sidebar_filters_it(window, qtbot) -> None:
    qtbot.keyClicks(window.command_panel.search, "circ")
    assert panel_titles(window) == ["Circle", "Create Circle"]


def test_a_command_run_from_the_sidebar_leaves_it_ready_for_the_next(window, bus, qtbot) -> None:
    panel = window.command_panel
    panel.search.setFocus()
    qtbot.keyClicks(panel.search, "create rect")
    qtbot.keyClick(panel.search, Qt.Key.Key_Return)
    assert panel.stack.currentIndex() == 1
    assert QApplication.focusWidget().objectName() == "width"  # the corner defaults to 0, 0
    type_keys(qtbot, "120\t50\n")
    assert bus.sent == [
        CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0),
    ]
    assert panel.isVisible()
    assert panel.stack.currentIndex() == 0
    assert panel.search.text() == ""
    assert QApplication.focusWidget() is window.canvas


def test_the_sidebar_greys_out_actions_as_they_become_unavailable(window, qtbot) -> None:
    def undo_enabled() -> bool:
        panel = window.command_panel
        (row,) = [i for i in range(panel.results.count()) if panel.results.item(i).text() == "Undo"]
        return bool(panel.results.item(row).flags() & Qt.ItemFlag.ItemIsEnabled)

    assert not undo_enabled()
    window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    assert undo_enabled()


def test_escape_in_the_sidebar_clears_it_and_returns_to_the_canvas(window, qtbot) -> None:
    panel = window.command_panel
    panel.search.setFocus()
    qtbot.keyClicks(panel.search, "circ")
    qtbot.keyClick(panel.search, Qt.Key.Key_Escape)
    assert panel.isVisible()
    assert panel.search.text() == ""
    assert QApplication.focusWidget() is window.canvas


def test_the_sidebar_list_is_drawn_on_the_panel_colour(window) -> None:
    from caliper.app import theme

    panel = window.command_panel
    results = panel.results
    image = panel.grab().toImage()
    ratio = image.devicePixelRatio()
    row = results.visualItemRect(results.item(1))
    at_list = results.mapTo(panel, row.center())
    # Between a title and its shortcut, and in the margin around the search field.
    for x, y in ((at_list.x(), at_list.y()), (1, 1)):
        colour = image.pixelColor(round(x * ratio), round(y * ratio))
        assert max(
            abs(colour.red() - theme.PANEL.red()),
            abs(colour.green() - theme.PANEL.green()),
            abs(colour.blue() - theme.PANEL.blue()),
        ) <= 8, (x, y, colour.name(), theme.PANEL.name())
