"""Constraint glyphs: a small badge beside the geometry for each constraint.

Where a glyph goes is UI state (the contract gives constraints no placement), so it is
worked out here from what the constraint refers to:

- a point: up and to the right of it;
- a straight curve (a line, a rectangle side): off its midpoint, on its left;
- a circle: off its rim at 45°; an arc: off its midpoint, away from the centre.

A constraint gets one glyph per place it refers to, so a parallel pair shows a badge on
each line; references that coincide (the two points of a coincident constraint) share one.
Badges that land on the same spot stack along the geometry instead of overlapping. Sizes
are in pixels so glyphs stay readable at any zoom; every location comes from queries.
"""

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter

from caliper.app import references, theme
from caliper.app.viewport.painter import cosmetic_pen
from caliper.app.viewport.transform import ViewTransform
from caliper.contracts.document import (
    Arc,
    Circle,
    Constraint,
    ConstraintType,
    Document,
    EntityId,
    Feature,
    Point2,
    Ref,
)
from caliper.contracts.queries import Queries

SYMBOL: dict[ConstraintType, str] = {
    ConstraintType.COINCIDENT: "●",  # the points meet
    ConstraintType.CONCENTRIC: "◎",
    ConstraintType.HORIZONTAL: "H",
    ConstraintType.VERTICAL: "V",
    ConstraintType.PARALLEL: "∥",
    ConstraintType.PERPENDICULAR: "⊥",
    ConstraintType.TANGENT: "T",
    ConstraintType.EQUAL: "=",
    ConstraintType.MIDPOINT: "M",
    ConstraintType.SYMMETRIC: "S",
    ConstraintType.FIX: "F",
    ConstraintType.NORMAL: "N",
    ConstraintType.PIERCE: "P",
    ConstraintType.CURVATURE: "κ",  # kappa
}
"""One short symbol per type. A test checks every one is in the canvas font."""

GAP_PX = 11.0
"""From the geometry to the glyph's centre."""
SIZE_PX = 15.0
"""A glyph's side; stacked glyphs are this far apart."""
SAME_SPOT_PX = 2.0
"""Anchors closer than this share a place."""


@dataclass(frozen=True, slots=True)
class Glyph:
    id: EntityId
    """The constraint."""
    symbol: str
    rect: QRectF
    """In widget pixels, at the view it was laid out for."""


Hang = tuple[Point2, tuple[float, float]]
"""A model point, and the unit direction (model axes) a glyph sits off it in."""


def anchor(queries: Queries, document: Document, ref: Ref) -> Hang | None:
    """Where a reference's glyph hangs from, and the unit direction it sits off in."""
    straight = references.straight(queries, ref)
    if straight is not None:
        a, b = straight.start, straight.end
        length = math.hypot(b.x - a.x, b.y - a.y)
        side = (0.0, 1.0) if length == 0 else (-(b.y - a.y) / length, (b.x - a.x) / length)
        return straight.mid, side
    curve = references.round_curve(document, ref)
    if isinstance(curve, Circle):
        d = (math.sqrt(0.5), math.sqrt(0.5))
        c, r = curve.center, curve.radius
        return Point2(x=c.x + d[0] * r, y=c.y + d[1] * r), d
    if isinstance(curve, Arc):
        mid = references.point(queries, Ref(entity=ref.entity, feature=Feature.MID))
        if mid is None:
            return None
        c = curve.center
        length = math.hypot(mid.x - c.x, mid.y - c.y) or 1.0
        return mid, ((mid.x - c.x) / length, (mid.y - c.y) / length)
    point = references.point(queries, ref)
    if point is None:
        return None
    return point, (math.sqrt(0.5), math.sqrt(0.5))


def layout(queries: Queries, document: Document, view: ViewTransform) -> list[Glyph]:
    """Every constraint's glyphs, in id order, stacked where they'd overlap."""
    glyphs: list[Glyph] = []
    taken: dict[tuple[int, int], int] = {}
    """How many glyphs already hang from each spot (in rounded pixels)."""
    for id in sorted(document.entities):
        constraint = document.entities[id]
        if not isinstance(constraint, Constraint):
            continue
        placed: list[QPointF] = []
        for ref in constraint.refs:
            found = anchor(queries, document, ref)
            if found is None:
                continue
            at, (dx, dy) = found
            wx, wy = view.to_widget(at)
            if any(math.hypot(p.x() - wx, p.y() - wy) < SAME_SPOT_PX for p in placed):
                continue
            placed.append(QPointF(wx, wy))
            key = (round(wx / SAME_SPOT_PX), round(wy / SAME_SPOT_PX))
            n = taken.get(key, 0)
            taken[key] = n + 1
            # Widget Y points down; the model direction's Y flips. Stack along the geometry
            # (perpendicular to the direction the glyph sits off in).
            ox, oy = dx, -dy
            cx = wx + ox * GAP_PX + -oy * SIZE_PX * n
            cy = wy + oy * GAP_PX + ox * SIZE_PX * n
            half = SIZE_PX / 2
            rect = QRectF(cx - half, cy - half, SIZE_PX, SIZE_PX)
            glyphs.append(Glyph(id=id, symbol=SYMBOL[constraint.type], rect=rect))
    return glyphs


def paint(qp: QPainter, glyphs: list[Glyph], colours: dict[EntityId, QColor]) -> None:
    """Draw glyphs; `colours` overrides the default for some constraints."""
    for glyph in glyphs:
        colour = colours.get(glyph.id, theme.GLYPH)
        qp.fillRect(glyph.rect, theme.CANVAS)
        qp.setPen(cosmetic_pen(colour, theme.GUIDE_WIDTH))
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.drawRect(glyph.rect.adjusted(0.5, 0.5, -0.5, -0.5))
        qp.drawText(glyph.rect, Qt.AlignmentFlag.AlignCenter, glyph.symbol)


def at(glyphs: list[Glyph], x: float, y: float) -> EntityId | None:
    """The constraint whose glyph is under widget point (x, y); the last drawn wins."""
    for glyph in reversed(glyphs):
        if glyph.rect.contains(QPointF(x, y)):
            return glyph.id
    return None
