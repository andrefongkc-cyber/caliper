"""Browser, History, and Checks panels."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.app import icons
from caliper.app.panels.checks import describe, options
from caliper.app.panels.describe import ICON
from caliper.app.panels.history import POSITION_ROLE, ago
from caliper.app.session import Author
from caliper.contracts.commands import (
    CreateCircle,
    CreateConstraint,
    CreateDimension,
    CreateDistanceDimension,
    CreateLine,
    CreatePoint,
    CreateRadialDimension,
    CreateRectangle,
    ModifyEntity,
)
from caliper.contracts.document import (
    ConstraintType,
    DistanceOrientation,
    Entity,
    Feature,
    Point2,
    RadialMeasure,
    Ref,
)
from caliper.contracts.queries import AreaProperties, BoundingBox, Expectation, Metric
from caliper.engine.commands.bus import Bus
from tests.app.conftest import make_window


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
    assert item.text(1) == "(120) · horizontal"  # driven: in parentheses, as on the canvas


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


class AreaKernel:
    """Just enough of the contract's `Kernel` for a circle's area to be measurable."""

    def make_face(self, boundary: object) -> object:
        return boundary

    def area_properties(self, face: object) -> AreaProperties:
        return AreaProperties(area=1.0, centroid=Point2(x=0, y=0), ixx=0.0, iyy=0.0, ixy=0.0)

    def bounding_box(self, shape: object) -> BoundingBox:
        return BoundingBox(x_min=0, y_min=0, x_max=1, y_max=1)

    def is_valid(self, shape: object) -> bool:
        return True


def area_offered(qtbot, kernel: AreaKernel | None) -> bool:
    """Whether Checks offers the area of a circle, on a bus with exactly this kernel.

    The kernel is pinned rather than inherited: the default finds OCCT whenever the `occt`
    extra is installed, which CI's app job doesn't install and a developer's machine may.
    """
    window = make_window(qtbot, Bus(kernel=kernel))
    s = window.session
    (hole,) = s.execute(CreateCircle(center=Point2(x=30, y=25), radius=8)).created_ids
    s.set_selection(frozenset({hole}))
    return f"Area of {hole}" in [o.label for o in options(s)]


def test_area_isnt_offered_without_a_geometry_kernel(qtbot) -> None:
    assert not area_offered(qtbot, None)


def test_area_is_offered_when_a_kernel_can_answer(qtbot) -> None:
    assert area_offered(qtbot, AreaKernel())


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


def _width_dimension(session, plate: str) -> str:
    (dim,) = session.execute(
        CreateDistanceDimension(
            a=Ref(entity=plate, feature=Feature.BOTTOM_LEFT),
            b=Ref(entity=plate, feature=Feature.BOTTOM_RIGHT),
            orientation=DistanceOrientation.HORIZONTAL,
            offset=-10,
        )
    ).created_ids
    return dim


def test_a_dimension_row_follows_the_shape_it_measures(window, sketch) -> None:
    plate, _ = sketch
    dim = _width_dimension(window.session, plate)
    window.session.execute(ModifyEntity(id=plate, changes={"width": 150.0}))
    assert window.browser.items[dim].text(1) == "(150) · horizontal"


def test_an_edit_refills_only_the_rows_it_changes(window, sketch, monkeypatch) -> None:
    plate, hole = sketch
    on_plate = _width_dimension(window.session, plate)
    (on_hole,) = window.session.execute(
        CreateRadialDimension(target=hole, measure=RadialMeasure.DIAMETER, label_angle=45)
    ).created_ids
    browser = window.browser
    filled: list[str] = []
    fill = browser._fill
    monkeypatch.setattr(
        browser, "_fill", lambda item, id, queries: (filled.append(id), fill(item, id, queries))
    )
    window.session.execute(ModifyEntity(id=plate, changes={"width": 150.0}))
    assert sorted(filled) == sorted([plate, on_plate])  # not the hole or its diameter
    assert on_hole in browser.items


def test_the_value_column_fits_its_longest_value(window, sketch, qtbot) -> None:
    plate, _ = sketch
    browser = window.browser
    metrics = browser.fontMetrics()

    def fits() -> bool:
        widest = max(metrics.horizontalAdvance(i.text(1)) for i in browser.items.values())
        return browser.header().sectionSize(1) >= widest

    qtbot.waitUntil(fits)
    narrow = browser.header().sectionSize(1)
    window.session.execute(ModifyEntity(id=plate, changes={"width": 123456.789}))
    qtbot.waitUntil(fits)
    assert browser.header().sectionSize(1) > narrow
    window.undo_action.trigger()
    qtbot.waitUntil(lambda: browser.header().sectionSize(1) == narrow)  # and shrinks back
    (wide,) = window.session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=987654.321, height=1)
    ).created_ids
    qtbot.waitUntil(lambda: browser.header().sectionSize(1) > narrow)
    window.session.set_selection(frozenset({wide}))
    window.delete_action.trigger()
    assert wide not in browser.items
    qtbot.waitUntil(lambda: browser.header().sectionSize(1) == narrow)  # a removed row too


# --- V1.5 kinds ---------------------------------------------------------------------------


def test_every_kind_has_an_icon() -> None:

    kinds = {t.kind for t in Entity.__args__}  # type: ignore[attr-defined]
    assert kinds <= ICON.keys()
    assert set(ICON.values()) <= icons.NAMES


def test_the_browser_lists_points_constraints_and_angles(window) -> None:
    from caliper.contracts.commands import (
        CreateConstraint,
        CreateLine,
    )

    s = window.session
    (point,) = s.execute(CreatePoint(position=Point2(x=5, y=5))).created_ids
    (a,) = s.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=2))).created_ids
    (b,) = s.execute(
        CreateLine(start=Point2(x=0, y=0), end=Point2(x=0, y=50), construction=True)
    ).created_ids
    curve_a = Ref(entity=a, feature=Feature.CURVE)
    curve_b = Ref(entity=b, feature=Feature.CURVE)
    (h,) = s.execute(CreateConstraint(type=ConstraintType.HORIZONTAL, refs=(curve_a,))).created_ids
    (angle,) = s.execute(
        CreateDimension(refs=(curve_a, curve_b), placement=Point2(x=10, y=10))
    ).created_ids
    items = window.browser.items
    assert items[point].parent() is window.browser.groups["Geometry"]
    assert items[h].parent() is window.browser.groups["Constraints"]
    assert items[h].text(0) == f"Horizontal  {h}"
    assert items[h].text(1) == f"{a} curve"
    assert items[angle].parent() is window.browser.groups["Dimensions"]
    assert items[angle].text(1) == "(90°)"
    assert items[b].font(0).italic()  # construction
    assert not items[a].font(0).italic()


def test_a_constraint_offers_no_checks(window) -> None:

    s = window.session
    (a,) = s.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=2))).created_ids
    (h,) = s.execute(
        CreateConstraint(
            type=ConstraintType.HORIZONTAL, refs=(Ref(entity=a, feature=Feature.CURVE),)
        )
    ).created_ids
    s.set_selection(frozenset({h}))
    assert options(s) == []
