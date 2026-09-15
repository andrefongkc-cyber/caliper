"""Canvas navigation, painting, feature snapping, and the dimension tool."""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QWheelEvent
from PySide6.QtWidgets import QApplication

from caliper.app import theme
from caliper.app.tools.base import SnapKind
from caliper.contracts.commands import (
    CreateArc,
    CreateCircle,
    CreateDistanceDimension,
    CreateLine,
    CreateRadialDimension,
    CreateRectangle,
)
from caliper.contracts.document import (
    DistanceOrientation,
    Feature,
    Point2,
    RadialMeasure,
    Ref,
)


def wheel(canvas, at: QPoint, angle: int = 0, pixels: QPoint | None = None) -> None:
    position = QPointF(at)
    event = QWheelEvent(
        position,
        canvas.mapToGlobal(position),
        pixels or QPoint(),
        QPoint(0, angle),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase if pixels is None else Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(canvas, event)


def pixel(window, x: float, y: float) -> QColor:
    image = window.canvas.grab().toImage()
    at = window.canvas.view.to_widget(Point2(x=x, y=y))
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(at[0] * ratio), round(at[1] * ratio))


def draw_everything(session) -> tuple[str, str]:
    (rect,) = session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50)
    ).created_ids
    session.execute(CreateLine(start=Point2(x=-40, y=-40), end=Point2(x=-10, y=60)))
    (circle,) = session.execute(CreateCircle(center=Point2(x=160, y=25), radius=20)).created_ids
    session.execute(
        CreateArc(center=Point2(x=60, y=100), radius=30, start_angle=0, sweep_angle=180)
    )
    session.execute(
        CreateDistanceDimension(
            a=Ref(entity=rect, feature=Feature.BOTTOM_LEFT),
            b=Ref(entity=rect, feature=Feature.BOTTOM_RIGHT),
            orientation=DistanceOrientation.ALIGNED,
            offset=-15,
        )
    )
    session.execute(
        CreateRadialDimension(target=circle, measure=RadialMeasure.DIAMETER, label_angle=45)
    )
    return rect, circle


def test_wheel_zooms_about_the_pointer(window, driver) -> None:
    canvas = window.canvas
    at = driver.at(40, 30)
    before = canvas.view.scale
    wheel(canvas, at, angle=120)
    assert canvas.view.scale > before
    under = canvas.view.to_model(at.x(), at.y())
    assert under.x == pytest.approx(40, abs=0.2)
    assert under.y == pytest.approx(30, abs=0.2)


def test_trackpad_scroll_pans(window) -> None:
    canvas = window.canvas
    before = (canvas.view.origin_x, canvas.view.origin_y, canvas.view.scale)
    wheel(canvas, QPoint(300, 300), pixels=QPoint(12, -8))
    assert (canvas.view.origin_x, canvas.view.origin_y, canvas.view.scale) == (
        before[0] + 12,
        before[1] - 8,
        before[2],
    )


def test_middle_drag_pans_without_touching_the_tool(window, driver, bus, qtbot) -> None:
    canvas = window.canvas
    driver.tool("Line")
    origin = canvas.view.origin_x
    qtbot.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(200, 200))
    driver.buttons = Qt.MouseButton.MiddleButton
    driver.move(*_model(canvas, 260, 200))
    qtbot.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(260, 200))
    assert canvas.view.origin_x == pytest.approx(origin + 60)
    assert not window.controller.active.busy
    assert bus.sent == []


def _model(canvas, px: float, py: float) -> tuple[float, float]:
    p = canvas.view.to_model(px, py)
    return p.x, p.y


def test_zoom_to_fit_frames_the_geometry(window) -> None:
    draw_everything(window.session)
    window.fit_action.trigger()
    box = window.canvas.visible_box()
    assert box.x_min <= -40
    assert box.x_max >= 180
    assert box.y_min <= -40
    assert box.y_max >= 130


