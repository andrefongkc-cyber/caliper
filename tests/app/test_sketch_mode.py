"""Sketching in 3D (ADR 0015), the Part panel, and the Extrude panel.

A sketch is started on one of the part's planes, picked in the 3D view or the Part panel, and
edited facing that plane: the 2D canvas, every tool as it is, over the part. A right drag
orbits away, N faces the sketch again, and Finish or Cancel closes it. While a sketch is
edited, the canvas draws and picks only its entities, so another sketch can't be edited by
accident. The 2D tab is a sketch to test on, a document of its own (the rest of the app's
tests).
"""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from caliper.app.agent.proposal import Plan
from caliper.app.main_window import MainWindow
from caliper.app.panels.checks import options
from caliper.app.viewport.painter import ModelPainter
from caliper.app.viewport.scene3d import facing_camera
from caliper.contracts.commands import (
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    DeleteEntities,
)
from caliper.contracts.document import (
    EntityId,
    Expectation,
    Extrude,
    Plane,
    Point2,
    Rectangle,
    Sketch,
)
from caliper.contracts.queries import Point3
from caliper.engine import faces, features, geometry, part
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.app.parts import plate, sketch_on


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def sketches(window: MainWindow) -> list[EntityId]:
    return [f.id for f in window.session.document.features if isinstance(f, Sketch)]


def click_row(qtbot, window: MainWindow, item, *, double: bool = False) -> None:  # type: ignore[no-untyped-def]
    QApplication.processEvents()  # the panel laid out, as it is before anyone can click it
    tree = window.features.tree
    at = tree.visualItemRect(item).center()
    qtbot.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=at)
    if double:  # a double-click is a click, then the second press: an item view needs both
        qtbot.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=at)


# --- Starting a sketch on a plane -------------------------------------------------------------


def test_a_sketch_on_a_picked_plane_faces_it_over_the_part(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    window.set_mode("3d")
    click_row(qtbot, window, window.features.planes[Plane.XZ])
    assert window.session.picked_plane is Plane.XZ
    window.sketch_action.trigger()
    sketch = window.sketch_open
    assert sketch is not None
    assert window.session.document.features[-1] == Sketch(id=sketch, plane=Plane.XZ)
    assert window.session.active_sketch == sketch
    assert window.views.currentWidget() is window.canvas  # still the 3D tab: over the part
    backdrop = window.canvas.backdrop
    assert backdrop is not None
    assert backdrop.facing
    assert backdrop.plane is Plane.XZ
    assert window.sketch_bar.isVisible()
    assert window.sketch_title.text() == "Sketch 1  ·  Front"
    assert window.tool_actions["Rectangle"].isEnabled()
    # The canvas maps the plane to the screen exactly as the camera facing it does.
    view, w, h = window.canvas.view, window.canvas.width(), window.canvas.height()
    camera = backdrop.camera(view, w, h)
    for x, z in ((0.0, 0.0), (30.0, -12.5)):
        seen = camera.project(Point3(x=x, y=0.0, z=z), w, h)
        assert (seen.x, seen.y) == pytest.approx(view.to_widget(Point2(x=x, y=z)))
    assert window.features.items[sketch].text(1) == "Front  ·  editing"


def test_a_sketch_needs_a_plane_or_a_sketch_picked_first(window: MainWindow) -> None:
    window.set_mode("3d")
    window.sketch_action.trigger()
    assert window.sketch_open is None
    assert window.statusBar().currentMessage().startswith("Pick a plane")


def test_double_clicking_a_plane_or_a_sketch_row_starts_or_edits_it(
    window: MainWindow, qtbot
) -> None:  # type: ignore[no-untyped-def]
    window.set_mode("3d")
    click_row(qtbot, window, window.features.planes[Plane.YZ], double=True)
    first = window.sketch_open
    assert first is not None
    window.finish_sketch()
    sketch_on(window, Plane.XY)
    window.finish_sketch()
    click_row(qtbot, window, window.features.items[first], double=True)
    assert window.sketch_open == first
    assert window.canvas.backdrop is not None
    assert window.canvas.backdrop.plane is Plane.YZ


def test_drawing_goes_into_the_open_sketch_and_only_it_is_seen(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
) -> None:
    plate(window)
    second = sketch_on(window, Plane.XZ)
    driver.tool("Circle")
    driver.click(50, 50)
    driver.click(60, 50)
    (circle,) = [i for i, e in window.session.document.entities.items() if e.kind == "circle"]
    assert window.session.document.entities[circle].sketch == second  # type: ignore[union-attr]
    assert set(window.session.sketch_view.entities) == {circle}
    assert set(window.browser.items) == {circle}


def test_the_other_sketch_cant_be_picked_or_selected_by_accident(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
) -> None:
    first = sketch_on(window)
    (rectangle,) = window.session.execute(  # type: ignore[union-attr]
        CreateRectangle(corner=Point2(x=0, y=0), width=40, height=20)
    ).created_ids
    window.finish_sketch()
    sketch_on(window, Plane.XY)  # another sketch on the same plane
    driver.tool("Select")
    driver.click(0, 10)  # right on the first sketch's rectangle
    assert window.session.selection == frozenset()
    window.select_all_action.trigger()
    assert window.session.selection == frozenset()  # nothing drawn here yet
    window.edit_sketch(first)
    driver.click(0, 10)
    assert window.session.selection == {rectangle}


# --- Finish, Cancel, orbit, N ------------------------------------------------------------------


def test_finish_keeps_the_sketch_and_cancel_takes_back_all_of_it(window: MainWindow) -> None:
    sketch = sketch_on(window)
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=40, height=20))
    window.finish_button.click()
    assert window.sketch_open is None
    assert window.views.currentWidget() is window.view3d
    assert not window.sketch_bar.isVisible()
    assert not window.tool_actions["Rectangle"].isEnabled()
    assert sketches(window) == [sketch]
    kept = window.session.document
    # A new sketch, cancelled: it's gone with what was drawn in it.
    sketch_on(window, Plane.XZ)
    window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    window.cancel_button.click()
    assert window.sketch_open is None
    assert window.session.document == kept
    assert "undid 2 changes" in window.statusBar().currentMessage()
    window.redo_action.trigger()  # Redo brings it back
    assert len(sketches(window)) == 2
    window.undo_action.trigger()
    # An existing sketch, edited and cancelled: only the edit goes.
    window.edit_sketch(sketch)
    window.session.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=9, y=9)))
    window.cancel_sketch()
    assert window.session.document == kept


