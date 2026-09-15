"""Drawing in model coordinates.

Tools and the canvas describe what to draw in millimetres; `ModelPainter` converts to widget
pixels, so pixel coordinates never leave `caliper/app/viewport/`. It paints stored entity
inputs (a rectangle's corner, width, and height) and computes no derived geometry.
"""

import math
from dataclasses import replace

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from caliper.app.viewport.transform import ViewTransform
from caliper.contracts.document import Arc, Circle, Geometry, Line, Point2, Rectangle


def cosmetic_pen(color: QColor, width: float, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class ModelPainter:
    def __init__(self, painter: QPainter, view: ViewTransform) -> None:
        self.painter = painter
        self.view = view

    def translated(self, dx: float, dy: float) -> "ModelPainter":
        """A painter whose drawing is shifted by (dx, dy) mm, e.g. for a move preview."""
        view = replace(
            self.view,
            origin_x=self.view.origin_x + dx * self.view.scale,
            origin_y=self.view.origin_y - dy * self.view.scale,
        )
        return ModelPainter(self.painter, view)

    def point(self, p: Point2) -> QPointF:
        return QPointF(*self.view.to_widget(p))

    def pixels(self, mm: float) -> float:
        return mm * self.view.scale

    def set_pen(self, pen: QPen) -> None:
        self.painter.setPen(pen)
        self.painter.setBrush(Qt.BrushStyle.NoBrush)

    # --- Primitives -----------------------------------------------------------------------

    def line(self, a: Point2, b: Point2) -> None:
        self.painter.drawLine(QLineF(self.point(a), self.point(b)))

    def circle(self, center: Point2, radius: float) -> None:
        r = self.pixels(radius)
        self.painter.drawEllipse(self.point(center), r, r)

    def arc(self, center: Point2, radius: float, start_angle: float, sweep_angle: float) -> None:
        # Model angles run counter-clockwise with Y up; Qt's drawArc angles run
        # counter-clockwise as seen on screen, so the same numbers draw the same arc.
        c, r = self.point(center), self.pixels(radius)
        box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        self.painter.drawArc(box, round(start_angle * 16), round(sweep_angle * 16))

    def rectangle(self, corner: Point2, width: float, height: float) -> None:
        a = self.point(corner)
        b = self.point(Point2(x=corner.x + width, y=corner.y + height))
        self.painter.drawRect(QRectF(a, b).normalized())

    def filled_box(self, a: Point2, b: Point2, fill: QColor) -> None:
        self.painter.fillRect(QRectF(self.point(a), self.point(b)).normalized(), fill)

    def marker(self, p: Point2, size_px: float = 4.0) -> None:
        """A small square that stays the same size on screen."""
        c = self.point(p)
        self.painter.drawRect(QRectF(c.x() - size_px, c.y() - size_px, 2 * size_px, 2 * size_px))

    def text(self, p: Point2, text: str, dx_px: float = 0.0, dy_px: float = 0.0) -> None:
        c = self.point(p)
        self.painter.drawText(QPointF(c.x() + dx_px, c.y() + dy_px), text)

    def geometry(self, entity: Geometry) -> None:
        match entity:
            case Line(start=a, end=b):
                self.line(a, b)
            case Circle(center=c, radius=r):
                self.circle(c, r)
            case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
                self.arc(c, r, start, sweep)
            case Rectangle(corner=c, width=w, height=h):
                self.rectangle(c, w, h)


def angle_between(center: Point2, p: Point2) -> float:
    """Direction from `center` to `p` in degrees, in [0, 360). Tool input, not a query."""
    return math.degrees(math.atan2(p.y - center.y, p.x - center.x)) % 360.0
