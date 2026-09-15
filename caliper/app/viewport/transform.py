"""Model ↔ widget coordinates. The only place the Y axis flips.

The model is Y-up millimetres; Qt widgets are Y-down pixels (logical pixels: Qt applies the
device pixel ratio underneath, which keeps Retina drawing sharp). Plain floats, no Qt, so
the math is testable on its own.
"""

from dataclasses import dataclass

from caliper.contracts.document import Point2
from caliper.contracts.queries import BoundingBox

MIN_SCALE = 1e-3
"""Pixels per mm when zoomed all the way out (1 px = 1 m)."""
MAX_SCALE = 1e4
"""Pixels per mm when zoomed all the way in (1 px = 0.1 µm)."""


@dataclass(slots=True)
class ViewTransform:
    """Maps model millimetres to widget pixels.

    `origin_x`, `origin_y` is where the model origin lands in widget pixels, and `scale` is
    pixels per millimetre.
    """

    scale: float = 4.0
    origin_x: float = 0.0
    origin_y: float = 0.0

    def to_widget(self, point: Point2) -> tuple[float, float]:
        return self.origin_x + point.x * self.scale, self.origin_y - point.y * self.scale

    def to_model(self, x: float, y: float) -> Point2:
        return Point2(x=(x - self.origin_x) / self.scale, y=(self.origin_y - y) / self.scale)

    def length_to_model(self, pixels: float) -> float:
        """A screen distance in mm, e.g. a pick radius for hit-testing."""
        return pixels / self.scale

    def pan(self, dx: float, dy: float) -> None:
        """Move the view by a widget-pixel offset (the content follows the pointer)."""
        self.origin_x += dx
        self.origin_y += dy

    def zoom_about(self, factor: float, x: float, y: float) -> None:
        """Zoom by `factor`, keeping the model point under widget pixel (x, y) fixed."""
        anchor = self.to_model(x, y)
        self.scale = min(MAX_SCALE, max(MIN_SCALE, self.scale * factor))
        self.origin_x = x - anchor.x * self.scale
        self.origin_y = y + anchor.y * self.scale

    def fit(self, box: BoundingBox, width: float, height: float, margin: float = 40.0) -> None:
        """Center `box` in a width-by-height widget with `margin` pixels on every side."""
        usable_w = max(width - 2 * margin, 1.0)
        usable_h = max(height - 2 * margin, 1.0)
        if box.width <= 0 and box.height <= 0:
            scale = self.scale
        elif box.width <= 0:
            scale = usable_h / box.height
        elif box.height <= 0:
            scale = usable_w / box.width
        else:
            scale = min(usable_w / box.width, usable_h / box.height)
        self.scale = min(MAX_SCALE, max(MIN_SCALE, scale))
        center = box.center
        self.origin_x = width / 2 - center.x * self.scale
        self.origin_y = height / 2 + center.y * self.scale
