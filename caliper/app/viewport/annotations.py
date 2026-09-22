"""Drawing dimensions.

Where a dimension's references are and what value it shows come from queries
(`feature_point`, `dimension_value`), never from shell math; `dimension_layout` only places
the lines. A dimension whose references can't be resolved is skipped and counted, so the
canvas can say so instead of drawing it wrong. A radial dimension is drawn from its target's
stored center and radius, with the value still read from `dimension_value`.

A driving dimension (`value` set) shows its value plainly. A driven one shows it in
parentheses, the drafting convention for a reference dimension that follows the geometry.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF

from caliper.app import references, theme
from caliper.app.dimension_layout import (
    AngleLayout,
    Segment,
    anchors,
    angle_layout,
    layout,
    midpoint,
)
from caliper.app.session import DocumentSession
from caliper.app.viewport.painter import ModelPainter, cosmetic_pen
from caliper.app.viewport.transform import ViewTransform
from caliper.contracts.document import (
    AngleDimension,
    Arc,
    Circle,
    DistanceDimension,
    DistanceOrientation,
    EntityId,
    Point2,
    RadialDimension,
    RadialMeasure,
)
from caliper.contracts.errors import Error

ARROW_PX = 8.0
LABEL_GAP_PX = 24.0
"""How far past the rim a radial dimension's label sits."""
EXTENSION_OVERSHOOT_PX = 4.0
DIAMETER_SIGN = "⌀"
DEGREE_SIGN = "°"

Dimension = DistanceDimension | RadialDimension | AngleDimension


@dataclass(frozen=True, slots=True)
class Arrow:
    tip: Point2
    back: tuple[float, float]
    """Unit vector from the tip toward the arrow's tail (where its wings open)."""


@dataclass(frozen=True, slots=True)
class DimensionDrawing:
    """Everything needed to draw one dimension, in model coordinates."""

    lines: tuple[tuple[Point2, Point2], ...]
    arrows: tuple[Arrow, ...]
    label_at: Point2
    text: str
    arc: AngleLayout | None = None


def drawing(session: DocumentSession, id: EntityId, view: ViewTransform) -> DimensionDrawing | None:
    """How to draw dimension `id`, or None if it isn't one or its references can't be found."""
    match session.document.entities.get(id):
        case DistanceDimension() as dim:
            return _distance(session, id, dim, view)
        case RadialDimension() as dim:
            return _radial(session, id, dim, view)
        case AngleDimension() as dim:
            return _angle(session, id, dim, view)
    return None


def paint_annotations(
    painter: ModelPainter,
    session: DocumentSession,
    selected: frozenset[EntityId],
    only: Iterable[EntityId] | None = None,
    color: QColor | None = None,
) -> int:
    """Draw dimensions (all of them, or just `only`). Returns how many couldn't be drawn.

    `color` overrides the usual colours, e.g. for dimensions a rejected change named.
    """
    document = session.document
    skipped = 0
    for id in sorted(document.entities) if only is None else sorted(only):
        entity = document.entities.get(id)
        if not isinstance(entity, Dimension):
            continue
        plan = drawing(session, id, painter.view)
        if plan is None:
            skipped += 1
            continue
        if color is not None:
            shown = color
        else:
            shown = theme.SELECTED if id in selected else theme.DIMENSION
        paint(painter, plan, shown)
    return skipped


def paint(painter: ModelPainter, plan: DimensionDrawing, color: QColor) -> None:
    painter.set_pen(cosmetic_pen(color, theme.GUIDE_WIDTH))
    for a, b in plan.lines:
        painter.line(a, b)
    if plan.arc is not None:
        arc = plan.arc
        painter.arc(arc.center, arc.radius, arc.start_angle, arc.sweep)
    for arrow in plan.arrows:
        _arrow(painter, arrow)
    label(painter, plan.label_at, plan.text, color)


def value_text(session: DocumentSession, id: EntityId, prefix: str = "", unit: str = "") -> str:
    entity = session.document.entities.get(id)
    value = session.queries.dimension_value(id)
    text = "?" if isinstance(value, Error) else f"{prefix}{value:.2f}{unit}"
    driven = isinstance(entity, Dimension) and entity.value is None
    return f"({text})" if driven else text


def _distance(
    session: DocumentSession, id: EntityId, dim: DistanceDimension, view: ViewTransform
) -> DimensionDrawing | None:
    queries = session.queries
    ref_a, ref_b = references.anchor(queries, dim.a), references.anchor(queries, dim.b)
    if ref_a is None or ref_b is None:
        return None
    a, b = anchors(ref_a, ref_b)
    orientation = dim.orientation
    if isinstance(ref_a, Segment) or isinstance(ref_b, Segment):
        orientation = DistanceOrientation.ALIGNED  # the contract: curve references are aligned
    geo = layout(orientation, a, b, dim.offset)
    overshoot = view.length_to_model(EXTENSION_OVERSHOOT_PX)
    lines = [
        (point, Point2(x=foot.x + ox * overshoot, y=foot.y + oy * overshoot))
        for point, foot, (ox, oy) in ((a, geo.start, geo.out_a), (b, geo.end, geo.out_b))
    ]
    # A foot beyond the end of a referenced line: extend the line to it, thin like the
    # extension lines, so the dimension visibly attaches to that line.
    for ref, at in ((ref_a, a), (ref_b, b)):
        if isinstance(ref, Segment) and not _within(ref, at):
            lines.append((_nearest_end(ref, at), at))
    lines.append((geo.start, geo.end))
    ux, uy = geo.along
    return DimensionDrawing(
        lines=tuple(lines),
        arrows=(Arrow(geo.start, (ux, uy)), Arrow(geo.end, (-ux, -uy))),
        label_at=midpoint(geo.start, geo.end),
        text=value_text(session, id),
    )


