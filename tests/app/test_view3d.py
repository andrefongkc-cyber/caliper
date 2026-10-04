"""The 3D view and the 2D/3D switch (V2's F5, ADR 0012, ADR 0015).

The 3D tab is the part: it starts with its origin and its Top, Front, and Right planes, and no
sketch. The 2D tab is a sketch to test on, a document of its own. CI's app job has no OCCT,
so these tests give the engine the analytic kernel: the app never chooses a kernel itself, and
the engine's default is looked up when queries are made.
"""

import statistics

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QToolButton

from caliper.app import theme
from caliper.app.main_window import MainWindow
from caliper.app.session import DocumentSession, Space
from caliper.contracts.commands import (
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
)
from caliper.contracts.document import ExtrudeOperation, Plane, Point2
from caliper.contracts.errors import Error
from caliper.contracts.queries import Point3
from caliper.engine import features, geometry, part
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.app.parts import plate, sketch_on


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def volume(window: MainWindow) -> float:
    found = window.session.queries.solid_properties()
    assert not isinstance(found, Error), found
    return found.volume


def center(window: MainWindow) -> tuple[float, float]:
    view = window.view3d
    return view.width() / 2, view.height() / 2


# --- The tabs ---------------------------------------------------------------------------------


def test_the_app_starts_in_3d_on_a_part_with_only_its_planes(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(DocumentSession())
    qtbot.addWidget(window)
    window.confirm_discard = lambda: True  # type: ignore[method-assign]
    window.show()
    qtbot.waitExposed(window)
    assert window.mode == "3d"
    assert window.mode_3d_action.isChecked()
    assert window.views.currentWidget() is window.view3d
    assert window.session.document == part.no_sketch()
    assert "Pick a plane" in (window.view3d.problem or "")
    rows = [window.features.planes[p].text(0) for p in (Plane.XY, Plane.XZ, Plane.YZ)]
    assert rows == ["Top", "Front", "Right"]
    assert not window.tool_actions["Rectangle"].isEnabled()  # nothing to draw on yet
    window.new_action.trigger()
    assert window.session.document == part.no_sketch()  # New in 3D: a part, planes only


def test_each_tab_is_its_own_document_with_its_own_history(window: MainWindow) -> None:
    (circle,) = window.session.execute(  # type: ignore[union-attr]
        CreateCircle(center=Point2(x=0, y=0), radius=5)
    ).created_ids
    window.session.set_selection(frozenset({circle}))
    sketch_doc, sketch_history = window.session.document, window.session.history
    _, rectangle, _ = plate(window)
    assert window.session.space is Space.PART
    assert circle not in window.session.document.entities  # the part never had the circle
    window.session.set_selection(frozenset({rectangle}))
    part_doc, part_undo = window.session.document, window.session.bus.undo_label
    for mode in ("2d", "3d", "2d", "3d"):
        window.set_mode(mode)
        QApplication.processEvents()
        if mode == "2d":
            assert window.views.currentWidget() is window.canvas
            assert window.session.document is sketch_doc  # the very same object
            assert window.session.history == sketch_history
            assert window.session.selection == {circle}
            assert not window.features.isVisible()  # a sketch to test on: no Part panel
            assert not window.extrude_action.isEnabled()
        else:
            assert window.views.currentWidget() is window.view3d
            assert window.session.document is part_doc
            assert window.session.bus.undo_label == part_undo
            assert window.session.selection == {rectangle}
            assert window.features.isVisible()


def test_the_toggle_is_in_the_toolbar_and_on_command_and_keys(window: MainWindow) -> None:
    buttons = {b.objectName() for b in window.tool_bar.findChildren(QToolButton)}
    assert {"mode-2d", "mode-3d", "sketch"} <= buttons
    assert window.mode_2d_action.isChecked()
    window.mode_3d_action.trigger()
    assert window.mode == "3d"
    assert window.mode_3d_action.isChecked()
    assert not window.mode_2d_action.isChecked()
    assert window.mode_3d_action.shortcut().toString() == "Ctrl+2"
    window.mode_2d_action.trigger()
    assert window.mode == "2d"


def test_switching_tabs_keeps_where_the_3d_view_looks(window: MainWindow) -> None:
    plate(window)
    view = window.view3d
    view.camera = view.camera.orbited(40, 10)
    turned = view.camera
    window.set_mode("2d")
    window.set_mode("3d")
    assert view.camera == turned


def test_sketch_tools_wait_in_3d_until_a_sketch_is_open(window: MainWindow, driver) -> None:  # type: ignore[no-untyped-def]
    window.set_mode("3d")
    assert not window.tool_actions["Rectangle"].isEnabled()
    assert not window.grid_action.isEnabled()
    sketch_on(window)
    assert window.tool_actions["Rectangle"].isEnabled()
    window.finish_sketch()
    assert not window.tool_actions["Rectangle"].isEnabled()
    window.set_mode("2d")
    assert window.tool_actions["Rectangle"].isEnabled()
    driver.tool("Rectangle")
    driver.drag([(0, 0), (40, 20)])
    assert len(window.session.document.entities) == 1  # 2D drawing works as ever


def test_undo_and_redo_work_in_3d_and_the_view_follows(window: MainWindow) -> None:
    _, rectangle, _ = plate(window)
    window.session.execute(ModifyEntity(id=rectangle, changes={"width": 140.0}))
    QApplication.processEvents()
    box = window.session.queries.solid_properties()
    assert not isinstance(box, Error)
    assert box.bounding_box is not None
    assert box.bounding_box.x_max == 140.0
    window.undo_action.trigger()
    assert volume(window) == pytest.approx(60_000.0)
    window.redo_action.trigger()
    assert volume(window) == pytest.approx(70_000.0)


# --- What the 3D view shows ---------------------------------------------------------------


def test_the_solid_is_drawn_from_the_engines_mesh(window: MainWindow) -> None:
    plate(window)
    QApplication.processEvents()
    view = window.view3d
    assert view.mesh is not None
    assert len(view.mesh.triangles) == 12
    assert view.problem is None
    assert view.frame_ms  # each frame is timed
    assert [c.sketch for c in view.scene.curves] == [window.session.active_sketch]


def test_with_no_solid_the_planes_show_and_it_says_how_to_start(window: MainWindow) -> None:
    window.set_mode("3d")
    QApplication.processEvents()
    view = window.view3d
    assert view.mesh is None
    assert "Pick a plane" in (view.problem or "")
    sketch_on(window, Plane.XZ)
    window.finish_sketch()
    assert view.problem is None  # a sketch to extrude: the Part panel says there's no solid
    assert window.features.volume.text() == "No solid yet"


def test_in_a_narrow_view_what_it_says_wraps_instead_of_running_off_the_edges(
    window: MainWindow,
) -> None:
    """The line under the part, "Pick a plane and press Sketch, or double-click a plane", is
    wider than a narrow view: it wraps upwards, none of it cut off at either edge."""
    window.set_mode("3d")
    window.resize(window.minimumSizeHint().width(), 600)
    QApplication.processEvents()
    view = window.view3d
    assert "Pick a plane" in (view.problem or "")
    wide = view.fontMetrics().horizontalAdvance(view.problem or "")
    assert wide > view.width() - 144  # it wouldn't fit on one line beside the triad
    image = view.grab().toImage()
    ratio = image.devicePixelRatio()
    said = theme.TEXT_DIM

    def written(x: int, y: int) -> bool:
        c = image.pixelColor(x, y)
        return abs(c.red() - said.red()) + abs(c.green() - said.green()) < 40 and (
            abs(c.blue() - said.blue()) < 20
        )

    strip = range(int((view.height() - 200) * ratio), image.height())
    edge = int(8 * ratio)
    assert any(written(x, y) for y in strip for x in range(edge, image.width() - edge))
    assert not any(written(x, y) for y in strip for x in range(edge))
    assert not any(written(x, y) for y in strip for x in range(image.width() - edge, image.width()))
    # Above the triad in the corner, not over it.
    corner = range(int((view.height() - 60) * ratio), image.height())
    assert not any(written(x, y) for y in corner for x in range(int(60 * ratio)))


def test_without_a_kernel_it_says_what_to_install(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(geometry, "default_kernel", lambda: None)
    plate(window)
    assert "occt" in (window.view3d.problem or "")


def test_a_part_cut_away_entirely_says_so(window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A cut through all of the solid leaves an empty one, which OCCT makes and the analytic
    kernel can't: the view draws nothing and says why, rather than failing (F8)."""
    occt = pytest.importorskip(
        "caliper.engine.geometry.occt_kernel", reason="the occt extra isn't installed"
    )
    kernel = occt.OCCTKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()
    sketch, _, _ = plate(window)
    assert volume(window) == pytest.approx(60_000.0)
    window.edit_sketch(sketch)
    (block,) = window.session.execute(  # type: ignore[union-attr]
        CreateRectangle(corner=Point2(x=-10, y=-10), width=200, height=100)
    ).created_ids
    window.finish_sketch()
    window.session.execute(
        CreateExtrude(depth=10.0, ids=(block,), operation=ExtrudeOperation.REMOVE)
    )
    assert volume(window) == pytest.approx(0.0, abs=1e-6)
    QApplication.processEvents()
    assert window.view3d.mesh is not None
    assert window.view3d.mesh.triangles == ()
    assert "cut away" in (window.view3d.problem or "")
    assert window.features.volume.text() == "0 mm³"


def test_a_failing_extrude_keeps_the_last_solid_on_screen_with_the_reason(
    window: MainWindow,
) -> None:
    sketch_on(window)
    corners = [Point2(x=0, y=0), Point2(x=40, y=0), Point2(x=40, y=30)]
    lines = [
        window.session.execute(CreateLine(start=a, end=b)).created_ids[0]  # type: ignore[union-attr]
        for a, b in zip(corners, [*corners[1:], corners[0]], strict=True)
    ]
    window.finish_sketch()
    window.session.execute(CreateExtrude(depth=5.0))
    QApplication.processEvents()
    shown = window.view3d.mesh
    assert shown is not None
    window.session.execute(DeleteEntities(ids=(lines[0],)))  # breaks the profile
    QApplication.processEvents()
    assert window.view3d.mesh is shown
    assert "last solid" in (window.view3d.problem or "")


def test_a_sketch_edited_again_reshapes_the_solid_when_it_finishes(window: MainWindow) -> None:
    sketch, rectangle, _ = plate(window)
    window.edit_sketch(sketch)
    window.session.execute(ModifyEntity(id=rectangle, changes={"width": 140.0}))
    # Layout geometry: not part of the profile, which is the sketch's other geometry.
    window.session.execute(CreateCircle(center=Point2(x=200, y=200), radius=5, construction=True))
    window.finish_sketch()
    QApplication.processEvents()
    mesh = window.view3d.mesh
    assert mesh is not None
    assert max(p.x for p in mesh.vertices) == 140.0


# --- Picking --------------------------------------------------------------------------------


def test_a_click_picks_a_plane_and_a_double_click_sketches_on_it(
    window: MainWindow,
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    window.set_mode("3d")
    view = window.view3d
    view.camera = view.camera.orbited(0, 0)  # isometric: every plane faces the viewer a bit
    x, y = center(window)
    # Just above the origin on screen, the Top plane is nearest at an isometric angle.
    found = view.pick(x, y - 20)
    assert isinstance(found, Plane)
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(round(x), round(y - 20)))
    assert window.session.picked_plane is found
    assert window.features.planes[found].isSelected()
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))  # nothing there
    assert window.session.picked_plane is None
    point = QPoint(round(x), round(y - 20))
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=point)
    qtbot.mouseDClick(view, Qt.MouseButton.LeftButton, pos=point)
    assert window.sketch_open is not None
    assert window.views.currentWidget() is window.canvas
    sketch = window.session.document.features[-1]
    assert getattr(sketch, "plane", None) is found


