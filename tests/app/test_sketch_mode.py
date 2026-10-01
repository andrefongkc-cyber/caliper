"""Sketch mode, the Part panel, and the Extrude form (V2's F6).

The app edits one sketch at a time: drawing goes into it, and the canvas draws and picks only
its entities, so another sketch can't be edited by accident. With one sketch, every V1 file
among them, nothing changes (the rest of the app's tests).
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.app.main_window import MainWindow
from caliper.app.panels.checks import options
from caliper.contracts.commands import (
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
)
from caliper.contracts.document import (
    FIRST_SKETCH,
    EntityId,
    Expectation,
    Extrude,
    Plane,
    Point2,
    Sketch,
)
from caliper.engine import features, geometry
from caliper.engine.geometry.fake_kernel import FakeKernel


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def sketches(window: MainWindow) -> list[EntityId]:
    return [f.id for f in window.session.document.features if isinstance(f, Sketch)]


# --- Editing one sketch at a time ---------------------------------------------------------


def test_a_new_part_edits_its_first_sketch_and_says_so(window: MainWindow) -> None:
    assert window.session.active_sketch == FIRST_SKETCH
    assert window.sketch_label.text() == "Editing Sketch 1  ·  XY"
    assert window.session.sketch_view is window.session.document  # one sketch: the document


def test_a_new_sketch_is_edited_at_once_and_drawing_goes_into_it(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
) -> None:
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=20, height=10))
    window.new_sketch_actions[Plane.XZ].trigger()
    second = sketches(window)[1]
    assert window.session.active_sketch == second
    assert window.mode == "2d"
    assert window.sketch_label.text() == "Editing Sketch 2  ·  XZ"
    driver.tool("Circle")
    driver.click(50, 50)
    driver.click(60, 50)
    (circle,) = [i for i, e in window.session.document.entities.items() if e.kind == "circle"]
    assert window.session.document.entities[circle].sketch == second  # type: ignore[union-attr]
    # The canvas shows and picks only the sketch being edited.
    assert set(window.session.sketch_view.entities) == {circle}
    assert set(window.browser.items) == {circle}


def test_the_other_sketch_cant_be_picked_or_selected_by_accident(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
) -> None:
    (plate,) = window.session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=40, height=20)
    ).created_ids  # type: ignore[union-attr]
    window.new_sketch_actions[Plane.XY].trigger()
    driver.tool("Select")
    driver.click(0, 10)  # right on the first sketch's rectangle
    assert window.session.selection == frozenset()
    window.select_all_action.trigger()
    assert window.session.selection == frozenset()  # nothing drawn here yet
    window.features.edit_requested.emit(FIRST_SKETCH)  # as a double-click on its row does
    assert window.session.active_sketch == FIRST_SKETCH
    driver.click(0, 10)
    assert window.session.selection == {plate}


def test_deleting_or_undoing_the_sketch_being_edited_falls_back_to_another(
    window: MainWindow,
) -> None:
    window.new_sketch_actions[Plane.YZ].trigger()
    second = sketches(window)[1]
    window.undo_action.trigger()
    assert window.session.active_sketch == FIRST_SKETCH
    window.redo_action.trigger()
    window.edit_sketch(second)
    window.session.execute(DeleteEntities(ids=(second,)))
    assert window.session.active_sketch == FIRST_SKETCH


def test_a_file_with_two_sketches_opens_on_its_last(window: MainWindow, tmp_path) -> None:  # type: ignore[no-untyped-def]
    window.new_sketch_actions[Plane.XZ].trigger()
    window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    path = tmp_path / "two.caliper"
    window._save_to(path)
    window.new_action.trigger()
    assert window.load(path)
    assert window.session.active_sketch == sketches(window)[1]


# --- The Part panel -------------------------------------------------------------------------


def test_the_part_panel_lists_features_in_order_with_the_volume(window: MainWindow) -> None:
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    window.session.execute(CreateExtrude(depth=10.0))
    rows = [
        (window.features.tree.topLevelItem(i).text(0), window.features.tree.topLevelItem(i).text(1))
        for i in range(window.features.tree.topLevelItemCount())
    ]
    assert rows == [("Sketch 1", "XY  ·  editing"), ("Extrude 1", "adds 10 mm")]
    assert window.features.volume.text() == "60,000 mm³"
    assert window.solid_label.text() == "Solid 60,000 mm³"


def test_a_failing_extrude_says_why_in_the_part_panel(window: MainWindow) -> None:
    corners = [Point2(x=0, y=0), Point2(x=40, y=0), Point2(x=40, y=30)]
    for a, b in zip(corners, [*corners[1:], corners[0]], strict=True):
        window.session.execute(CreateLine(start=a, end=b))
    (extrude,) = window.session.execute(CreateExtrude(depth=5.0)).created_ids  # type: ignore[union-attr]
    window.session.execute(DeleteEntities(ids=(EntityId("e1"),)))
    item = window.features.items[extrude]
    assert "fails" in item.toolTip(0)
    assert window.features.volume.text() == "Failing"


def test_selecting_a_feature_edits_it_in_properties(window: MainWindow) -> None:
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    (extrude,) = window.session.execute(CreateExtrude(depth=10.0)).created_ids  # type: ignore[union-attr]
    window.features.items[extrude].setSelected(True)
    assert window.session.selection == {extrude}
    depth = window.properties.fields["depth"]
    depth.setText("20")
    depth.editingFinished.emit()
    feature = window.session.document.features[1]
    assert isinstance(feature, Extrude)
    assert feature.depth == 20.0
    assert window.features.volume.text() == "120,000 mm³"
    assert window.session.selection == {extrude}  # a feature stays selected through the change


# --- The Extrude form -------------------------------------------------------------------------


def test_extrude_sends_one_command_and_shows_the_solid(window: MainWindow) -> None:
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    window.extrude_action.trigger()
    form = window.extrude_form
    assert form.isVisible()
    form.depth.setText("10")
    form.confirm.click()
    assert not form.isVisible()
    assert isinstance(window.session.document.features[1], Extrude)
    assert window.mode == "3d"
    assert window.session.history[-1].label == "Extrude"
    assert QApplication.focusWidget() is window.view3d  # not left in the closed form


def test_extrude_explains_an_open_profile_and_stays_open(window: MainWindow) -> None:
    window.session.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=10, y=0)))
    window.extrude_action.trigger()
    form = window.extrude_form
    form.confirm.click()
    assert form.isVisible()
    assert form.error.isVisible()
    assert "closed" in form.error.text() or "join" in form.error.text()
    assert len(window.session.document.features) == 1
    form.close_form()
    assert not form.isVisible()


def test_extrude_takes_the_selected_profile(window: MainWindow) -> None:
    (plate,) = window.session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50)
    ).created_ids  # type: ignore[union-attr]
    window.session.execute(CreateCircle(center=Point2(x=300, y=0), radius=5))  # elsewhere
    window.session.set_selection(frozenset({plate}))
    window.extrude_action.trigger()
    window.extrude_form.confirm.click()
    extrude = window.session.document.features[1]
    assert isinstance(extrude, Extrude)
    assert extrude.ids == (plate,)
    QApplication.processEvents()
    assert window.features.volume.text() == "60,000 mm³"


def test_a_part_with_no_sketch_has_nothing_to_extrude(window: MainWindow) -> None:
    window.session.execute(DeleteEntities(ids=(FIRST_SKETCH,)))
    window.extrude_action.trigger()
    assert window.statusBar().currentMessage().startswith("The part has no sketch")
    assert window.sketch_label.text().startswith("No sketch")


def test_the_width_edited_in_properties_reaches_the_solid(window: MainWindow) -> None:
    (plate,) = window.session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50)
    ).created_ids  # type: ignore[union-attr]
    window.session.execute(CreateExtrude(depth=10.0))
    window.session.execute(ModifyEntity(id=plate, changes={"width": 140.0}))
    assert window.features.volume.text() == "70,000 mm³"


def test_the_extrude_panel_takes_return_and_escape(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    window.extrude_action.trigger()
    form = window.extrude_form
    assert form.parent() is window.views  # in the window, over the view: not a window of its own
    assert QApplication.focusWidget() is form.depth
    qtbot.keyClick(form, Qt.Key.Key_Escape)
    assert not form.isVisible()
    assert len(window.session.document.features) == 1
    window.extrude_action.trigger()
    form = window.extrude_form
    form.depth.setText("12.5")
    qtbot.keyClick(form.depth, Qt.Key.Key_Return)
    extrude = window.session.document.features[1]
    assert isinstance(extrude, Extrude)
    assert extrude.depth == 12.5


# --- The rest of the window, with several sketches (F7) -------------------------------------


def test_the_sketch_size_checks_measure_the_sketch_being_edited(window: MainWindow) -> None:
    """With two sketches, "all the geometry" spans planes and has no one size: the Checks
    panel's Sketch width names the edited sketch's own geometry."""
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    window.new_sketch_actions[Plane.XZ].trigger()
    labels = [o.label for o in options(window.session)]
    assert "Sketch width" not in labels  # nothing drawn here yet: no size to check
    (circle,) = window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5)).created_ids  # type: ignore[union-attr]
    width = next(o for o in options(window.session) if o.label == "Sketch width")
    assert width.ids == (circle,)
    result = window.session.queries.check(
        Expectation(metric=width.metric, expected=10.0, tolerance=1e-6, ids=width.ids)
    )
    assert result.error is None
    assert result.passed
    window.edit_sketch(FIRST_SKETCH)
    width = next(o for o in options(window.session) if o.label == "Sketch width")
    assert width.ids == (EntityId("e1"),)


def test_with_one_sketch_the_size_checks_name_no_ids(window: MainWindow) -> None:
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    width = next(o for o in options(window.session) if o.label == "Sketch width")
    assert width.ids == ()  # the whole sketch, as a V1 file's check always said