def test_a_right_drag_orbits_and_drawing_carries_on_at_an_angle(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    """ADR 0016: turned up to 70° from the sketch's plane, the tools still draw on it; a click
    lands where the pointer meets the plane."""
    sketch_on(window)
    canvas, backdrop = window.canvas, window.canvas.backdrop
    assert backdrop is not None
    qtbot.mousePress(canvas, Qt.MouseButton.RightButton, pos=QPoint(300, 300))
    qtbot.mouseMove(canvas, QPoint(340, 280))
    qtbot.mouseRelease(canvas, Qt.MouseButton.RightButton, pos=QPoint(340, 280))
    assert not backdrop.facing
    assert backdrop.drawable
    assert 0.34 < backdrop.tilt < 1.0
    assert window.tool_actions["Rectangle"].isEnabled()
    assert window.face_action.isEnabled()
    assert "At an angle" in window.sketch_hint.text()
    mapping = canvas.mapping
    driver.tool("Rectangle")
    a, b = mapping.to_widget(Point2(x=0, y=0)), mapping.to_widget(Point2(x=40, y=20))
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(round(a[0]), round(a[1])))
    qtbot.mouseMove(canvas, QPoint(round(b[0]), round(b[1])))
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(round(b[0]), round(b[1])))
    (drawn,) = window.session.document.entities.values()
    assert isinstance(drawn, Rectangle)
    assert (drawn.width, drawn.height) == pytest.approx((40.0, 20.0), abs=1.0)  # snapped


