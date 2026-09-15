"""The sketch canvas: a QWidget painted with QPainter (ADR 0004).

Owns the view transform and all pixel handling. Mouse events become model-space `Pointer`s
(with snapping and a pick tolerance in mm) before the active tool sees them.

Navigation, following Fusion/SolidWorks on a Mac:
- Two-finger trackpad scroll pans; pinch or Cmd+scroll zooms about the pointer.
- A mouse wheel zooms about the pointer.
- Middle-drag, or Space+drag, pans.
- Hold Option (Alt) to suspend snapping.
"""

from PySide6.QtCore import QEvent, QLineF, QPointF, Qt, Signal
from PySide6.QtGui import (
    QInputDevice,
    QKeyEvent,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from caliper.app import theme
from caliper.app.engine_gaps import Unavailable, attempt
from caliper.app.session import DocumentSession
from caliper.app.tools.base import Pointer, SnapKind
from caliper.app.tools.controller import ToolController
from caliper.app.viewport.annotations import paint_annotations
from caliper.app.viewport.grid import grid_lines, major_every, minor_spacing, snap_to_grid
from caliper.app.viewport.painter import ModelPainter, cosmetic_pen
from caliper.app.viewport.transform import ViewTransform
from caliper.contracts.document import Arc, Circle, EntityId, Line, Point2, Rectangle
from caliper.contracts.errors import Error
from caliper.contracts.queries import BoundingBox

PICK_RADIUS_PX = 6.0
WHEEL_ZOOM_BASE = 1.0015
"""Zoom factor per unit of wheel angle delta (120 units = one notch ≈ 20%)."""
MAX_GRID_LINES = 600
DEFAULT_VIEW_MM = 250.0
"""How many millimetres a fresh view spans across its shorter side."""

_GEOMETRY = (Line, Circle, Arc, Rectangle)


class Canvas(QWidget):
    cursor_moved = Signal(object)
    """Model position under the pointer (a Point2), or None when the pointer leaves."""

    def __init__(
        self,
        session: DocumentSession,
        controller: ToolController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.controller = controller
        self.view = ViewTransform()
        self.snap_to_grid = True
        self.show_grid = True
        self.hidden_dimensions = 0
        self._placed = False
        self._pan_from: QPointF | None = None
        self._space = False
        self._pointer: Pointer | None = None

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(200, 150)
        self.setCursor(Qt.CursorShape.CrossCursor)
        session.document_changed.connect(self.update)
        session.selection_changed.connect(self.update)
        session.hover_changed.connect(self.update)
        controller.changed.connect(self.update)

    # --- View -----------------------------------------------------------------------------

    def zoom_to_fit(self) -> None:
        box = self.session.queries.bounding_box()
        if isinstance(box, Error):
            self.reset_view()
        else:
            self.view.fit(box, self.width(), self.height())
        self.update()

    def reset_view(self) -> None:
        self.view.scale = min(self.width(), self.height()) / DEFAULT_VIEW_MM
        self.view.origin_x = self.width() / 2
        self.view.origin_y = self.height() / 2
        self.update()

    def visible_box(self) -> BoundingBox:
        top_left = self.view.to_model(0, 0)
        bottom_right = self.view.to_model(self.width(), self.height())
        return BoundingBox(
            x_min=top_left.x, y_min=bottom_right.y, x_max=bottom_right.x, y_max=top_left.y
        )

    # --- Pointer --------------------------------------------------------------------------

    def pointer_at(self, position: QPointF, modifiers: Qt.KeyboardModifier) -> Pointer:
        raw = self.view.to_model(position.x(), position.y())
        tolerance = self.view.length_to_model(PICK_RADIUS_PX)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if modifiers & Qt.KeyboardModifier.AltModifier:
            return Pointer(
                raw=raw, point=raw, snap=SnapKind.NONE, ref=None, tolerance=tolerance, shift=shift
            )
        queries = self.session.queries
        ref = attempt("Point snapping", lambda: queries.nearest_feature(raw, tolerance))
        if ref is not None and not isinstance(ref, Unavailable):
            at = attempt("Feature points", lambda: queries.feature_point(ref))
            if isinstance(at, Point2):
                return Pointer(
                    raw=raw,
                    point=at,
                    snap=SnapKind.FEATURE,
                    ref=ref,
                    tolerance=tolerance,
                    shift=shift,
                )
        if self.snap_to_grid:
            snapped = snap_to_grid(raw, minor_spacing(self.view.scale))
            return Pointer(
                raw=raw,
                point=snapped,
                snap=SnapKind.GRID,
                ref=None,
                tolerance=tolerance,
                shift=shift,
            )
        return Pointer(
            raw=raw, point=raw, snap=SnapKind.NONE, ref=None, tolerance=tolerance, shift=shift
        )

    def _hit(self, pointer: Pointer) -> EntityId | None:
        return self.session.queries.entity_at_point(pointer.raw, pointer.tolerance)

    # --- Qt events ------------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        if not self._placed:
            self._placed = True
            self.reset_view()
        else:
            old = event.oldSize()
            if old.isValid():
                self.view.pan(
                    (event.size().width() - old.width()) / 2,
                    (event.size().height() - old.height()) / 2,
                )
        super().resizeEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.setFocus()
        button = event.button()
        if button == Qt.MouseButton.MiddleButton or (
            button == Qt.MouseButton.LeftButton and self._space
        ):
            self._pan_from = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif button == Qt.MouseButton.LeftButton:
            self._pointer = self.pointer_at(event.position(), event.modifiers())
            self.controller.active.press(self._pointer)
            self.controller.changed.emit()
        elif button == Qt.MouseButton.RightButton:
            self.controller.escape()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        # Qt delivers a fast second click as a double-click instead of a press. Click-click
        # tools need it as a press.
        self.mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.view.pan(delta.x(), delta.y())
            self.update()
            return
        pointer = self.pointer_at(event.position(), event.modifiers())
        self._pointer = pointer
        tool = self.controller.active
        pressed = bool(event.buttons() & Qt.MouseButton.LeftButton)
        self.session.set_hover(self._hit(pointer) if tool.uses_hover and not pressed else None)
        tool.move(pointer)
        self.cursor_moved.emit(pointer.point)
        if tool.busy or pressed:
            self.controller.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._pan_from is not None and event.button() in (
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.LeftButton,
        ):
            self._pan_from = None
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if self._space else Qt.CursorShape.CrossCursor
            )
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._pointer = self.pointer_at(event.position(), event.modifiers())
            self.controller.active.release(self._pointer)
            self.controller.changed.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        pixels = event.pixelDelta()
        trackpad = not pixels.isNull() and (
            event.device().type() == QInputDevice.DeviceType.TouchPad
            or event.phase() != Qt.ScrollPhase.NoScrollPhase
        )
        zoom_modifier = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if trackpad and not zoom_modifier:
            self.view.pan(pixels.x(), pixels.y())
        else:
            steps = event.angleDelta().y() or event.angleDelta().x()
            position = event.position()
            self.view.zoom_about(WHEEL_ZOOM_BASE**steps, position.x(), position.y())
        self.update()
        event.accept()

    def event(self, event: QEvent) -> bool:
        if (
            isinstance(event, QNativeGestureEvent)
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            position = event.position()
            self.view.zoom_about(1.0 + event.value(), position.x(), position.y())
            self.update()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.controller.escape()
        elif event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = False
            if self._pan_from is None:
                self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            super().keyReleaseEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._pointer = None
        self.session.set_hover(None)
        self.cursor_moved.emit(None)
        self.update()
        super().leaveEvent(event)

    # --- Painting -------------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        qp = QPainter(self)
        qp.fillRect(self.rect(), theme.CANVAS)
        if self.show_grid:
            self._paint_grid(qp)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter = ModelPainter(qp, self.view)
        self._paint_geometry(painter)
        self.hidden_dimensions = paint_annotations(painter, self.session, self.session.selection)
        self.controller.active.paint(painter)
        self._paint_snap(painter)
        self._paint_overlay(qp)
        qp.end()

    def _paint_grid(self, qp: QPainter) -> None:
        box = self.visible_box()
        spacing = minor_spacing(self.view.scale)
        every = major_every(spacing)
        xs = grid_lines(box.x_min, box.x_max, spacing)
        ys = grid_lines(box.y_min, box.y_max, spacing)
        if len(xs) + len(ys) > MAX_GRID_LINES:
            return
        minor: list[QLineF] = []
        major: list[QLineF] = []
        w, h = float(self.width()), float(self.height())
        for k in xs:
            if k == 0:
                continue
            x = round(self.view.to_widget(Point2(x=k * spacing, y=0.0))[0]) + 0.5
            (major if k % every == 0 else minor).append(QLineF(x, 0.0, x, h))
        for k in ys:
            if k == 0:
                continue
            y = round(self.view.to_widget(Point2(x=0.0, y=k * spacing))[1]) + 0.5
            (major if k % every == 0 else minor).append(QLineF(0.0, y, w, y))
        qp.setPen(cosmetic_pen(theme.GRID_MINOR, theme.GUIDE_WIDTH))
        qp.drawLines(minor)
        qp.setPen(cosmetic_pen(theme.GRID_MAJOR, theme.GUIDE_WIDTH))
        qp.drawLines(major)
        ox, oy = self.view.to_widget(Point2(x=0.0, y=0.0))
        qp.setPen(cosmetic_pen(theme.AXIS_X, theme.GUIDE_WIDTH))
        qp.drawLine(QLineF(0.0, round(oy) + 0.5, w, round(oy) + 0.5))
        qp.setPen(cosmetic_pen(theme.AXIS_Y, theme.GUIDE_WIDTH))
        qp.drawLine(QLineF(round(ox) + 0.5, 0.0, round(ox) + 0.5, h))

    def _paint_geometry(self, painter: ModelPainter) -> None:
        entities = self.session.document.entities
        selection = self.session.selection
        hover = self.session.hover
        painter.set_pen(cosmetic_pen(theme.GEOMETRY, theme.GEOMETRY_WIDTH))
        for id, entity in entities.items():
            if isinstance(entity, _GEOMETRY) and id not in selection and id != hover:
                painter.geometry(entity)
        if hover is not None and hover not in selection:
            entity = entities.get(hover)
            if isinstance(entity, _GEOMETRY):
                painter.set_pen(cosmetic_pen(theme.HOVER, theme.HIGHLIGHT_WIDTH))
                painter.geometry(entity)
        painter.set_pen(cosmetic_pen(theme.SELECTED, theme.HIGHLIGHT_WIDTH))
        for id in selection:
            entity = entities.get(id)
            if isinstance(entity, _GEOMETRY):
                painter.geometry(entity)

    def _paint_snap(self, painter: ModelPainter) -> None:
        pointer = self._pointer
        if pointer is None or pointer.snap is not SnapKind.FEATURE:
            return
        painter.set_pen(cosmetic_pen(theme.SNAP, theme.GEOMETRY_WIDTH))
        painter.marker(pointer.point, 5.0)

    def _paint_overlay(self, qp: QPainter) -> None:
        if not self.hidden_dimensions:
            return
        noun = "dimension" if self.hidden_dimensions == 1 else "dimensions"
        qp.setPen(theme.TEXT_DIM)
        qp.drawText(
            QPointF(10.0, self.height() - 10.0),
            f"{self.hidden_dimensions} {noun} not drawn: feature points aren't in the engine yet",
        )
