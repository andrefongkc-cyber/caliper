"""Drawing dimensions.

Where a distance dimension's points are and what value it shows come from queries
(`feature_point`, `dimension_value`), never from shell math. A dimension whose points can't
be resolved yet is skipped and counted, so the canvas can say so instead of drawing it
wrong. A radial dimension is drawn from its target's stored center and radius, with the
value still read from `dimension_value`.
"""

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF

from caliper.app import theme
from caliper.app.engine_gaps import Unavailable, attempt
from caliper.app.session import DocumentSession
from caliper.app.viewport.painter import ModelPainter, cosmetic_pen
from caliper.contracts.document import (
    Arc,
    Circle,
    DistanceDimension,
    EntityId,
    Point2,
    RadialDimension,
    RadialMeasure,
    Ref,
)
from caliper.contracts.errors import Error

ARROW_PX = 8.0
EXTENSION_OVERSHOOT_PX = 4.0


def paint_annotations(
    painter: ModelPainter, session: DocumentSession, selected: frozenset[EntityId]
) -> int:
    """Draw every dimension. Returns how many couldn't be drawn yet."""
    document = session.document
    skipped = 0
    for id in sorted(document.entities):
        entity = document.entities[id]
        color = theme.SELECTED if id in selected else theme.DIMENSION
        match entity:
            case DistanceDimension():
                if not _distance(painter, session, id, entity, color):
                    skipped += 1
            case RadialDimension():
                if not _radial(painter, session, id, entity, color):
                    skipped += 1
    return skipped


def _value_text(session: DocumentSession, id: EntityId) -> str:
    value = attempt("Dimension values", lambda: session.queries.dimension_value(id))
    return "?" if isinstance(value, Unavailable | Error) else f"{value:.2f}"


def _point(session: DocumentSession, ref: Ref) -> Point2 | None:
    found = attempt("Feature points", lambda: session.queries.feature_point(ref))
    return None if isinstance(found, Unavailable | Error) else found


def _distance(
    painter: ModelPainter,
    session: DocumentSession,
    id: EntityId,
    dim: DistanceDimension,
    color: QColor,
) -> bool:
    a, b = _point(session, dim.a), _point(session, dim.b)
    if a is None or b is None:
        return False
    ex, ey = b.x - a.x, b.y - a.y
    length = math.hypot(ex, ey)
    if length == 0:
        return False
    ux, uy = ex / length, ey / length
    nx, ny = -uy, ux  # left of a→b, matching the dimension tool's offset sign
    overshoot = math.copysign(painter.view.length_to_model(EXTENSION_OVERSHOOT_PX), dim.offset)
    da = Point2(x=a.x + nx * dim.offset, y=a.y + ny * dim.offset)
    db = Point2(x=b.x + nx * dim.offset, y=b.y + ny * dim.offset)
    painter.set_pen(cosmetic_pen(color, 1.0))
    painter.line(a, Point2(x=da.x + nx * overshoot, y=da.y + ny * overshoot))
    painter.line(b, Point2(x=db.x + nx * overshoot, y=db.y + ny * overshoot))
    painter.line(da, db)
    _arrow(painter, da, ux, uy)
    _arrow(painter, db, -ux, -uy)
    _label(
        painter, Point2(x=(da.x + db.x) / 2, y=(da.y + db.y) / 2), _value_text(session, id), color
    )
    return True


def _radial(
    painter: ModelPainter,
    session: DocumentSession,
    id: EntityId,
    dim: RadialDimension,
    color: QColor,
) -> bool:
    target = session.document.entities.get(dim.target)
    if not isinstance(target, Circle | Arc):
        return False
    c, r = target.center, target.radius
    ux, uy = math.cos(math.radians(dim.label_angle)), math.sin(math.radians(dim.label_angle))
    rim = Point2(x=c.x + ux * r, y=c.y + uy * r)
    beyond = painter.view.length_to_model(24.0)
    label_at = Point2(x=rim.x + ux * beyond, y=rim.y + uy * beyond)
    painter.set_pen(cosmetic_pen(color, 1.0))
    if dim.measure is RadialMeasure.DIAMETER:
        far = Point2(x=c.x - ux * r, y=c.y - uy * r)
        painter.line(far, label_at)
        _arrow(painter, far, ux, uy)
        prefix = "⌀"  # diameter sign
    else:
        painter.line(c, label_at)
        prefix = "R"
    _arrow(painter, rim, -ux, -uy)
    _label(painter, label_at, prefix + _value_text(session, id), color)
    return True


def _arrow(painter: ModelPainter, tip: Point2, ux: float, uy: float) -> None:
    """An open arrowhead at `tip`, pointing opposite to (ux, uy)."""
    size = painter.view.length_to_model(ARROW_PX)
    for side in (1.0, -1.0):
        angle = math.atan2(uy, ux) + side * math.radians(20)
        painter.line(
            tip, Point2(x=tip.x + size * math.cos(angle), y=tip.y + size * math.sin(angle))
        )


def _label(painter: ModelPainter, at: Point2, text: str, color: QColor) -> None:
    qp = painter.painter
    center = painter.point(at)
    metrics = QFontMetricsF(qp.font())
    width, height = metrics.horizontalAdvance(text) + 8, metrics.height() + 2
    box = QRectF(center.x() - width / 2, center.y() - height / 2, width, height)
    qp.fillRect(box, theme.CANVAS)
    qp.setPen(cosmetic_pen(color, 1.0))
    qp.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