def test_a_click_on_a_sketch_picks_it_for_extrude(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    sketch = sketch_on(window, Plane.XZ)
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=40, height=20))
    window.finish_sketch()
    view = window.view3d
    corner = view.camera.project(Point3(x=40.0, y=0.0, z=10.0), view.width(), view.height())
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(round(corner.x), round(corner.y)))
    assert window.session.selection == {sketch}
    window.extrude_action.trigger()
    window.extrude_form.confirm.click()
    assert volume(window) == pytest.approx(40 * 20 * 10)


# --- Moving the camera ----------------------------------------------------------------------


def test_drags_orbit_and_pan_and_the_wheel_zooms(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    plate(window)
    view = window.view3d
    start = view.camera
    qtbot.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(200, 200))
    qtbot.mouseMove(view, QPoint(260, 180))
    qtbot.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPoint(260, 180))
    assert (view.camera.yaw, view.camera.pitch) != (start.yaw, start.pitch)
    assert view.camera.target == start.target
    turned = view.camera
    qtbot.mousePress(view, Qt.MouseButton.RightButton, pos=QPoint(200, 200))
    qtbot.mouseMove(view, QPoint(230, 200))
    qtbot.mouseRelease(view, Qt.MouseButton.RightButton, pos=QPoint(230, 200))
    assert view.camera.target != turned.target
    assert (view.camera.yaw, view.camera.pitch) == (turned.yaw, turned.pitch)
    scale = view.camera.scale
    at = QPointF(view.width() / 2, view.height() / 2)
    wheel = QWheelEvent(
        at,
        view.mapToGlobal(at),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(view, wheel)
    assert view.camera.scale > scale
    window.fit_action.trigger()  # F fits whichever view is showing: back on the plate
    assert view.camera.target == Point3(x=60.0, y=25.0, z=5.0)
    fitted = view.camera.fitted(view.scene.box(), view.width(), view.height())
    assert view.camera.scale == pytest.approx(fitted.scale)


def test_frames_of_a_detailed_part_take_milliseconds(window: MainWindow) -> None:
    """A plate with 24 round holes: thousands of triangles, drawn without a GPU. The bound is
    loose for CI; ADR 0012 records this Mac's numbers."""
    sketch_on(window)
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=240, height=160))
    for i in range(6):
        for j in range(4):
            window.session.execute(
                CreateCircle(center=Point2(x=20 + 40 * i, y=20 + 40 * j), radius=8)
            )
    window.finish_sketch()
    window.session.execute(CreateExtrude(depth=10.0))
    view = window.view3d
    QApplication.processEvents()
    assert view.mesh is not None
    assert len(view.mesh.triangles) > 1_000
    view.frame_ms.clear()
    for _ in range(10):
        view.repaint()
    assert statistics.median(view.frame_ms) < 500
