"""Canvas navigation, painting, feature snapping, and the dimension tool."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from caliper.app import theme
from caliper.app.tools.base import SnapKind
from caliper.app.viewport.annotations import label_anchors
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


def test_selection_is_painted_in_the_accent_color(window) -> None:
    rect, _ = draw_everything(window.session)
    window.session.set_selection(frozenset({rect}))
    assert pixel(window, 0, 25) == theme.SELECTED


def test_dimensions_draw_once_feature_points_exist(window) -> None:
    draw_everything(window.session)
    window.canvas.grab()
    assert window.canvas.hidden_dimensions == 0


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


def test_dimension_tool_on_a_circle_makes_a_diameter(window, driver, bus) -> None:
    _, circle = draw_everything(window.session)
    bus.sent.clear()
    driver.tool("Dimension")
    driver.click(180, 25)
    assert bus.sent == [
        CreateRadialDimension(target=circle, measure=RadialMeasure.DIAMETER, label_angle=0.0)
    ]


def test_cursor_position_is_shown_in_mm(window, driver) -> None:
    driver.move(25, 10)
    assert window.cursor_label.text().split() == ["X", "25.000", "Y", "10.000", "mm"]


def test_paints_every_entity_kind_including_dimensions(window) -> None:
    draw_everything(window.session)
    assert pixel(window, 50, 0) != theme.CANVAS  # rectangle bottom edge
    assert pixel(window, 52, 27) == theme.CANVAS  # rectangle interior, between grid lines
    window.canvas.grab()
    assert window.canvas.hidden_dimensions == 0


def test_dimension_tool_reports_an_empty_click(window, driver, bus) -> None:
    draw_everything(window.session)
    bus.sent.clear()
    driver.tool("Dimension")
    driver.click(300, 300)
    assert bus.sent == []
    assert window.statusBar().currentMessage() == "No point under the pointer"


def test_hover_and_selection_reuse_the_cached_layer(window, driver) -> None:
    draw_everything(window.session)
    window.canvas.grab()
    layer = window.canvas._layer
    driver.move(50, 0)  # hover the rectangle
    window.session.set_selection(frozenset(window.session.document.entities))
    window.canvas.grab()
    assert window.canvas._layer is layer
    window.session.execute(CreateCircle(center=Point2(x=300, y=0), radius=3))
    window.canvas.grab()
    assert window.canvas._layer is not layer  # a document change redraws it
    layer = window.canvas._layer
    window.canvas.view.pan(5, 0)
    window.canvas.grab()
    assert window.canvas._layer is not layer  # so does moving the view


def test_selected_dimensions_are_drawn_in_the_accent_colour(window) -> None:
    draw_everything(window.session)
    (radial,) = [
        id for id, e in window.session.document.entities.items() if e.kind == "radial_dimension"
    ]

    def accent_pixels() -> int:
        image = window.canvas.grab().toImage()
        target = theme.SELECTED
        return sum(
            1
            for x in range(image.width())
            for y in range(image.height())
            if abs(image.pixelColor(x, y).red() - target.red()) < 40
            and abs(image.pixelColor(x, y).green() - target.green()) < 40
            and abs(image.pixelColor(x, y).blue() - target.blue()) < 40
        )

    window.canvas.zoom_to_fit()  # the fixture's fixed view leaves the circle off-screen
    before = accent_pixels()
    window.session.set_selection(frozenset({radial}))
    assert accent_pixels() > before + 20


def test_empty_canvas_shows_a_hint_until_something_is_drawn(window, qtbot) -> None:
    hint = window.canvas.empty_hint
    window.session.new()
    assert hint.isVisible()
    assert "ask the agent" in hint.text()
    window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    assert not hint.isVisible()
    window.undo_action.trigger()
    assert hint.isVisible()


def test_zoom_to_fit_leaves_room_for_dimension_labels(window) -> None:
    session = window.session
    (plate,) = session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50)
    ).created_ids
    session.execute(
        CreateDistanceDimension(
            a=Ref(entity=plate, feature=Feature.TOP_LEFT),
            b=Ref(entity=plate, feature=Feature.TOP_RIGHT),
            orientation=DistanceOrientation.HORIZONTAL,
            offset=200,  # far above the plate: without padding the label falls outside
        )
    )
    window.fit_action.trigger()
    box = window.canvas.visible_box()
    (anchor, text) = label_anchors(session, window.canvas.view)[0]
    metrics = QFontMetricsF(window.canvas.font())
    view = window.canvas.view
    half_width = view.length_to_model(metrics.horizontalAdvance(text) / 2)
    half_height = view.length_to_model(metrics.height() / 2)
    assert box.y_max > anchor.y + half_height  # the whole label, not just its anchor
    assert box.x_min < anchor.x - half_width
    assert box.x_max > anchor.x + half_width


def test_zoom_to_fit_ignores_labels_when_there_are_none(window) -> None:
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    window.fit_action.trigger()
    box = window.canvas.visible_box()
    assert box.x_min < 0
    assert box.x_max > 120
    assert box.width < 400  # not padded for labels that aren't there


# --- Moving the view reuses the cached layer --------------------------------------------


def _rebuilt_after_settling(canvas, qtbot, layer) -> None:
    def rebuilt() -> bool:
        canvas.grab()
        return canvas._layer is not layer

    qtbot.waitUntil(rebuilt, timeout=2000)


def _brightest(image, at: tuple[float, float], along: str, reach: int = 8) -> int:
    """Offset (in device pixels) of the brightest pixel within `reach` of `at`, across a line."""
    ratio = image.devicePixelRatio()
    cx, cy = round(at[0] * ratio), round(at[1] * ratio)
    offsets = range(-reach, reach + 1)
    if along == "x":
        values = [image.pixelColor(cx + d, cy).lightness() for d in offsets]
    else:
        values = [image.pixelColor(cx, cy + d).lightness() for d in offsets]
    return offsets[values.index(max(values))]


def _trackpad_pan(canvas, qtbot) -> None:
    wheel(canvas, QPoint(300, 300), pixels=QPoint(6, -4))


def _wheel_zoom(canvas, qtbot) -> None:
    wheel(canvas, QPoint(300, 300), angle=120)


def _middle_drag(canvas, qtbot) -> None:
    qtbot.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(200, 200))
    position = QPointF(230, 190)
    QApplication.sendEvent(
        canvas,
        QMouseEvent(
            QEvent.Type.MouseMove,
            position,
            canvas.mapToGlobal(position),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    qtbot.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(230, 190))


@pytest.mark.parametrize("gesture", [_trackpad_pan, _wheel_zoom, _middle_drag])
def test_a_moving_view_reuses_the_layer_until_it_settles(window, qtbot, gesture) -> None:
    draw_everything(window.session)
    canvas = window.canvas
    canvas.grab()
    layer = canvas._layer
    for _ in range(3):
        gesture(canvas, qtbot)
        canvas.grab()
        assert canvas._layer is layer
    _rebuilt_after_settling(canvas, qtbot, layer)


def test_panning_draws_the_old_layer_exactly_where_a_redraw_would(window, qtbot) -> None:
    draw_everything(window.session)
    canvas = window.canvas
    canvas.grab()
    layer = canvas._layer
    wheel(canvas, QPoint(300, 300), pixels=QPoint(12, -8))  # content moves right and up
    moving = canvas.grab().toImage()
    assert canvas._layer is layer
    _rebuilt_after_settling(canvas, qtbot, layer)
    settled = canvas.grab().toImage()
    ratio = settled.devicePixelRatio()
    width, height = round(canvas.width() * ratio), round(canvas.height() * ratio)
    # The old layer still covers everything but a 12 px strip on the left and 8 px at the bottom.
    differing = [
        (x, y)
        for y in range(0, height - round(8 * ratio), 3)
        for x in range(round(12 * ratio), width, 3)
        if moving.pixel(x, y) != settled.pixel(x, y)
    ]
    assert differing == []


def test_zooming_draws_the_old_layer_scaled_about_the_pointer(window, qtbot) -> None:
    draw_everything(window.session)
    canvas = window.canvas
    window.fit_action.trigger()
    canvas.grab()
    layer = canvas._layer
    center = QPoint(canvas.width() // 2, canvas.height() // 2)
    for _ in range(3):
        wheel(canvas, center, angle=120)
    moving = canvas.grab().toImage()
    assert canvas._layer is layer
    _rebuilt_after_settling(canvas, qtbot, layer)
    settled = canvas.grab().toImage()
    right = canvas.view.to_widget(Point2(x=100, y=25))  # the rectangle's right edge
    top = canvas.view.to_widget(Point2(x=50, y=50))  # and its top edge
    for x, y in (right, top):
        assert 0 <= x < canvas.width()
        assert 0 <= y < canvas.height()
    # Scaling blurs a 1 px line, so compare where each edge peaks, not its exact colour.
    assert abs(_brightest(moving, right, "x") - _brightest(settled, right, "x")) <= 1
    assert abs(_brightest(moving, top, "y") - _brightest(settled, top, "y")) <= 1


def _edit(window) -> None:
    window.session.execute(CreateCircle(center=Point2(x=60, y=40), radius=4))


def _resize(window) -> None:
    window.resize(window.width() - 40, window.height())


def _toggle_grid(window) -> None:
    window.grid_action.trigger()


def _zoom_to_fit(window) -> None:
    window.fit_action.trigger()


@pytest.mark.parametrize("change", [_edit, _resize, _toggle_grid, _zoom_to_fit])
def test_changes_other_than_view_motion_redraw_at_once(window, qtbot, change) -> None:
    draw_everything(window.session)
    canvas = window.canvas
    canvas.grab()
    wheel(canvas, QPoint(300, 300), pixels=QPoint(6, -4))
    canvas.grab()
    layer = canvas._layer
    change(window)
    canvas.grab()
    assert canvas._layer is not layer
