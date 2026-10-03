"""Drawing on a sketch's plane with the view turned to an angle (ADR 0016): what's drawn and
picked lands where the pointer meets the plane, curves are drawn as the plane is seen, and
facing the sketch (or in 2D) the canvas is exactly as before."""

import math

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from caliper.app.main_window import MainWindow
from caliper.app.viewport.backdrop import plane_view
from caliper.app.viewport.camera3d import Camera
from caliper.app.viewport.painter import ModelPainter, cosmetic_pen
from caliper.app.viewport.transform import PlaneView
from caliper.contracts.commands import Applied, CreateCircle, CreateLine, CreateRectangle
from caliper.contracts.document import Circle, Line, Plane, Point2
from caliper.engine import features, geometry, part
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.app.parts import sketch_on


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def tilt(window: MainWindow, qtbot, dx: int = 40, dy: int = -20) -> PlaneView:  # type: ignore[no-untyped-def]
    """Right-drag the canvas by (dx, dy): turned away, still drawable."""
    canvas = window.canvas
    qtbot.mousePress(canvas, Qt.MouseButton.RightButton, pos=QPoint(300, 300))
    qtbot.mouseMove(canvas, QPoint(300 + dx, 300 + dy))
    qtbot.mouseRelease(canvas, Qt.MouseButton.RightButton, pos=QPoint(300 + dx, 300 + dy))
    backdrop = canvas.backdrop
    assert backdrop is not None
    assert backdrop.drawable
    assert not backdrop.facing
    mapping = canvas.mapping
    assert isinstance(mapping, PlaneView)
    return mapping


def at(mapping: PlaneView, x: float, y: float) -> QPoint:
    wx, wy = mapping.to_widget(Point2(x=x, y=y))
    return QPoint(round(wx), round(wy))


def test_facing_and_in_2d_the_canvas_draws_through_its_own_view(window: MainWindow) -> None:
    assert window.canvas.mapping is window.canvas.view  # 2D
    sketch_on(window)
    assert window.canvas.mapping is window.canvas.view  # facing in 3D


def test_a_circle_drawn_at_an_angle_is_where_the_pointer_meets_the_plane(
    window: MainWindow,
    qtbot,
    driver,  # type: ignore[no-untyped-def]
) -> None:
    window.canvas.snap_to_grid = False
    sketch_on(window)
    mapping = tilt(window, qtbot)
    driver.tool("Circle")
    centre, edge = at(mapping, 20, 10), at(mapping, 35, 10)
    qtbot.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=centre)
    qtbot.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=edge)
    (circle,) = window.session.document.entities.values()
    assert isinstance(circle, Circle)
    expected = mapping.to_model(centre.x(), centre.y())
    assert (circle.center.x, circle.center.y) == pytest.approx((expected.x, expected.y), abs=1e-6)
    rim = mapping.to_model(edge.x(), edge.y())
    assert circle.radius == pytest.approx(
        math.hypot(rim.x - expected.x, rim.y - expected.y), abs=1e-6
    )


def test_snapping_at_an_angle_takes_the_feature_under_the_pointer(
    window: MainWindow,
    qtbot,
    driver,  # type: ignore[no-untyped-def]
) -> None:
    sketch = sketch_on(window)
    result = window.session.execute(
        CreateLine(start=Point2(x=0.0, y=0.0), end=Point2(x=30.0, y=0.0), sketch=sketch)
    )
    assert isinstance(result, Applied)
    mapping = tilt(window, qtbot)
    driver.tool("Line")
    off = mapping.to_widget(Point2(x=30, y=0))
    near = QPoint(round(off[0]) + 3, round(off[1]) + 2)  # 4 pixels off the end
    qtbot.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=near)
    qtbot.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=at(mapping, 30, 25))
    lines = [e for e in window.session.document.entities.values() if isinstance(e, Line)]
    assert len(lines) == 2
    assert lines[1].start == Point2(x=30.0, y=0.0)  # snapped to the first line's end


def test_a_box_at_an_angle_takes_what_it_covers_on_screen(
    window: MainWindow,
    qtbot,
    driver,  # type: ignore[no-untyped-def]
) -> None:
    sketch = sketch_on(window)
    inside = window.session.execute(
        CreateCircle(center=Point2(x=10.0, y=10.0), radius=2.0, sketch=sketch)
    ).created_ids[0]  # type: ignore[union-attr]
    window.session.execute(
        CreateRectangle(corner=Point2(x=60.0, y=60.0), width=5.0, height=5.0, sketch=sketch)
    )
    mapping = tilt(window, qtbot)
    driver.tool("Select")
    corners = [mapping.to_widget(Point2(x=x, y=y)) for x in (0, 20) for y in (0, 20)]
    left, top = min(c[0] for c in corners) - 5, min(c[1] for c in corners) - 5
    right, bottom = max(c[0] for c in corners) + 5, max(c[1] for c in corners) + 5
    qtbot.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(round(left), round(top)))
    qtbot.mouseMove(window.canvas, QPoint(round(right), round(bottom)))
    qtbot.mouseRelease(
        window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(round(right), round(bottom))
    )
    assert window.session.selection == frozenset({inside})


def test_n_faces_the_sketch_again_and_drawing_is_as_before(
    window: MainWindow,
    qtbot,
    driver,  # type: ignore[no-untyped-def]
) -> None:
    sketch_on(window)
    tilt(window, qtbot)
    window.face_action.trigger()
    assert window.canvas.mapping is window.canvas.view
    driver.tool("Rectangle")
    driver.drag([(0, 0), (40, 20)])
    assert len(window.session.document.entities) == 1


class Recording(QPainter):
    """A painter that keeps every path drawn."""

    def __init__(self, device: QImage) -> None:
        super().__init__(device)
        self.paths: list[QPainterPath] = []

    def drawPath(self, path: QPainterPath) -> None:  # type: ignore[override]  # noqa: N802
        self.paths.append(QPainterPath(path))
        super().drawPath(path)


@pytest.mark.parametrize("pitch", [50.0, -50.0])
def test_an_arc_at_an_angle_ends_where_its_ends_are(pitch: float) -> None:
    """Drawn as a model-space path under the plane view (Qt's angles negated for Y up), an
    arc's path starts and ends at its ends' widget points, seen from above or from behind, to
    Qt's curve approximation (a hundredth of a pixel)."""
    view = plane_view(Camera(yaw=-30.0, pitch=pitch, scale=3.0), part.frame(Plane.XY), 400, 300)
    image = QImage(400, 300, QImage.Format.Format_ARGB32)
    qp = Recording(image)
    painter = ModelPainter(qp, view)
    painter.set_pen(cosmetic_pen(QColor("black"), 1.0))
    painter.arc(Point2(x=5.0, y=-3.0), 12.0, 30.0, 100.0)
    qp.end()
    (path,) = qp.paths
    for point, angle in ((path.pointAtPercent(0.0), 30.0), (path.pointAtPercent(1.0), 130.0)):
        t = math.radians(angle)
        want = view.to_widget(Point2(x=5.0 + 12.0 * math.cos(t), y=-3.0 + 12.0 * math.sin(t)))
        assert (point.x(), point.y()) == pytest.approx(want, abs=0.02)
