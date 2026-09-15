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
                offset = self._offset(pointer.raw)
                if offset is not None:
                    self.session.execute(
                        CreateDistanceDimension(
                            a=self.a,
                            b=self.b,
                            orientation=DistanceOrientation.ALIGNED,
                            offset=clean(offset),
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
        end = self._feature_point(self.b, quiet=True) if self.b is not None else None
        painter.line(a, end if end is not None else self.current)

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

    def _offset(self, placement: Point2) -> float | None:
        """Signed distance from the a→b line to the placement point; positive is to the left.

        The contract says only "signed perpendicular offset"; the sign convention is a gap
        recorded in docs/workplan/shell.md.
        """
        if self.a is None or self.b is None:
            return None
        a, b = self._feature_point(self.a), self._feature_point(self.b)
        if a is None or b is None:
            return None
        ex, ey = b.x - a.x, b.y - a.y
        length = (ex * ex + ey * ey) ** 0.5
        if length == 0:
            self.session.message.emit("The two points coincide")
            return None
        return (ex * (placement.y - a.y) - ey * (placement.x - a.x)) / length
