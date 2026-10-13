"""The 3D tab's everyday handling (the 3D UX fixes, 2026-10-13; docs/workplan/shell.md).

What the audit of the 3D tab found: a trackpad that did one thing in a sketch and another in
3D, a camera left flat on the sketch plane, an Extrude typed blind, and a part that didn't
answer the pointer. CI's app job has no OCCT, so the engine gets the analytic kernel.
"""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QNativeGestureEvent, QPointingDevice, QWheelEvent
from PySide6.QtWidgets import QApplication, QWidget

from caliper.app.main_window import MainWindow
from caliper.engine import features, geometry
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.app.parts import plate

NO_MODIFIER = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
COMMAND = Qt.KeyboardModifier.ControlModifier


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def scroll(
    widget: QWidget,
    at: QPoint,
    *,
    pixels: QPoint | None = None,
    angle: int = 0,
    modifiers: Qt.KeyboardModifier = NO_MODIFIER,
) -> None:
    """A trackpad's two-finger scroll (`pixels`), or a mouse wheel's notch (`angle` alone)."""
    position = QPointF(at)
    event = QWheelEvent(
        position,
        widget.mapToGlobal(position),
        pixels or QPoint(),
        QPoint(0, angle),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase if pixels is None else Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(widget, event)


def pinch(widget: QWidget, at: QPoint, value: float) -> None:
    position = QPointF(at)
    QApplication.sendEvent(
        widget,
        QNativeGestureEvent(
            Qt.NativeGestureType.ZoomNativeGesture,
            QPointingDevice.primaryPointingDevice(),
            2,
            position,
            position,
            widget.mapToGlobal(position),
            value,
            QPointF(),
            0,
        ),
    )


# --- F1: a trackpad moves the part as it moves a sketch --------------------------------------


def test_two_fingers_pan_the_part_as_they_pan_a_sketch(window: MainWindow) -> None:
    plate(window)
    view = window.view3d
    start = view.camera
    under = start.project(start.target, view.width(), view.height())
    scroll(view, QPoint(300, 300), pixels=QPoint(12, -8))
    assert view.camera.scale == start.scale  # not a zoom, as it was before
    assert (view.camera.yaw, view.camera.pitch) == (start.yaw, start.pitch)
    moved = view.camera.project(start.target, view.width(), view.height())
    assert (moved.x - under.x, moved.y - under.y) == pytest.approx((12, -8))  # with the fingers


def test_shift_with_two_fingers_orbits(window: MainWindow) -> None:
    plate(window)
    view = window.view3d
    start = view.camera
    scroll(view, QPoint(300, 300), pixels=QPoint(30, 10), modifiers=SHIFT)
    assert view.camera == start.orbited(30, 10)
    assert view.camera.target == start.target


def test_a_pinch_and_command_scroll_zoom_about_the_pointer(window: MainWindow) -> None:
    plate(window)
    view = window.view3d
    at = QPoint(120, 90)

    def under() -> tuple[float, float]:
        """Where on the screen's plane the pixel `at` looks, in mm from the target's."""
        camera = view.camera
        right, up, _ = camera.axes()
        u = (at.x() - view.width() / 2) / camera.scale
        v = -(at.y() - view.height() / 2) / camera.scale
        t = camera.target
        return (
            u + t.x * right.x + t.y * right.y + t.z * right.z,
            v + t.x * up.x + t.y * up.y + t.z * up.z,
        )

    start, held = view.camera, under()
    pinch(view, at, 0.25)
    assert view.camera.scale == pytest.approx(start.scale * 1.25)
    assert under() == pytest.approx(held)
    pinched = view.camera
    scroll(view, at, pixels=QPoint(0, 6), angle=60, modifiers=COMMAND)
    assert view.camera.scale > pinched.scale
    assert under() == pytest.approx(held)
    assert (view.camera.yaw, view.camera.pitch) == (start.yaw, start.pitch)


def test_a_mouse_wheel_still_zooms(window: MainWindow) -> None:
    plate(window)
    view = window.view3d
    start = view.camera
    scroll(view, QPoint(300, 300), angle=120)
    assert view.camera.scale == pytest.approx(start.scale * 1.15)
    scroll(view, QPoint(300, 300), angle=-120, modifiers=SHIFT)  # a wheel never orbits
    assert view.camera.scale == pytest.approx(start.scale)
    assert (view.camera.yaw, view.camera.pitch) == (start.yaw, start.pitch)


def test_a_sketch_turned_away_from_scrolls_as_the_part_does(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    sketch, _, _ = plate(window)
    window.edit_sketch(sketch)
    canvas = window.canvas
    qtbot.mousePress(canvas, Qt.MouseButton.RightButton, pos=QPoint(300, 300))
    qtbot.mouseMove(canvas, QPoint(340, 330))
    qtbot.mouseRelease(canvas, Qt.MouseButton.RightButton, pos=QPoint(340, 330))
    backdrop = canvas.backdrop
    assert backdrop is not None
    assert backdrop.free is not None
    free = backdrop.free
    scroll(canvas, QPoint(300, 300), pixels=QPoint(12, -8))
    assert backdrop.free == free.panned(12, -8)
    scroll(canvas, QPoint(300, 300), pixels=QPoint(20, 0), modifiers=SHIFT)
    assert backdrop.free == free.panned(12, -8).orbited(20, 0)
