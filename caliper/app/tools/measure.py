"""Measure tool: click two points to see the distance between them.

Points snap to features (corners, centers, ends, midpoints) through `nearest_feature`, and
the numbers come from `queries.measure_distance`, the same query a check or an agent uses.
The result stays on the canvas until the next click or Esc.
"""

from enum import StrEnum

from PySide6.QtCore import Qt

from caliper.app import theme
from caliper.app.session import DocumentSession
from caliper.app.tools.base import Pointer, SnapKind, Tool
from caliper.app.viewport.annotations import label
from caliper.app.viewport.painter import ModelPainter, cosmetic_pen
from caliper.contracts.document import Point2, Ref
from caliper.contracts.errors import Error
from caliper.contracts.queries import Distance


class MeasurePhase(StrEnum):
    FIRST = "first"
    SECOND = "second"
    SHOWN = "shown"


def describe(distance: Distance) -> str:
    return f"Distance {distance.value:.3f} mm · dx {distance.dx:.3f} · dy {distance.dy:.3f}"


class MeasureTool(Tool):
    name = "Measure"
    shortcut = "M"
    category = "inspect"

    def __init__(self, session: DocumentSession) -> None:
        super().__init__(session)
        self.phase = MeasurePhase.FIRST
        self.a: Ref | None = None
        self.b: Ref | None = None
        self.result: Distance | None = None
        self.current: Point2 | None = None

    @property
    def hint(self) -> str:
        return {
            MeasurePhase.FIRST: "Measure: click a point (corners, centers, ends, midpoints)",
            MeasurePhase.SECOND: "Measure: click the second point (Esc cancels)",
            MeasurePhase.SHOWN: "Measure: click to start a new measurement (Esc clears)",
        }[self.phase]

    @property
    def busy(self) -> bool:
        return self.phase is not MeasurePhase.FIRST

    def move(self, pointer: Pointer) -> None:
        self.current = pointer.point

    def release(self, pointer: Pointer) -> None:
        ref = self._pick(pointer)
        if ref is None:
            return
        if self.phase is MeasurePhase.SECOND and self.a is not None and ref != self.a:
            self.b = ref
            self._measure()
            return
        self.a, self.b, self.result = ref, None, None
        self.phase = MeasurePhase.SECOND

    def cancel(self) -> None:
        self.phase = MeasurePhase.FIRST
        self.a = self.b = None
        self.result = None
        self.current = None

    def _pick(self, pointer: Pointer) -> Ref | None:
        if pointer.snap is SnapKind.FEATURE and pointer.ref is not None:
            return pointer.ref
        found = self.session.queries.nearest_feature(pointer.raw, pointer.tolerance)
        if found is None:
            self.session.message.emit("Click on a point: a corner, center, end, or midpoint")
        return found

    def _measure(self) -> None:
        a, b = self.a, self.b
        if a is None or b is None:
            return
        found = self.session.queries.measure_distance(a, b)
        if isinstance(found, Error):
            self.session.message.emit(found.message)
            self.cancel()
            return
        self.result = found
        self.phase = MeasurePhase.SHOWN
        self.session.message.emit(describe(found))

    def _point(self, ref: Ref) -> Point2 | None:
        found = self.session.queries.feature_point(ref)
        return None if isinstance(found, Error) else found

    def paint(self, painter: ModelPainter) -> None:
        a = self._point(self.a) if self.a is not None else None
        if a is None:
            return
        b = self._point(self.b) if self.b is not None else self.current
        if b is None:
            return
        painter.set_pen(cosmetic_pen(theme.SNAP, theme.GUIDE_WIDTH, Qt.PenStyle.DashLine))
        corner = Point2(x=b.x, y=a.y)
        painter.line(a, corner)
        painter.line(corner, b)
        painter.set_pen(cosmetic_pen(theme.SNAP, theme.GEOMETRY_WIDTH))
        painter.line(a, b)
        painter.marker(a, 3.0)
        painter.marker(b, 3.0)
        if self.result is not None:
            mid = Point2(x=(a.x + b.x) / 2, y=(a.y + b.y) / 2)
            label(painter, mid, f"{self.result.value:.3f} mm", theme.SNAP)
