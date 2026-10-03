"""Select, box-select, and drag-to-move.

- Click an entity to select it; Shift-click toggles. Click empty space to clear.
- Drag a selected entity to move the selection: a preview while dragging, then one
  `MoveEntities` on release.
- Drag on empty space to box-select. Left-to-right selects entities fully inside (window);
  right-to-left also selects entities the box touches (crossing), as in SolidWorks/AutoCAD.
"""

import math
from enum import StrEnum

from PySide6.QtCore import Qt

from caliper.app import theme
from caliper.app.session import DocumentSession
from caliper.app.tools.base import Pointer, Tool
from caliper.app.tools.shapes import clean
from caliper.app.viewport.painter import GEOMETRY_TYPES, ModelPainter, cosmetic_pen
from caliper.contracts.commands import MoveEntities
from caliper.contracts.document import (
    AngleDimension,
    Arc,
    Circle,
    DistanceDimension,
    EntityId,
    Point2,
    RadialDimension,
    Rectangle,
)
from caliper.contracts.queries import BoundingBox

DRAG_PX = 6.0
"""On a plane seen at an angle, a press that moves less than this on screen is a click."""


class SelectPhase(StrEnum):
    IDLE = "idle"
    PRESSED_ENTITY = "pressed_entity"
    PRESSED_EMPTY = "pressed_empty"
    MOVING = "moving"
    BOXING = "boxing"