def _radial(
    session: DocumentSession, id: EntityId, dim: RadialDimension, view: ViewTransform
) -> DimensionDrawing | None:
    target = session.document.entities.get(dim.target)
    if not isinstance(target, Circle | Arc):
        return None
    c, r = target.center, target.radius
    ux, uy = math.cos(math.radians(dim.label_angle)), math.sin(math.radians(dim.label_angle))
    rim = Point2(x=c.x + ux * r, y=c.y + uy * r)
    beyond = view.length_to_model(LABEL_GAP_PX)
    label_at = Point2(x=rim.x + ux * beyond, y=rim.y + uy * beyond)
    arrows = [Arrow(rim, (-ux, -uy))]
    if dim.measure is RadialMeasure.DIAMETER:
        far = Point2(x=c.x - ux * r, y=c.y - uy * r)
        lines = ((far, label_at),)
        arrows.append(Arrow(far, (ux, uy)))
        prefix = DIAMETER_SIGN
    else:
        lines = ((c, label_at),)
        prefix = "R"
    return DimensionDrawing(
        lines=lines,
        arrows=tuple(arrows),
        label_at=label_at,
        text=value_text(session, id, prefix=prefix),
    )


def _angle(
    session: DocumentSession, id: EntityId, dim: AngleDimension, view: ViewTransform
) -> DimensionDrawing | None:
    queries = session.queries
    a, b = references.straight(queries, dim.a), references.straight(queries, dim.b)
    if a is None or b is None:
        return None
    text = value_text(session, id, unit=DEGREE_SIGN)
    arc = angle_layout(a, b, dim.offset, dim.supplementary)
    if arc is None:  # parallel: there's no corner to draw an arc in
        return DimensionDrawing(lines=(), arrows=(), label_at=midpoint(a.mid, b.mid), text=text)
    start = math.radians(arc.start_angle)
    end = math.radians(arc.start_angle + arc.sweep)
    first = _on_arc(arc, start)
    second = _on_arc(arc, end)
    lines = [
        (_nearest_end(segment, at), at)
        for segment, at in ((a, first), (b, second), (a, second), (b, first))
        if _on_line(segment, at) and not _within(segment, at)
    ]
    del view
    return DimensionDrawing(
        lines=tuple(lines),
        arrows=(
            Arrow(first, (-math.sin(start), math.cos(start))),
            Arrow(second, (math.sin(end), -math.cos(end))),
        ),
        label_at=arc.label,
        text=text,
        arc=arc,
    )


def _on_arc(arc: AngleLayout, angle: float) -> Point2:
    return Point2(
        x=arc.center.x + arc.radius * math.cos(angle),
        y=arc.center.y + arc.radius * math.sin(angle),
    )


def _on_line(segment: Segment, p: Point2, tolerance: float = 1e-6) -> bool:
    a, b = segment.start, segment.end
    length = math.hypot(b.x - a.x, b.y - a.y)
    if length == 0:
        return False
    cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
    return abs(cross) / length <= tolerance * max(1.0, length)


def _within(segment: Segment, p: Point2) -> bool:
    a, b = segment.start, segment.end
    dx, dy = b.x - a.x, b.y - a.y
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2
    return 0.0 <= t <= 1.0


def _nearest_end(segment: Segment, p: Point2) -> Point2:
    a, b = segment.start, segment.end
    return a if math.hypot(p.x - a.x, p.y - a.y) <= math.hypot(p.x - b.x, p.y - b.y) else b


def _arrow(painter: ModelPainter, arrow: Arrow) -> None:
    """An open arrowhead at `arrow.tip`, its wings opening toward `arrow.back`."""
    size = painter.view.length_to_model(ARROW_PX)
    tip = arrow.tip
    for side in (1.0, -1.0):
        angle = math.atan2(arrow.back[1], arrow.back[0]) + side * math.radians(20)
        painter.line(
            tip, Point2(x=tip.x + size * math.cos(angle), y=tip.y + size * math.sin(angle))
        )


def label_box(metrics: QFontMetricsF, center_x: float, center_y: float, text: str) -> QRectF:
    """The label's box in widget pixels, centred on (center_x, center_y)."""
    width = metrics.horizontalAdvance(text) + 8
    height = metrics.height() + 2
    return QRectF(center_x - width / 2, center_y - height / 2, width, height)


def label(painter: ModelPainter, at: Point2, text: str, color: QColor) -> None:
    qp = painter.painter
    center = painter.point(at)
    box = label_box(QFontMetricsF(qp.font()), center.x(), center.y(), text)
    qp.fillRect(box, theme.CANVAS)
    qp.setPen(cosmetic_pen(color, theme.GUIDE_WIDTH))
    qp.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


def label_anchors(session: DocumentSession, view: ViewTransform) -> list[tuple[Point2, str]]:
    """Where each visible dimension's label sits, and its text, for Zoom to Fit padding."""
    anchors_: list[tuple[Point2, str]] = []
    for id in sorted(session.document.entities):
        plan = drawing(session, id, view)
        if plan is not None:
            anchors_.append((plan.label_at, plan.text))
    return anchors_
