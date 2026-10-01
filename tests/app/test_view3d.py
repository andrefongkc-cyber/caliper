"""The 3D view and the 2D/3D switch (V2's F5, ADR 0012): one document behind both views.

CI's app job has no OCCT, so these tests give the engine the analytic kernel: the app never
chooses a kernel itself, and the engine's default is looked up when queries are made.
"""

import statistics

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from caliper.app import theme
from caliper.app.main_window import MainWindow
from caliper.contracts.commands import (
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
)
from caliper.contracts.document import EntityId, Point2
from caliper.contracts.errors import Error
from caliper.contracts.queries import Point3
from caliper.engine import features, geometry
from caliper.engine.geometry.fake_kernel import FakeKernel

PLATE, EXTRUDE = EntityId("e1"), EntityId("e2")


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def extruded(window: MainWindow, width: float = 120.0) -> None:
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=width, height=50))
    window.session.execute(CreateExtrude(depth=10.0))


def volume(window: MainWindow) -> float:
    found = window.session.queries.solid_properties()
    assert not isinstance(found, Error), found
    return found.volume


def center_color(window: MainWindow) -> str:
    image = window.view3d.grab().toImage()
    return image.pixelColor(image.width() // 2, image.height() // 2).name()


# --- Switching --------------------------------------------------------------------------------


def test_switching_shows_the_other_view_and_leaves_the_document_alone(window: MainWindow) -> None:
    extruded(window)
    window.session.set_selection(frozenset({PLATE}))
    document, history = window.session.document, window.session.history
    undo = window.session.bus.undo_label
    for mode in ("3d", "2d", "3d", "2d"):
        window.set_mode(mode)
        QApplication.processEvents()
        assert window.views.currentWidget() is (window.view3d if mode == "3d" else window.canvas)
        assert window.session.document is document  # the very same object
        assert window.session.history == history
        assert window.session.bus.undo_label == undo
        assert window.session.selection == {PLATE}


def test_the_toggle_is_in_the_toolbar_and_on_command_and_keys(window: MainWindow) -> None:
    buttons = {
        b.objectName()
        for b in window.tool_bar.findChildren(
            type(window.tool_bar.widgetForAction(window.mode_2d_action))
        )
    }
    assert {"mode-2d", "mode-3d"} <= buttons
    assert window.mode_2d_action.isChecked()
    window.mode_3d_action.trigger()
    assert window.mode == "3d"
    assert window.mode_3d_action.isChecked()
    assert not window.mode_2d_action.isChecked()
    assert window.mode_3d_action.shortcut().toString() == "Ctrl+2"
    window.mode_2d_action.trigger()
    assert window.mode == "2d"


def test_sketch_tools_wait_in_3d_and_work_again_in_2d(window: MainWindow, driver) -> None:  # type: ignore[no-untyped-def]
    window.set_mode("3d")
    assert not window.tool_actions["Rectangle"].isEnabled()
    assert not window.grid_action.isEnabled()
    assert window.undo_action is not None
    window.set_mode("2d")
    assert window.tool_actions["Rectangle"].isEnabled()
    driver.tool("Rectangle")
    driver.drag([(0, 0), (40, 20)])
    assert len(window.session.document.entities) == 1  # 2D drawing works as ever


def test_undo_and_redo_work_in_3d_and_the_view_follows(window: MainWindow) -> None:
    extruded(window)
    window.set_mode("3d")
    window.session.execute(ModifyEntity(id=PLATE, changes={"width": 140.0}))
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
    extruded(window)
    window.set_mode("3d")
    QApplication.processEvents()
    view = window.view3d
    assert view.mesh is not None
    assert len(view.mesh.triangles) == 12
    assert view.problem is None
    assert center_color(window) != theme.CANVAS.name()  # the plate fills the middle
    assert view.frame_ms  # each frame is timed


def test_with_no_solid_it_says_how_to_make_one(window: MainWindow) -> None:
    window.set_mode("3d")
    QApplication.processEvents()
    assert window.view3d.mesh is None
    assert "extrude" in (window.view3d.problem or "")
    assert center_color(window) == theme.CANVAS.name()


def test_without_a_kernel_it_says_what_to_install(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(geometry, "default_kernel", lambda: None)
    extruded(window)
    window.set_mode("3d")
    assert "occt" in (window.view3d.problem or "")


def test_a_failing_extrude_keeps_the_last_solid_on_screen_with_the_reason(
    window: MainWindow,
) -> None:
    corners = [Point2(x=0, y=0), Point2(x=40, y=0), Point2(x=40, y=30)]
    for a, b in zip(corners, [*corners[1:], corners[0]], strict=True):
        window.session.execute(CreateLine(start=a, end=b))
    window.session.execute(CreateExtrude(depth=5.0))
    window.set_mode("3d")
    shown = window.view3d.mesh
    assert shown is not None
    window.session.execute(DeleteEntities(ids=(EntityId("e1"),)))  # breaks the profile
    QApplication.processEvents()
    assert window.view3d.mesh is shown
    assert "last solid" in (window.view3d.problem or "")


def test_edits_made_in_2d_are_there_when_3d_is_shown_again(window: MainWindow) -> None:
    extruded(window)
    window.set_mode("3d")
    window.set_mode("2d")
    window.session.execute(ModifyEntity(id=PLATE, changes={"width": 140.0}))
    # Layout geometry: not part of the profile, which is the sketch's other geometry.
    window.session.execute(CreateCircle(center=Point2(x=200, y=200), radius=5, construction=True))
    assert window.view3d._stale  # not asked while hidden
    window.set_mode("3d")
    QApplication.processEvents()
    mesh = window.view3d.mesh
    assert mesh is not None
    assert max(p.x for p in mesh.vertices) == 140.0


# --- Moving the camera ----------------------------------------------------------------------


def test_drags_orbit_and_pan_and_the_wheel_zooms(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    extruded(window)
    window.set_mode("3d")
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
    assert view.camera.scale < scale * 1.15


def test_frames_of_a_detailed_part_take_milliseconds(window: MainWindow) -> None:
    """A plate with 24 round holes: thousands of triangles, drawn without a GPU. The bound is
    loose for CI; ADR 0012 records this Mac's numbers."""
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=240, height=160))
    for i in range(6):
        for j in range(4):
            window.session.execute(
                CreateCircle(center=Point2(x=20 + 40 * i, y=20 + 40 * j), radius=8)
            )
    window.session.execute(CreateExtrude(depth=10.0))
    window.set_mode("3d")
    view = window.view3d
    assert view.mesh is not None
    assert len(view.mesh.triangles) > 1_000
    view.frame_ms.clear()
    for _ in range(10):
        view.repaint()
    assert statistics.median(view.frame_ms) < 500
