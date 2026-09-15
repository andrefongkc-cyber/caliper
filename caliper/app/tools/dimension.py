"""Dimension tool.

- Click a point feature, then another, then click to place: `CreateDistanceDimension`.
- Click a circle (diameter) or an arc (radius) away from any point feature:
  `CreateRadialDimension`, labelled in the direction of the click.

Feature picking uses `queries.nearest_feature` and placement uses `queries.feature_point`.
Both go through the engine-gap seam until Stream A lands them.
"""

from enum import StrEnum

from PySide6.QtCore import Qt

from caliper.app import theme
from caliper.app.dimension_layout import choose_orientation, layout, offset_for
from caliper.app.session import DocumentSession
from caliper.app.tools.base import Pointer, Tool
from caliper.app.tools.shapes import clean
from caliper.app.viewport.painter import ModelPainter, angle_between, cosmetic_pen
from caliper.contracts.commands import CreateDistanceDimension, CreateRadialDimension
from caliper.contracts.document import (
    Arc,
    Circle,
    DistanceOrientation,
    Point2,
    RadialMeasure,
    Ref,
)
from caliper.contracts.errors import Error


class DimensionPhase(StrEnum):
    FIRST = "first"
    SECOND = "second"
    PLACE = "place"


class DimensionTool(Tool):
    name = "Dimension"
    shortcut = "D"
    uses_hover = True

    def __init__(self, session: DocumentSession) -> None:
        super().__init__(session)
        self.phase = DimensionPhase.FIRST
        self.a: Ref | None = None
        self.b: Ref | None = None
        self.current: Point2 | None = None

    @property
    def hint(self) -> str:
        return {
            DimensionPhase.FIRST: "Dimension: click a point, or a circle or arc",
            DimensionPhase.SECOND: "Dimension: click the second point (Esc cancels)",
            DimensionPhase.PLACE: "Dimension: click to place (Esc cancels)",
        }[self.phase]

    @property
    def busy(self) -> bool:
        return self.phase is not DimensionPhase.FIRST

    def move(self, pointer: Pointer) -> None:
        self.current = pointer.raw

    def release(self, pointer: Pointer) -> None:
        self.current = pointer.raw
        match self.phase:
            case DimensionPhase.FIRST:
                self._first(pointer)
            case DimensionPhase.SECOND:
                ref = self._pick(pointer)
                if ref is not None and ref != self.a:
                    self.b = ref
                    self.phase = DimensionPhase.PLACE
            case DimensionPhase.PLACE if self.a is not None and self.b is not None:
                placed = self._placement(pointer.raw)
                if placed is not None:
                    orientation, offset = placed
                    self.session.execute(
                        CreateDistanceDimension(
                            a=self.a, b=self.b, orientation=orientation, offset=clean(offset)
                        )
                    )
                self.cancel()

    def cancel(self) -> None:
        self.phase = DimensionPhase.FIRST
        self.a = self.b = None
        self.current = None

    def paint(self, painter: ModelPainter) -> None:
        if self.current is None or self.a is None:
            return
        a = self._feature_point(self.a, quiet=True)
        if a is None:
            return
        painter.set_pen(cosmetic_pen(theme.PREVIEW, theme.GUIDE_WIDTH, Qt.PenStyle.DashLine))
        painter.marker(a, 3.0)
        b = self._feature_point(self.b, quiet=True) if self.b is not None else None
        if b is None or a == b:
            painter.line(a, self.current)
            return
        painter.marker(b, 3.0)
        orientation = choose_orientation(a, b, self.current)
        geo = layout(orientation, a, b, offset_for(orientation, a, b, self.current))
        painter.line(a, geo.start)
        painter.line(b, geo.end)
        painter.set_pen(cosmetic_pen(theme.PREVIEW, theme.GUIDE_WIDTH))
        painter.line(geo.start, geo.end)

    # --- Steps ----------------------------------------------------------------------------

    def _first(self, pointer: Pointer) -> None:
        ref = self._pick(pointer, quiet=True)
        if ref is not None:
            self.a = ref
            self.phase = DimensionPhase.SECOND
            return
        queries = self.session.queries
        hit = queries.entity_at_point(pointer.raw, pointer.tolerance)
        entity = self.session.document.entities.get(hit) if hit is not None else None
        if isinstance(entity, Circle | Arc) and hit is not None:
            measure = RadialMeasure.DIAMETER if isinstance(entity, Circle) else RadialMeasure.RADIUS
            self.session.execute(
                CreateRadialDimension(
                    target=hit,
                    measure=measure,
                    label_angle=clean(angle_between(entity.center, pointer.raw)),
                )
            )
            return
        self._pick(pointer)  # reports that nothing is under the pointer

    def _pick(self, pointer: Pointer, *, quiet: bool = False) -> Ref | None:
        found = self.session.queries.nearest_feature(pointer.raw, pointer.tolerance)
        if found is None and not quiet:
            self.session.message.emit("No point under the pointer")
        return found

    def _feature_point(self, ref: Ref, *, quiet: bool = False) -> Point2 | None:
        found = self.session.queries.feature_point(ref)
        if isinstance(found, Error):
            if not quiet:
                self.session.message.emit(found.message)
            return None
        return found

    def _placement(self, placement: Point2) -> tuple[DistanceOrientation, float] | None:
        """Orientation and offset for a dimension line through `placement`."""
        if self.a is None or self.b is None:
            return None
        a, b = self._feature_point(self.a), self._feature_point(self.b)
        if a is None or b is None:
            return None
        if a == b:
            self.session.message.emit("The two points coincide")
            return None
        orientation = choose_orientation(a, b, placement)
        return orientation, offset_for(orientation, a, b, placement)
