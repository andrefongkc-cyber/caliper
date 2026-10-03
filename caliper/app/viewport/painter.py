"""Drawing in model coordinates.

Tools and the canvas describe what to draw in millimetres; `ModelPainter` converts to widget
pixels, so pixel coordinates never leave `caliper/app/viewport/`. It paints stored entity
inputs (a rectangle's corner, width, and height) and computes no derived geometry.
"""

import math
from dataclasses import replace

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QTransform

from caliper.app.viewport.transform import PlaneView, View, ViewTransform
from caliper.contracts.document import Arc, Circle, Geometry, Line, Point, Point2, Rectangle

GEOMETRY_TYPES = (Point, Line, Circle, Arc, Rectangle)
"""Every drawable geometry kind, for isinstance checks."""

POINT_RADIUS_PX = 2.5
"""A sketch point's dot, the same size on screen at any zoom."""


def cosmetic_pen(color: QColor, width: float, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class ModelPainter:
    """Draws model millimetres through the canvas's view: facing the sketch, a scale and an
    offset; at an angle (ADR 0016), a `PlaneView`, where circles become ellipses, so curves
    are drawn as model-space paths under its affine map. Pens are cosmetic either way."""

    def __init__(self, painter: QPainter, view: View) -> None:
        self.painter = painter
        self.view = view

    def translated(self, dx: float, dy: float) -> "ModelPainter":
        """A painter whose drawing is shifted by (dx, dy) mm, e.g. for a move preview."""
        view = self.view
        if isinstance(view, PlaneView):
            shifted: View = replace(
                view,
                tx=view.tx + view.a * dx + view.b * dy,
                ty=view.ty + view.c * dx + view.d * dy,
            )
        else:
            assert isinstance(view, ViewTransform)
            shifted = replace(
                view,
                origin_x=view.origin_x + dx * view.scale,
                origin_y=view.origin_y - dy * view.scale,
            )
        return ModelPainter(self.painter, shifted)

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
        if isinstance(self.view, PlaneView):
            path = QPainterPath()
            path.addEllipse(QPointF(center.x, center.y), radius, radius)
            self._draw(path)
            return
        r = self.pixels(radius)
        self.painter.drawEllipse(self.point(center), r, r)

    def arc(self, center: Point2, radius: float, start_angle: float, sweep_angle: float) -> None:
        if isinstance(self.view, PlaneView):
            # In model coordinates (Y up), Qt's angles turn the other way round, so they're
            # negated; the affine map then puts the arc where it is.
            box = QRectF(center.x - radius, center.y - radius, 2 * radius, 2 * radius)
            path = QPainterPath()
            path.arcMoveTo(box, -start_angle)
            path.arcTo(box, -start_angle, -sweep_angle)
            self._draw(path)
            return
        # Model angles run counter-clockwise with Y up; Qt's drawArc angles run
        # counter-clockwise as seen on screen, so the same numbers draw the same arc.
        c, r = self.point(center), self.pixels(radius)
        box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        self.painter.drawArc(box, round(start_angle * 16), round(sweep_angle * 16))

    def rectangle(self, corner: Point2, width: float, height: float) -> None:
        if isinstance(self.view, PlaneView):
            self.polygon(_corners(corner, Point2(x=corner.x + width, y=corner.y + height)))
            return
        a = self.point(corner)
        b = self.point(Point2(x=corner.x + width, y=corner.y + height))
        self.painter.drawRect(QRectF(a, b).normalized())

    def filled_box(self, a: Point2, b: Point2, fill: QColor) -> None:
        if isinstance(self.view, PlaneView):
            path = QPainterPath()
            path.addPolygon(QPolygonF([self.point(p) for p in _corners(a, b)]))
            path.closeSubpath()
            self.painter.fillPath(path, fill)
            return
        self.painter.fillRect(QRectF(self.point(a), self.point(b)).normalized(), fill)

    def fill_polygon(self, points: list[Point2], fill: QColor) -> None:
        path = QPainterPath()
        path.addPolygon(QPolygonF([self.point(p) for p in points]))
        path.closeSubpath()
        self.painter.fillPath(path, fill)

    def polygon(self, points: list[Point2]) -> None:
        """A closed outline through `points`, in order."""
        self.painter.drawPolygon(QPolygonF([self.point(p) for p in points]))

    def _draw(self, path: QPainterPath) -> None:
        """A model-space path, mapped to the widget by the plane view's affine map."""
        view = self.view
        assert isinstance(view, PlaneView)
        mapped = QTransform(view.a, view.c, view.b, view.d, view.tx, view.ty).map(path)
        self.painter.drawPath(mapped)

    def marker(self, p: Point2, size_px: float = 4.0) -> None:
        """A small square that stays the same size on screen."""
        c = self.point(p)
        self.painter.drawRect(QRectF(c.x() - size_px, c.y() - size_px, 2 * size_px, 2 * size_px))

    def dot(self, p: Point2, radius_px: float = POINT_RADIUS_PX) -> None:
        """A filled dot in the pen's colour that stays the same size on screen."""
        pen = self.painter.pen()
        self.painter.setBrush(pen.color())
        self.painter.drawEllipse(self.point(p), radius_px, radius_px)
        self.painter.setBrush(Qt.BrushStyle.NoBrush)

    def text(self, p: Point2, text: str, dx_px: float = 0.0, dy_px: float = 0.0) -> None:
        c = self.point(p)
        self.painter.drawText(QPointF(c.x() + dx_px, c.y() + dy_px), text)

    def geometry(self, entity: Geometry) -> None:
        match entity:
            case Point(position=p):
                self.dot(p, POINT_RADIUS_PX + (self.painter.pen().widthF() - 1.5) / 2)
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


def _corners(a: Point2, b: Point2) -> list[Point2]:
    """The box with opposite corners `a` and `b`, as four points in order."""
    return [a, Point2(x=b.x, y=a.y), b, Point2(x=a.x, y=b.y)]