class SelectTool(Tool):
    name = "Select"
    category = "select"
    shortcut = "S"
    uses_hover = True

    def __init__(self, session: DocumentSession) -> None:
        super().__init__(session)
        self.phase = SelectPhase.IDLE
        self.start: Pointer | None = None
        self.current: Pointer | None = None
        self.hit: EntityId | None = None

    @property
    def hint(self) -> str:
        return {
            SelectPhase.MOVING: "Release to move (Esc cancels)",
            SelectPhase.BOXING: "Left to right selects inside; right to left selects touching",
        }.get(
            self.phase,
            "Click to select, Shift-click to add, drag to move · ⌘-drag boxes from inside a shape",
        )

    @property
    def busy(self) -> bool:
        return self.phase in (SelectPhase.MOVING, SelectPhase.BOXING)

    def press(self, pointer: Pointer) -> None:
        # Pressing inside a closed shape picks it (as Fusion and SolidWorks do), so ⌘ is
        # how you rubber-band from inside one.
        if pointer.force_box:
            hit = None
        elif pointer.annotation is not None:
            hit = pointer.annotation
        else:
            hit = self.session.sketch_queries.entity_at_point(pointer.raw, pointer.tolerance)
        self.start = self.current = pointer
        self.hit = hit
        if hit is None:
            self.phase = SelectPhase.PRESSED_EMPTY
            return
        selection = self.session.selection
        if pointer.shift:
            self.session.set_selection(selection ^ {hit})
            self.phase = SelectPhase.IDLE  # a toggle click never starts a move
        else:
            if hit not in selection:
                self.session.set_selection(frozenset({hit}))
            self.phase = SelectPhase.PRESSED_ENTITY

    def move(self, pointer: Pointer) -> None:
        if self.start is None:
            return
        self.current = pointer
        if pointer.view is not None:  # at an angle: how far on screen
            moved = math.hypot(pointer.px[0] - self.start.px[0], pointer.px[1] - self.start.px[1])
            if moved <= DRAG_PX:
                return
        else:
            dragged = math.hypot(pointer.raw.x - self.start.raw.x, pointer.raw.y - self.start.raw.y)
            if dragged <= pointer.tolerance:
                return
        if self.phase is SelectPhase.PRESSED_ENTITY:
            self.phase = SelectPhase.MOVING
        elif self.phase is SelectPhase.PRESSED_EMPTY:
            self.phase = SelectPhase.BOXING

    def release(self, pointer: Pointer) -> None:
        start, self.current = self.start, pointer
        match self.phase:
            case SelectPhase.MOVING if start is not None:
                dx, dy = self._offset(start, pointer)
                if dx or dy:
                    ids = tuple(sorted(self.session.selection))
                    self.session.execute(MoveEntities(ids=ids, dx=dx, dy=dy))
            case SelectPhase.BOXING if start is not None:
                self._select_box(start, pointer)
            case SelectPhase.PRESSED_ENTITY if self.hit is not None:
                # A plain click inside a multi-selection narrows it to the clicked entity.
                self.session.set_selection(frozenset({self.hit}))
            case SelectPhase.PRESSED_EMPTY if not pointer.shift:
                self.session.set_selection(frozenset())
        self.cancel()

    def cancel(self) -> None:
        self.phase = SelectPhase.IDLE
        self.start = self.current = None
        self.hit = None

    def paint(self, painter: ModelPainter) -> None:
        if self.start is None or self.current is None:
            return
        if self.phase is SelectPhase.MOVING:
            dx, dy = self._offset(self.start, self.current)
            moved = painter.translated(dx, dy)
            moved.set_pen(cosmetic_pen(theme.PREVIEW, theme.GEOMETRY_WIDTH, Qt.PenStyle.DashLine))
            entities = self.session.sketch_view.entities
            for id in self.session.selection:
                entity = entities.get(id)
                if isinstance(entity, GEOMETRY_TYPES):
                    moved.geometry(entity)
        elif self.phase is SelectPhase.BOXING:
            corners, crossing = self._box(self.start, self.current)
            if corners is None:
                a, b = self.start.raw, self.current.raw
                painter.filled_box(a, b, theme.RUBBER_BAND)
            else:
                painter.fill_polygon(corners, theme.RUBBER_BAND)
            style = Qt.PenStyle.DashLine if crossing else Qt.PenStyle.SolidLine
            painter.set_pen(cosmetic_pen(theme.ACCENT, theme.GUIDE_WIDTH, style))
            if corners is None:
                a, b = self.start.raw, self.current.raw
                painter.rectangle(a, b.x - a.x, b.y - a.y)
            else:
                painter.polygon(corners)

    @staticmethod
    def _offset(start: Pointer, end: Pointer) -> tuple[float, float]:
        return clean(end.point.x - start.point.x), clean(end.point.y - start.point.y)

    @staticmethod
    def _box(start: Pointer, end: Pointer) -> tuple[list[Point2] | None, bool]:
        """At an angle, the box on screen as a polygon on the plane, and whether it crosses
        (dragged right to left on screen); facing, None and the model's own rule."""
        view = end.view
        if view is None:
            return None, end.raw.x < start.raw.x
        (x0, y0), (x1, y1) = start.px, end.px
        corners = [view.to_model(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        return corners, x1 < x0

    def _select_box(self, start: Pointer, end: Pointer) -> None:
        corners, crossing = self._box(start, end)
        queries = self.session.sketch_queries
        if corners is not None:
            found = queries.entities_in_polygon(corners, crossing=crossing)
        else:
            a, b = start.raw, end.raw
            box = BoundingBox(
                x_min=min(a.x, b.x), y_min=min(a.y, b.y), x_max=max(a.x, b.x), y_max=max(a.y, b.y)
            )
            found = queries.entities_in_box(box, crossing=crossing)
        ids: frozenset[EntityId] = frozenset(found)
        self.session.set_selection(self.session.selection | ids if end.shift else ids)


def editable_field(entity: object, point: Point2, tolerance: float) -> str | None:
    """The value a double-click edits, or None.

    A dimension (its label was hit) edits its `value`: typing a number makes it driving.

    A rectangle's top or bottom edge edits width; a side edits height. Circles and arcs edit
    radius. A line's length isn't a stored input, so there's nothing to edit in place yet.
    The point must be within `tolerance` of the outline: since a click inside a shape selects
    it, without this a double-click in the middle would open whichever edge happened to be
    nearer.
    """
    match entity:
        case DistanceDimension() | RadialDimension() | AngleDimension():
            return "value"
        case Rectangle(corner=c, width=w, height=h):
            inside_x = c.x - tolerance <= point.x <= c.x + w + tolerance
            inside_y = c.y - tolerance <= point.y <= c.y + h + tolerance
            to_side = min(abs(point.x - c.x), abs(point.x - (c.x + w)))
            to_top_or_bottom = min(abs(point.y - c.y), abs(point.y - (c.y + h)))
            if to_top_or_bottom <= tolerance and inside_x:
                return "width"
            if to_side <= tolerance and inside_y:
                return "height"
            return None
        case Circle(center=c, radius=r) | Arc(center=c, radius=r):
            return (
                "radius" if abs(math.hypot(point.x - c.x, point.y - c.y) - r) <= tolerance else None
            )
    return None
