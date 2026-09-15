"""The sketch canvas: a QWidget painted with QPainter (ADR 0004).

Owns the view transform and all pixel handling. Mouse events become model-space `Pointer`s
(with snapping and a pick tolerance in mm) before the active tool sees them.

Navigation, following Fusion/SolidWorks on a Mac:
- Two-finger trackpad scroll pans; pinch or Cmd+scroll zooms about the pointer.
- A mouse wheel zooms about the pointer.
- Middle-drag, or Space+drag, pans.
- Hold Option (Alt) to suspend snapping.
"""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QLineF, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QInputDevice,
    QKeyEvent,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QLabel, QWidget

from caliper.app import theme
from caliper.app.agent.proposal import Proposal
from caliper.app.properties import format_number
from caliper.app.session import DocumentSession
from caliper.app.tools.base import Pointer, SnapKind
from caliper.app.tools.controller import SELECT, ToolController
from caliper.app.tools.select import editable_field
from caliper.app.viewport.annotations import paint_annotations
from caliper.app.viewport.grid import grid_lines, major_every, minor_spacing, snap_to_grid
from caliper.app.viewport.hud import STARTS_ENTRY, NumericEntry
from caliper.app.viewport.inference import acquire, align
from caliper.app.viewport.painter import ModelPainter, cosmetic_pen
from caliper.app.viewport.transform import ViewTransform
from caliper.contracts.commands import Applied, ModifyEntity
from caliper.contracts.document import (
    Arc,
    Circle,
    DistanceDimension,
    EntityId,
    Line,
    Point2,
    RadialDimension,
    Rectangle,
)
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
        self._mouse_px = QPoint()
        self.proposal: Callable[[], Proposal | None] = lambda: None
        """The agent proposal to preview, if any; set by the main window."""
        self.reject_proposal: Callable[[], None] = lambda: None
        self._layer: QPixmap | None = None
        self._layer_key: tuple[float, float, float, int, int, float, bool] | None = None
        self._layer_document: object = None
        self.acquired: list[Point2] = []
        """Feature points the pointer recently passed over, for alignment guides."""
        self._editing: tuple[EntityId, str] | None = None
        """(entity, field) while the entry edits an existing value rather than a new shape."""
        self.empty_hint = QLabel(
            "Draw with R, L, C, or A  ·  type sizes as you go\n"
            "or ask the agent below  ·  ⌘K finds anything",
            self,
        )
        self.empty_hint.setObjectName("empty-hint")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        session.document_changed.connect(self._sync_empty_hint)
        self.entry = NumericEntry(self)
        self.entry.changed.connect(self._typed)
        self.entry.committed.connect(self._commit_typed)
        self.entry.closed.connect(self._entry_closed)

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(200, 150)
        self.setCursor(Qt.CursorShape.CrossCursor)
        session.document_changed.connect(self.update)
        session.document_changed.connect(self._forget_acquired)
        session.selection_changed.connect(self.update)
        session.hover_changed.connect(self.update)
        controller.changed.connect(self.update)
        controller.changed.connect(self._sync_entry)

    # --- View -----------------------------------------------------------------------------

    def zoom_to_fit(self) -> None:
        box = self.session.queries.bounding_box()
        if isinstance(box, Error):
            self.reset_view()
        else:
            self.view.fit(box, self.width(), self.height())
        self.update()

    def frame(self, ids: frozenset[EntityId]) -> None:
        """Fit the given geometry in view; dimensions frame the geometry they measure."""
        entities = self.session.document.entities
        targets: set[EntityId] = set()
        for id in ids:
            match entities.get(id):
                case DistanceDimension(a=a, b=b):
                    targets |= {a.entity, b.entity}
                case RadialDimension(target=target):
                    targets.add(target)
                case None:
                    pass
                case _:
                    targets.add(id)
        box = self.session.queries.bounding_box(sorted(targets))
        if not isinstance(box, Error):
            margin = max(box.width, box.height, 1.0) * 0.25
            self.view.fit(
                BoundingBox(
                    x_min=box.x_min - margin,
                    y_min=box.y_min - margin,
                    x_max=box.x_max + margin,
                    y_max=box.y_max + margin,
                ),
                self.width(),
                self.height(),
            )
            self.update()

    def frame_box(self, box: BoundingBox, right_inset: float = 0.0) -> None:
        """Fit `box` into the canvas, leaving `right_inset` pixels free on the right."""
        margin = max(box.width, box.height, 1.0) * 0.15
        padded = BoundingBox(
            x_min=box.x_min - margin,
            y_min=box.y_min - margin,
            x_max=box.x_max + margin,
            y_max=box.y_max + margin,
        )
        width = max(self.width() - right_inset, 100.0)
        self.view.fit(padded, width, self.height())
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
        ref = queries.nearest_feature(raw, tolerance)
        if ref is not None:
            at = queries.feature_point(ref)
            if isinstance(at, Point2):
                return Pointer(
                    raw=raw,
                    point=at,
                    snap=SnapKind.FEATURE,
                    ref=ref,
                    tolerance=tolerance,
                    shift=shift,
                )
        base = snap_to_grid(raw, minor_spacing(self.view.scale)) if self.snap_to_grid else raw
        point, guides = align(raw, base, self.acquired, tolerance)
        fallback = SnapKind.GRID if self.snap_to_grid else SnapKind.NONE
        kind = SnapKind.GUIDE if guides else fallback
        return Pointer(
            raw=raw,
            point=point,
            snap=kind,
            ref=None,
            tolerance=tolerance,
            shift=shift,
            guides=guides,
        )

    # --- Typed values ---------------------------------------------------------------------

    def _typed(self, values: tuple[float | None, ...]) -> None:
        if self._editing is not None:
            return
        self.controller.active.type_values(values)
        self.update()

    def _commit_typed(self, values: tuple[float | None, ...]) -> None:
        if self._editing is not None:
            entity_id, field = self._editing
            (value,) = values
            if value is None:
                self.entry.close_entry()
                return
            result = self.session.execute(ModifyEntity(id=entity_id, changes={field: value}))
            if isinstance(result, Applied):
                self.entry.close_entry()
            else:  # the session already put the engine's message in the status bar
                self.entry.mark_invalid(0)
            return
        if self.controller.active.commit_values(values):
            self.entry.close_entry()
            self.controller.changed.emit()
        else:
            self.session.message.emit("Those values don't make a shape: sizes must be above 0")

    def _entry_closed(self) -> None:
        if self._editing is not None:
            self._editing = None
            self.setFocus()
            return
        tool = self.controller.active
        tool.type_values([None] * len(tool.numeric_fields))
        self.setFocus()
        self.update()

    def _sync_entry(self) -> None:
        if self.entry.isVisible() and self._editing is None and not self.controller.active.busy:
            self.entry.close_entry()

    def _sync_empty_hint_geometry(self, width: int, height: int) -> None:
        hint_height = self.empty_hint.sizeHint().height()
        self.empty_hint.setGeometry(0, (height - hint_height) // 2, width, hint_height)

    def _sync_empty_hint(self) -> None:
        self.empty_hint.setVisible(not self.session.document.entities)
        self._sync_empty_hint_geometry(self.width(), self.height())

    def _forget_acquired(self) -> None:
        self.acquired = []

    def _hit(self, pointer: Pointer) -> EntityId | None:
        return self.session.queries.entity_at_point(pointer.raw, pointer.tolerance)

    # --- Qt events ------------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self._sync_empty_hint_geometry(event.size().width(), event.size().height())
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
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.controller.active.name == SELECT
            and not self._space
        ):
            pointer = self.pointer_at(event.position(), event.modifiers())
            if self.edit_at(pointer, event.position().toPoint()):
                return
        # Qt delivers a fast second click as a double-click instead of a press. Click-click
        # tools need it as a press.
        self.mousePressEvent(event)

    def edit_at(self, pointer: Pointer, at: QPoint) -> bool:
        """Open the entry on the dimension under the pointer. False if there's none."""
        hit = self._hit(pointer)
        entity = self.session.document.entities.get(hit) if hit is not None else None
        field = editable_field(entity, pointer.raw)
        if hit is None or field is None:
            return False
        self.controller.cancel_operation()
        self.session.set_selection(frozenset({hit}))
        self._editing = (hit, field)
        self.entry.open((field.capitalize(),), format_number(getattr(entity, field)), at)
        self.entry.fields[0].selectAll()
        return True

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.view.pan(delta.x(), delta.y())
            self.update()
            return
        self._mouse_px = event.position().toPoint()
        pointer = self.pointer_at(event.position(), event.modifiers())
        if pointer.snap is SnapKind.FEATURE:
            self.acquired = acquire(self.acquired, pointer.point)
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
        tool = self.controller.active
        text = event.text()
        if (
            tool.busy
            and tool.numeric_fields
            and text
            and text in STARTS_ENTRY
            and not self.entry.isVisible()
        ):
            self.entry.open(tool.numeric_fields, text, self._mouse_px)
            return
        if event.key() == Qt.Key.Key_Escape:
            if self.proposal() is not None:
                self.reject_proposal()
            else:
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
        qp.drawPixmap(0, 0, self._static_layer())
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter = ModelPainter(qp, self.view)
        self._paint_highlights(painter)
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

    def _static_layer(self) -> QPixmap:
        """Grid, geometry, and dimensions, redrawn only when the document or the view changes.

        Hover, selection, tool previews, and snap markers are painted over it every frame, so
        moving the pointer never repaints the whole sketch.
        """
        ratio = self.devicePixelRatioF()
        view = self.view
        key = (
            view.scale,
            view.origin_x,
            view.origin_y,
            self.width(),
            self.height(),
            ratio,
            self.show_grid,
        )
        document = self.session.document
        if self._layer is not None and self._layer_key == key and self._layer_document is document:
            return self._layer
        layer = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        layer.setDevicePixelRatio(ratio)
        qp = QPainter(layer)
        qp.fillRect(QRectF(0, 0, self.width(), self.height()), theme.CANVAS)
        if self.show_grid:
            self._paint_grid(qp)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter = ModelPainter(qp, view)
        painter.set_pen(cosmetic_pen(theme.GEOMETRY, theme.GEOMETRY_WIDTH))
        for entity in document.entities.values():
            if isinstance(entity, _GEOMETRY):
                painter.geometry(entity)
        self.hidden_dimensions = paint_annotations(painter, self.session, frozenset())
        qp.end()
        self._layer, self._layer_key, self._layer_document = layer, key, document
        return layer

    def _paint_highlights(self, painter: ModelPainter) -> None:
        entities = self.session.document.entities
        selection = self.session.selection
        hover = self.session.hover
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
        paint_annotations(painter, self.session, selection, only=selection)
        self._paint_proposal(painter)

    def _paint_proposal(self, painter: ModelPainter) -> None:
        """Ghost geometry: what accepting would add (agent colour), change, or remove (dashed)."""
        proposal = self.proposal()
        if proposal is None:
            return
        before, after = proposal.base.entities, proposal.result.entities
        dashed = Qt.PenStyle.DashLine
        for id, entity in before.items():
            if isinstance(entity, _GEOMETRY) and after.get(id) != entity:
                colour = theme.TEXT_DIM if id in after else theme.ERROR
                painter.set_pen(cosmetic_pen(colour, theme.GUIDE_WIDTH, dashed))
                painter.geometry(entity)
        painter.set_pen(cosmetic_pen(theme.AGENT, theme.HIGHLIGHT_WIDTH))
        for id, entity in after.items():
            if isinstance(entity, _GEOMETRY) and before.get(id) != entity:
                painter.geometry(entity)

    def _paint_snap(self, painter: ModelPainter) -> None:
        pointer = self._pointer
        if pointer is None:
            return
        if pointer.snap is SnapKind.FEATURE:
            painter.set_pen(cosmetic_pen(theme.SNAP, theme.GEOMETRY_WIDTH))
            painter.marker(pointer.point, 5.0)
        elif pointer.snap is SnapKind.GUIDE:
            painter.set_pen(cosmetic_pen(theme.SNAP, theme.GUIDE_WIDTH, Qt.PenStyle.DashLine))
            for source, target in pointer.guides:
                painter.line(source, target)
                painter.marker(source, 2.5)

    def _paint_overlay(self, qp: QPainter) -> None:
        if not self.hidden_dimensions:
            return
        noun = "dimension" if self.hidden_dimensions == 1 else "dimensions"
        qp.setPen(theme.TEXT_DIM)
        qp.drawText(
            QPointF(10.0, self.height() - 10.0),
            f"{self.hidden_dimensions} {noun} not drawn: the points they measure can't be found",
        )
