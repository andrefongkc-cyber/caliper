"""Sketch tools: line, circle, rectangle, arc.

Each accepts either a drag (press, move, release) or clicks (click, move, click), and sends
exactly one create command when the shape is complete. A shape with zero size sends
nothing and keeps waiting for the next point.
"""

import math
from collections.abc import Sequence
from enum import StrEnum

from PySide6.QtCore import Qt

from caliper.app import theme
from caliper.app.session import DocumentSession
from caliper.app.tools.base import Pointer, Tool
from caliper.app.viewport.painter import ModelPainter, angle_between, cosmetic_pen
from caliper.contracts.commands import Command, CreateArc, CreateCircle, CreateLine, CreateRectangle
from caliper.contracts.document import Point2


def clean(value: float) -> float:
    """Drop float noise from subtracting snapped inputs (0.3 - 0.1 = 0.19999999999999998).

    Twelve significant digits is far below any manufacturing tolerance and keeps files tidy.
    """
    return float(f"{value:.12g}") + 0.0


def _cos(degrees: float, radians: float) -> float:
    """Exact at multiples of 90 degrees, so a typed 90 gives a truly vertical line."""
    return (1.0, 0.0, -1.0, 0.0)[int(degrees // 90) % 4] if degrees % 90 == 0 else math.cos(radians)


def _sin(degrees: float, radians: float) -> float:
    return (0.0, 1.0, 0.0, -1.0)[int(degrees // 90) % 4] if degrees % 90 == 0 else math.sin(radians)


class TwoPoint(StrEnum):
    FIRST = "first"
    SECOND = "second"


class TwoPointTool(Tool):
    """Shapes defined by an anchor point and a second point."""

    first_hint = ""
    second_hint = ""

    def __init__(self, session: DocumentSession) -> None:
        super().__init__(session)
        self.phase = TwoPoint.FIRST
        self.anchor: Point2 | None = None
        self.current: Point2 | None = None
        self.pointer: Point2 | None = None
        """Where the pointer is, kept separately so typed values can follow its direction."""
        self.typed: tuple[float | None, ...] | None = None

    @property
    def hint(self) -> str:
        if self.phase is TwoPoint.SECOND and self.numeric_fields and self.typed is None:
            return f"{self.second_hint}; or type {' and '.join(self.numeric_fields).lower()}"
        return self.first_hint if self.phase is TwoPoint.FIRST else self.second_hint

    @property
    def busy(self) -> bool:
        return self.phase is TwoPoint.SECOND

    def press(self, pointer: Pointer) -> None:
        if self.phase is TwoPoint.FIRST:
            self.anchor = pointer.point
            self.phase = TwoPoint.SECOND
        self.current = self.pointer = pointer.point

    def move(self, pointer: Pointer) -> None:
        self.pointer = pointer.point
        if self.typed is not None:
            self.type_values(self.typed)
        else:
            self.current = pointer.point

    def release(self, pointer: Pointer) -> None:
        if self.phase is not TwoPoint.SECOND or self.anchor is None:
            return
        if self.typed is not None:  # a click while values are typed confirms them
            self.pointer = pointer.point
            self.commit_values(self.typed)
            return
        self.current = pointer.point
        command = self.command(self.anchor, self.current)
        if command is not None:
            self.session.execute(command)
            self.cancel()

    def cancel(self) -> None:
        self.phase = TwoPoint.FIRST
        self.anchor = None
        self.current = self.pointer = None
        self.typed = None

    def type_values(self, values: Sequence[float | None]) -> None:
        if self.anchor is None:
            return
        typed = tuple(values)
        if all(v is None for v in typed):
            self.typed = None
            self.current = self.pointer or self.anchor
            return
        self.typed = typed
        point = self.point_from_values(self.anchor, self.typed, self.pointer or self.anchor)
        if point is not None:
            self.current = point

    def commit_values(self, values: Sequence[float | None]) -> bool:
        if self.phase is not TwoPoint.SECOND or self.anchor is None:
            return False
        point = self.point_from_values(self.anchor, tuple(values), self.pointer or self.anchor)
        command = None if point is None else self.command(self.anchor, point)
        if command is None:
            return False
        self.session.execute(command)
        self.cancel()
        return True

    def point_from_values(
        self, anchor: Point2, values: tuple[float | None, ...], pointer: Point2
    ) -> Point2 | None:
        """The second point implied by typed values; untyped ones come from the pointer."""
        return None

    def paint(self, painter: ModelPainter) -> None:
        if self.anchor is None or self.current is None:
            return
        painter.set_pen(cosmetic_pen(theme.PREVIEW, theme.GEOMETRY_WIDTH))
        self.preview(painter, self.anchor, self.current)

    def command(self, anchor: Point2, current: Point2) -> Command | None:
        raise NotImplementedError

    def preview(self, painter: ModelPainter, anchor: Point2, current: Point2) -> None:
        raise NotImplementedError


class LineTool(TwoPointTool):
    name = "Line"
    shortcut = "L"
    first_hint = "Line: click or drag from the start point"
    second_hint = "Line: click the end point (Esc cancels)"
    numeric_fields = ("Length", "Angle")

    def point_from_values(
        self, anchor: Point2, values: tuple[float | None, ...], pointer: Point2
    ) -> Point2 | None:
        length, angle = values
        dx, dy = pointer.x - anchor.x, pointer.y - anchor.y
        if length is None:
            length = math.hypot(dx, dy)
        if angle is None:
            angle = math.degrees(math.atan2(dy, dx)) if (dx or dy) else 0.0
        if length <= 0:
            return None
        radians = math.radians(angle)
        return Point2(
            x=clean(anchor.x + length * _cos(angle, radians)),
            y=clean(anchor.y + length * _sin(angle, radians)),
        )

    def command(self, anchor: Point2, current: Point2) -> Command | None:
        if anchor == current:
            return None
        return CreateLine(start=anchor, end=current)

    def preview(self, painter: ModelPainter, anchor: Point2, current: Point2) -> None:
        painter.line(anchor, current)


class CircleTool(TwoPointTool):
    name = "Circle"
    shortcut = "C"
    first_hint = "Circle: click or drag from the center"
    second_hint = "Circle: click a point on the circle (Esc cancels)"
    numeric_fields = ("Radius",)

    def point_from_values(
        self, anchor: Point2, values: tuple[float | None, ...], pointer: Point2
    ) -> Point2 | None:
        (radius,) = values
        if radius is None:
            return pointer
        if radius <= 0:
            return None
        return Point2(x=clean(anchor.x + radius), y=anchor.y)

    def command(self, anchor: Point2, current: Point2) -> Command | None:
        radius = clean(math.hypot(current.x - anchor.x, current.y - anchor.y))
        return CreateCircle(center=anchor, radius=radius) if radius > 0 else None

    def preview(self, painter: ModelPainter, anchor: Point2, current: Point2) -> None:
        painter.circle(anchor, math.hypot(current.x - anchor.x, current.y - anchor.y))
        painter.marker(anchor, 2.0)


class RectangleTool(TwoPointTool):
    name = "Rectangle"
    shortcut = "R"
    first_hint = "Rectangle: click or drag from a corner"
    second_hint = "Rectangle: click the opposite corner (Esc cancels)"
    numeric_fields = ("Width", "Height")

    def point_from_values(
        self, anchor: Point2, values: tuple[float | None, ...], pointer: Point2
    ) -> Point2 | None:
        """Typed sizes extend toward the pointer, so the rectangle grows where you're pointing."""
        width, height = values
        dx, dy = pointer.x - anchor.x, pointer.y - anchor.y
        if (width is not None and width <= 0) or (height is not None and height <= 0):
            return None
        x = anchor.x + math.copysign(width, dx or 1.0) if width is not None else pointer.x
        y = anchor.y + math.copysign(height, dy or 1.0) if height is not None else pointer.y
        return Point2(x=clean(x), y=clean(y))

    def command(self, anchor: Point2, current: Point2) -> Command | None:
        width = clean(abs(current.x - anchor.x))
        height = clean(abs(current.y - anchor.y))
        if width <= 0 or height <= 0:
            return None
        corner = Point2(x=min(anchor.x, current.x), y=min(anchor.y, current.y))
        return CreateRectangle(corner=corner, width=width, height=height)

    def preview(self, painter: ModelPainter, anchor: Point2, current: Point2) -> None:
        painter.rectangle(anchor, current.x - anchor.x, current.y - anchor.y)
        painter.text(
            current,
            f"{clean(abs(current.x - anchor.x)):g} x {clean(abs(current.y - anchor.y)):g}",
            dx_px=10,
            dy_px=-10,
        )


class ArcPhase(StrEnum):
    CENTER = "center"
    START = "start"
    END = "end"


class ArcTool(Tool):
    """Center, then start point, then end point. The arc runs counter-clockwise."""

    name = "Arc"
    shortcut = "A"

    def __init__(self, session: DocumentSession) -> None:
        super().__init__(session)
        self.phase = ArcPhase.CENTER
        self.center: Point2 | None = None
        self.start: Point2 | None = None
        self.current: Point2 | None = None

    @property
    def hint(self) -> str:
        return {
            ArcPhase.CENTER: "Arc: click the center",
            ArcPhase.START: "Arc: click the start point (Esc cancels)",
            ArcPhase.END: "Arc: click the end point, counter-clockwise (Esc cancels)",
        }[self.phase]

    @property
    def busy(self) -> bool:
        return self.phase is not ArcPhase.CENTER

    def move(self, pointer: Pointer) -> None:
        self.current = pointer.point

    def release(self, pointer: Pointer) -> None:
        p = pointer.point
        self.current = p
        match self.phase:
            case ArcPhase.CENTER:
                self.center = p
                self.phase = ArcPhase.START
            case ArcPhase.START if self.center is not None and p != self.center:
                self.start = p
                self.phase = ArcPhase.END
            case ArcPhase.END if self.center is not None and self.start is not None:
                start_angle = angle_between(self.center, self.start)
                sweep = clean((angle_between(self.center, p) - start_angle) % 360.0)
                if p != self.center and 0 < sweep < 360:
                    radius = math.hypot(self.start.x - self.center.x, self.start.y - self.center.y)
                    self.session.execute(
                        CreateArc(
                            center=self.center,
                            radius=clean(radius),
                            start_angle=clean(start_angle) % 360.0,
                            sweep_angle=sweep,
                        )
                    )
                    self.cancel()

    def cancel(self) -> None:
        self.phase = ArcPhase.CENTER
        self.center = self.start = self.current = None

    def paint(self, painter: ModelPainter) -> None:
        if self.center is None or self.current is None:
            return
        painter.set_pen(cosmetic_pen(theme.PREVIEW, theme.GUIDE_WIDTH, Qt.PenStyle.DashLine))
        painter.marker(self.center, 2.0)
        if self.start is None:
            painter.line(self.center, self.current)
            return
        radius = math.hypot(self.start.x - self.center.x, self.start.y - self.center.y)
        painter.line(self.center, self.start)
        painter.set_pen(cosmetic_pen(theme.PREVIEW, theme.GEOMETRY_WIDTH))
        start_angle = angle_between(self.center, self.start)
        sweep = (angle_between(self.center, self.current) - start_angle) % 360.0
        painter.arc(self.center, radius, start_angle, sweep)