def test_zoom_to_fit_on_an_empty_document_resets(window) -> None:
    window.fit_action.trigger()
    assert window.canvas.view.origin_x == window.canvas.width() / 2


@pytest.mark.bus(missing=("nearest_feature", "feature_point"))
def test_paints_every_entity_kind(window) -> None:
    draw_everything(window.session)
    assert pixel(window, 50, 0) != theme.CANVAS  # rectangle bottom edge
    assert pixel(window, 52, 27) == theme.CANVAS  # rectangle interior, between grid lines
    # Distance dimensions need feature_point, which the engine doesn't have yet.
    window.canvas.grab()
    assert window.canvas.hidden_dimensions == 1


def test_selection_is_painted_in_the_accent_color(window) -> None:
    rect, _ = draw_everything(window.session)
    window.session.set_selection(frozenset({rect}))
    assert pixel(window, 0, 25) == theme.SELECTED


@pytest.mark.bus(complete_queries=True)
def test_dimensions_draw_once_feature_points_exist(window) -> None:
    draw_everything(window.session)
    window.canvas.grab()
    assert window.canvas.hidden_dimensions == 0


@pytest.mark.bus(complete_queries=True)
def test_pointer_snaps_to_features_before_the_grid(window, driver) -> None:
    draw_everything(window.session)
    near_corner = window.canvas.pointer_at(
        QPointF(driver.at(100.6, 0.4)), Qt.KeyboardModifier.NoModifier
    )
    assert near_corner.snap is SnapKind.FEATURE
    assert near_corner.point == Point2(x=100.0, y=0.0)
    assert near_corner.ref is not None
    assert near_corner.ref.feature is Feature.BOTTOM_RIGHT
    open_space = window.canvas.pointer_at(
        QPointF(driver.at(301, 301)), Qt.KeyboardModifier.NoModifier
    )
    assert open_space.snap is SnapKind.GRID


@pytest.mark.bus(missing=("nearest_feature", "feature_point"))
def test_without_point_queries_the_pointer_snaps_to_the_grid(window, driver) -> None:
    draw_everything(window.session)
    pointer = window.canvas.pointer_at(
        QPointF(driver.at(100.6, 0.4)), Qt.KeyboardModifier.NoModifier
    )
    assert pointer.snap is SnapKind.GRID


@pytest.mark.bus(complete_queries=True)
def test_dimension_tool_point_to_point(window, driver, bus) -> None:
    rect, _ = draw_everything(window.session)
    bus.sent.clear()
    driver.tool("Dimension")
    driver.click(0, 50)
    driver.click(100, 50)
    driver.click(50, 65)
    assert bus.sent == [
        CreateDistanceDimension(
            a=Ref(entity=rect, feature=Feature.TOP_LEFT),
            b=Ref(entity=rect, feature=Feature.TOP_RIGHT),
            orientation=DistanceOrientation.ALIGNED,
            offset=15.0,
        )
    ]


@pytest.mark.bus(complete_queries=True)
def test_dimension_tool_on_a_circle_makes_a_diameter(window, driver, bus) -> None:
    _, circle = draw_everything(window.session)
    bus.sent.clear()
    driver.tool("Dimension")
    driver.click(180, 25)
    assert bus.sent == [
        CreateRadialDimension(target=circle, measure=RadialMeasure.DIAMETER, label_angle=0.0)
    ]


@pytest.mark.bus(missing=("nearest_feature", "feature_point"))
def test_dimension_tool_reports_missing_point_picking(window, driver, bus) -> None:
    draw_everything(window.session)
    bus.sent.clear()
    driver.tool("Dimension")
    driver.click(0, 50)
    assert bus.sent == []
    assert window.statusBar().currentMessage() == "Point picking isn't in the engine yet"


def test_cursor_position_is_shown_in_mm(window, driver) -> None:
    driver.move(25, 10)
    assert window.cursor_label.text().split() == ["X", "25.000", "Y", "10.000", "mm"]
