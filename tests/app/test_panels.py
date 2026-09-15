"""Browser, History, and Checks panels."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.app.panels.checks import describe, options
from caliper.app.panels.history import POSITION_ROLE, ago
from caliper.app.session import Author
from caliper.contracts.commands import (
    CreateCircle,
    CreateDistanceDimension,
    CreateRectangle,
    ModifyEntity,
)
from caliper.contracts.document import DistanceOrientation, Feature, Point2, Ref
from caliper.contracts.queries import Expectation, Metric


@pytest.fixture
def sketch(window) -> tuple[str, str]:
    s = window.session
    (plate,) = s.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50)).created_ids
    (hole,) = s.execute(CreateCircle(center=Point2(x=30, y=25), radius=8)).created_ids
    return plate, hole


# --- History ------------------------------------------------------------------------------


def test_history_records_each_change_with_its_author(window, sketch) -> None:
    labels = [(e.label, e.author) for e in window.session.history]
    assert labels == [("Create Rectangle", Author.YOU), ("Create Circle", Author.YOU)]


def test_a_transaction_is_one_history_entry_and_one_undo_step(window, sketch) -> None:
    plate, _ = sketch
    session = window.session
    with session.transaction("Resize Plate", author=Author.AGENT):
        session.execute(ModifyEntity(id=plate, changes={"width": 140.0}))
        session.execute(ModifyEntity(id=plate, changes={"height": 60.0}))
    assert session.history[-1].label == "Resize Plate"
    assert session.history[-1].author is Author.AGENT
    assert len(session.history) == 3
    assert window.undo_action.text() == "Undo Resize Plate"
    window.undo_action.trigger()
    assert session.document.entities[plate].width == 120.0
    assert session.history_position == 2


def test_rolled_back_transaction_is_not_recorded(window, sketch) -> None:
    plate, _ = sketch
    session = window.session
    with session.transaction("Try Something") as tx:
        session.execute(ModifyEntity(id=plate, changes={"width": 200.0}))
        tx.rollback()
    assert len(session.history) == 2
    assert session.document.entities[plate].width == 120.0


def test_undone_entries_stay_until_a_new_change_replaces_them(window, sketch) -> None:
    session = window.session
    window.undo_action.trigger()
    assert session.history_position == 1
    assert len(session.history) == 2
    session.execute(CreateCircle(center=Point2(x=90, y=25), radius=4))
    assert [e.label for e in session.history] == ["Create Rectangle", "Create Circle"]
    assert session.history_position == 2


def test_rejected_and_no_op_commands_are_not_recorded(window, sketch) -> None:
    plate, _ = sketch
    window.session.execute(ModifyEntity(id=plate, changes={"width": -1.0}))
    window.session.execute(ModifyEntity(id=plate, changes={"width": 120.0}))
    assert len(window.session.history) == 2


def test_clicking_a_history_row_goes_back_to_it(window, sketch) -> None:
    history = window.history
    rows = {history.item(i).text(): history.item(i) for i in range(history.count())}
    assert list(rows) == ["Create Circle", "Create Rectangle", "Start"]
    history.itemClicked.emit(rows["Create Rectangle"])
    assert window.session.history_position == 1
    assert len(window.session.document.entities) == 1
    history.itemClicked.emit(history.item(history.count() - 1))  # Start
    assert window.session.document.entities == {}
    top = history.item(0)
    assert top.data(POSITION_ROLE) == 2
    history.itemClicked.emit(top)
    assert len(window.session.document.entities) == 2


def test_new_document_clears_history_and_checks(window, sketch) -> None:
    window.session.add_check(Expectation(metric=Metric.BBOX_WIDTH, expected=1, tolerance=1))
    window.new_action.trigger()
    assert window.session.history == ()
    assert window.session.checks == ()


@pytest.mark.parametrize(("seconds", "text"), [(5, "just now"), (125, "2 min ago")])
def test_relative_times(seconds: int, text: str) -> None:
    assert ago(1000.0, 1000.0 + seconds) == text


# --- Browser ------------------------------------------------------------------------------


def test_browser_lists_entities_with_their_sizes(window, sketch) -> None:
    plate, hole = sketch
    items = window.browser.items
    assert items[plate].text(0) == "Rectangle  e1"
    assert items[plate].text(1) == "120 \u00d7 50"
    assert items[hole].text(1) == "⌀16"
    window.session.execute(ModifyEntity(id=plate, changes={"width": 140.0}))
    assert window.browser.items[plate].text(1) == "140 \u00d7 50"


def test_browser_and_canvas_selection_stay_in_sync(window, driver, sketch) -> None:
    plate, hole = sketch
    driver.click(0, 20)
    assert window.browser.items[plate].isSelected()
    window.browser.items[plate].setSelected(False)
    window.browser.items[hole].setSelected(True)
    assert window.session.selection == {hole}


def test_browser_groups_dimensions_and_names_them_briefly(window, sketch) -> None:
    plate, _ = sketch
    (dim,) = window.session.execute(
        CreateDistanceDimension(
            a=Ref(entity=plate, feature=Feature.BOTTOM_LEFT),
            b=Ref(entity=plate, feature=Feature.BOTTOM_RIGHT),
            orientation=DistanceOrientation.HORIZONTAL,
            offset=-10,
        )
    ).created_ids
    item = window.browser.items[dim]
    assert item.parent() is window.browser.groups["Dimensions"]
    assert item.text(0) == "Distance  e3"
    assert item.text(1) == "120 · horizontal"


def test_ids_sort_naturally(window) -> None:
    for i in range(11):
        window.session.execute(CreateCircle(center=Point2(x=i * 10, y=0), radius=1))
    group = window.browser.groups["Geometry"]
    names = [group.child(i).text(0).split()[-1] for i in range(group.childCount())]
    assert names[:3] == ["e1", "e2", "e3"]
    assert names[-1] == "e11"


def test_double_click_frames_the_entity(window, sketch) -> None:
    _, hole = sketch
    window.canvas.zoom_to_fit()
    before = window.canvas.view.scale
    window.browser.itemDoubleClicked.emit(window.browser.items[hole], 0)
    assert window.canvas.view.scale > before
    box = window.canvas.visible_box()
    assert box.x_min < 22
    assert box.x_max > 38


# --- Checks -------------------------------------------------------------------------------


def test_checks_re_measure_after_every_change(window, sketch) -> None:
    plate, _ = sketch
    window.session.add_check(
        Expectation(metric=Metric.BBOX_WIDTH, expected=140, tolerance=0.01, ids=(plate,))
    )
    panel = window.checks
    assert panel.summary.text() == "0 of 1 pass"
    window.session.execute(ModifyEntity(id=plate, changes={"width": 140.0}))
    assert panel.summary.text() == "1 of 1 pass"
    assert panel.list.item(0).text() == "Width of e1 = 140 ± 0.01"


def test_add_check_from_the_selection_prefills_the_current_value(window, qtbot, sketch) -> None:
    plate, _ = sketch
    window.session.set_selection(frozenset({plate}))
    panel = window.checks
    panel.add_button.click()
    assert [panel.metric_box.itemText(i) for i in range(panel.metric_box.count())][:2] == [
        "Width of e1",
        "Height of e1",
    ]
    assert panel.expected.text() == "120"
    panel.expected.setText("120")
    panel.confirm.click()
    (check,) = window.session.checks
    assert check == Expectation(
        metric=Metric.BBOX_WIDTH, expected=120, tolerance=0.01, ids=(plate,)
    )
    assert panel.form.isHidden()


def test_measurement_becomes_a_check(window, driver, sketch) -> None:
    driver.tool("Measure")
    driver.click(0, 0)
    driver.click(120, 50)
    labels = [o.label for o in options(window.session)]
    assert "Last measurement: distance" in labels
    panel = window.checks
    panel.add_button.click()
    assert panel.metric_box.currentText() == "Last measurement: distance"
    assert panel.expected.text() == "130"
    panel.confirm.click()
    assert describe(window.session.checks[0]).startswith("Distance e1 bottom left → e1 top right")


def test_area_isnt_offered_without_a_geometry_kernel(window, sketch) -> None:
    _, hole = sketch
    window.session.set_selection(frozenset({hole}))
    assert "Area of e2" not in [o.label for o in options(window.session)]


def test_invalid_expected_value_is_explained(window, sketch) -> None:
    panel = window.checks
    panel.add_button.click()
    panel.expected.setText("wide")
    panel.confirm.click()
    assert window.session.checks == ()
    assert "Enter a number" in panel.error.text()


def test_delete_key_removes_a_check(window, qtbot, sketch) -> None:
    window.session.add_check(Expectation(metric=Metric.BBOX_WIDTH, expected=1, tolerance=1))
    panel = window.checks
    panel.list.setCurrentRow(0)
    panel.list.setFocus()
    qtbot.keyClick(panel.list, Qt.Key.Key_Delete)
    assert window.session.checks == ()
    assert QApplication.focusWidget() is not None


def test_browser_updates_rows_in_place(window, sketch) -> None:
    plate, hole = sketch
    hole_item = window.browser.items[hole]
    window.session.execute(ModifyEntity(id=plate, changes={"width": 150.0}))
    assert window.browser.items[hole] is hole_item  # untouched rows aren't rebuilt
    assert window.browser.items[plate].text(1) == "150 \u00d7 50"
    window.undo_action.trigger()
    window.undo_action.trigger()
    assert hole not in window.browser.items
    assert window.browser.groups["Geometry"].childCount() == 1