def test_turned_too_far_from_the_sketch_only_looks_and_n_faces_it_again(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    sketch_on(window)
    canvas, backdrop = window.canvas, window.canvas.backdrop
    assert backdrop is not None
    before = (canvas.view.scale, canvas.view.origin_x, canvas.view.origin_y)
    qtbot.mousePress(canvas, Qt.MouseButton.RightButton, pos=QPoint(300, 330))
    qtbot.mouseMove(canvas, QPoint(300, 120))  # tilted by about 84°: nearly edge on
    qtbot.mouseRelease(canvas, Qt.MouseButton.RightButton, pos=QPoint(300, 120))
    assert not backdrop.drawable
    assert not window.tool_actions["Rectangle"].isEnabled()
    assert "Turned too far" in window.sketch_hint.text()
    driver.click(10, 10)
    driver.click(30, 20)
    assert dict(window.session.document.entities) == {}  # clicks turn the view, never draw
    window.face_action.trigger()  # N
    assert backdrop.facing
    assert window.tool_actions["Rectangle"].isEnabled()
    assert canvas.view.scale == pytest.approx(before[0])
    driver.tool("Rectangle")
    driver.drag([(0, 0), (40, 20)])
    assert len(window.session.document.entities) == 1  # drawing again


def test_a_right_click_without_a_drag_still_cancels_the_tool(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    sketch_on(window)
    driver.tool("Line")
    driver.click(0, 0)
    assert window.controller.active.busy
    qtbot.mouseClick(window.canvas, Qt.MouseButton.RightButton, pos=QPoint(300, 300))
    assert not window.controller.active.busy
    assert window.canvas.backdrop is not None
    assert window.canvas.backdrop.facing


def test_undoing_the_open_sketch_closes_it(window: MainWindow) -> None:
    sketch_on(window, Plane.YZ)
    window.undo_action.trigger()
    assert window.sketch_open is None
    assert window.views.currentWidget() is window.view3d
    assert sketches(window) == []


def test_the_sketch_is_kept_open_across_a_trip_to_the_2d_tab(window: MainWindow) -> None:
    sketch = sketch_on(window, Plane.XZ)
    view = window.canvas.view
    facing = (view.scale, view.origin_x, view.origin_y)
    window.set_mode("2d")
    assert window.canvas.backdrop is None
    assert window.session.active_sketch != sketch
    window.set_mode("3d")
    assert window.sketch_open == sketch
    assert window.canvas.backdrop is not None
    assert (view.scale, view.origin_x, view.origin_y) == facing


def test_a_part_with_two_sketches_opens_on_its_last(window: MainWindow, tmp_path) -> None:  # type: ignore[no-untyped-def]
    plate(window)
    sketch_on(window, Plane.XZ)
    window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    window.finish_sketch()
    path = tmp_path / "two.caliper"
    window._save_to(path)
    window.new_action.trigger()
    assert sketches(window) == []
    assert window.load(path)
    assert window.mode == "3d"
    assert window.session.active_sketch == sketches(window)[1]
    assert window.sketch_open is None  # opened, not being edited


# --- The Part panel -------------------------------------------------------------------------


def test_the_part_panel_lists_the_planes_and_features_in_order_with_the_volume(
    window: MainWindow,
) -> None:
    plate(window)
    tree = window.features.tree
    defaults = tree.topLevelItem(0)
    assert defaults.text(0) == "Default geometry"
    assert [defaults.child(i).text(0) for i in range(4)] == ["Origin", "Top", "Front", "Right"]
    rows = [(tree.topLevelItem(i).text(0), tree.topLevelItem(i).text(1)) for i in (1, 2)]
    assert rows == [("Sketch 1", "Top"), ("Extrude 1", "adds 10 mm")]
    assert window.features.volume.text() == "60,000 mm³"
    assert window.solid_label.text() == "Solid 60,000 mm³"


def test_a_failing_extrude_says_why_in_the_part_panel(window: MainWindow) -> None:
    sketch_on(window)
    corners = [Point2(x=0, y=0), Point2(x=40, y=0), Point2(x=40, y=30)]
    lines = [
        window.session.execute(CreateLine(start=a, end=b)).created_ids[0]  # type: ignore[union-attr]
        for a, b in zip(corners, [*corners[1:], corners[0]], strict=True)
    ]
    window.finish_sketch()
    (extrude,) = window.session.execute(CreateExtrude(depth=5.0)).created_ids  # type: ignore[union-attr]
    window.session.execute(DeleteEntities(ids=(lines[0],)))
    item = window.features.items[extrude]
    assert "fails" in item.toolTip(0)
    assert window.features.volume.text() == "Failing"


def test_selecting_a_feature_edits_it_in_properties(window: MainWindow) -> None:
    _, _, extrude = plate(window)
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


def test_the_2d_tab_is_a_sketch_to_test_on_with_no_part_tools(window: MainWindow) -> None:
    assert window.mode == "2d"
    assert not window.features.isVisible()
    assert not window.sketch_action.isEnabled()
    assert not window.extrude_action.isEnabled()
    assert window.sketch_label.text() == "Editing Sketch 1  ·  Top"
    window.sketch_action.trigger()
    window.extrude_action.trigger()
    assert len(window.session.document.features) == 1


# --- The Extrude panel ------------------------------------------------------------------------


def test_extrude_sends_one_command_and_shows_the_solid(window: MainWindow) -> None:
    sketch_on(window)
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    window.extrude_action.trigger()  # with the sketch open: it's finished first
    assert window.sketch_open is None
    form = window.extrude_form
    assert form.isVisible()
    form.depth.setText("10")
    form.confirm.click()
    assert not form.isVisible()
    assert isinstance(window.session.document.features[-1], Extrude)
    assert window.views.currentWidget() is window.view3d
    assert window.session.history[-1].label == "Extrude"
    assert QApplication.focusWidget() is window.view3d  # not left in the closed form


def test_extrude_explains_an_open_profile_and_stays_open(window: MainWindow) -> None:
    sketch_on(window)
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
    sketch = sketch_on(window)
    (outline,) = window.session.execute(  # type: ignore[union-attr]
        CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50)
    ).created_ids
    window.session.execute(CreateCircle(center=Point2(x=300, y=0), radius=5))  # elsewhere
    window.finish_sketch()
    window.session.set_active_sketch(sketch)
    window.session.set_selection(frozenset({outline}))
    window.extrude_action.trigger()
    window.extrude_form.confirm.click()
    extrude = window.session.document.features[-1]
    assert isinstance(extrude, Extrude)
    assert extrude.ids == (outline,)
    QApplication.processEvents()
    assert window.features.volume.text() == "60,000 mm³"


def test_a_part_with_no_sketch_has_nothing_to_extrude(window: MainWindow) -> None:
    window.set_mode("3d")
    window.extrude_action.trigger()
    assert window.statusBar().currentMessage().startswith("The part has no sketch")


def test_a_depth_that_isnt_a_number_is_explained(window: MainWindow) -> None:
    sketch_on(window)
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    window.extrude_action.trigger()
    form = window.extrude_form
    form.depth.setText("deep")
    form.confirm.click()
    assert form.isVisible()
    assert form.error.text() == "Type a depth in mm"
    assert len(window.session.document.features) == 1


def test_the_extrude_panel_takes_return_and_escape(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    sketch_on(window)
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
    extrude = window.session.document.features[-1]
    assert isinstance(extrude, Extrude)
    assert extrude.depth == 12.5


# --- The rest of the window, with several sketches (F7) -------------------------------------


def test_the_sketch_size_checks_measure_the_sketch_being_edited(window: MainWindow) -> None:
    """With two sketches, "all the geometry" spans planes and has no one size: the Checks
    panel's Sketch width names the edited sketch's own geometry."""
    first, rectangle, _ = plate(window)
    sketch_on(window, Plane.XZ)
    labels = [o.label for o in options(window.session)]
    assert "Sketch width" not in labels  # nothing drawn here yet: no size to check
    (circle,) = window.session.execute(  # type: ignore[union-attr]
        CreateCircle(center=Point2(x=0, y=0), radius=5)
    ).created_ids
    width = next(o for o in options(window.session) if o.label == "Sketch width")
    assert width.ids == (circle,)
    result = window.session.queries.check(
        Expectation(metric=width.metric, expected=10.0, tolerance=1e-6, ids=width.ids)
    )
    assert result.error is None
    assert result.passed
    window.edit_sketch(first)
    width = next(o for o in options(window.session) if o.label == "Sketch width")
    assert width.ids == (rectangle,)


def test_with_one_sketch_the_size_checks_name_no_ids(window: MainWindow) -> None:
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    width = next(o for o in options(window.session) if o.label == "Sketch width")
    assert width.ids == ()  # the whole sketch, as a V1 file's check always said


def test_a_proposal_is_drawn_only_where_it_lands_in_the_sketch_being_edited(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another sketch is on another plane: its proposed geometry would be drawn in the wrong
    place on this one's canvas, so the preview shows only the edited sketch's changes."""
    first, _, _ = plate(window)
    second = sketch_on(window, Plane.XZ)
    plan = Plan(
        label="Two Circles",
        explanation="One in each sketch",
        commands=(
            CreateCircle(center=Point2(x=0, y=0), radius=5, sketch=first),
            CreateCircle(center=Point2(x=30, y=0), radius=7, sketch=second),
        ),
    )
    assert not window.agent.propose(plan, window.session.document).errors
    drawn: list[object] = []
    real = ModelPainter.geometry
    monkeypatch.setattr(
        ModelPainter, "geometry", lambda self, entity: (drawn.append(entity), real(self, entity))
    )
    window.canvas.grab()
    radii = {getattr(e, "radius", None) for e in drawn}
    assert 7.0 in radii  # the second sketch's circle, where it lands
    assert 5.0 not in radii  # not the first sketch's, on another plane


def test_the_facing_camera_looks_straight_at_each_plane_and_face() -> None:
    """Right is the plane's x and up its y, so the plane reads as a sketch does; a face's
    frame (ADR 0016) is faced the same way, the bottom of a plate from below."""
    bottom = faces.canonical(Point3(x=0.0, y=0.0, z=-1.0), Point3(x=0.0, y=0.0, z=0.0))
    side = faces.canonical(Point3(x=0.6, y=-0.8, z=0.0), Point3(x=3.0, y=0.0, z=0.0))
    for frame, right, up in (
        (part.frame(Plane.XY), (1, 0, 0), (0, 1, 0)),
        (part.frame(Plane.XZ), (1, 0, 0), (0, 0, 1)),
        (part.frame(Plane.YZ), (0, 1, 0), (0, 0, 1)),
        (bottom, (1, 0, 0), (0, -1, 0)),
        (side, (0.8, 0.6, 0), (0, 0, 1)),
    ):
        r, u, _ = facing_camera(frame, Point2(x=0, y=0), 1.0).axes()
        assert (r.x, r.y, r.z) == pytest.approx(right, abs=1e-12)
        assert (u.x, u.y, u.z) == pytest.approx(up, abs=1e-12)
