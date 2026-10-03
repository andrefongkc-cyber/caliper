"""Model ↔ widget coordinates. The only place the Y axis flips.

The model is Y-up millimetres; Qt widgets are Y-down pixels (logical pixels: Qt applies the
device pixel ratio underneath, which keeps Retina drawing sharp). Plain floats, no Qt, so
the math is testable on its own.
"""

import math
from dataclasses import dataclass
from typing import Protocol

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

    def pick_length_to_model(self, pixels: float) -> float:
        """The radius in mm that covers `pixels` on screen in every direction: here, the same."""
        return pixels / self.scale

    def direction_to_widget(self, dx: float, dy: float) -> tuple[float, float]:
        """A unit model direction as a unit widget direction: Y flips."""
        return dx, -dy

    @property
    def grid_scale(self) -> float:
        """Pixels per mm the grid's spacing is chosen for."""
        return self.scale

    @property
    def mirrored(self) -> bool:
        return False

    def key(self) -> tuple[float, ...]:
        """Equal exactly when two views draw the same."""
        return (self.scale, self.origin_x, self.origin_y)

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


@dataclass(frozen=True, slots=True)
class PlaneView:
    """A sketch's plane seen at an angle by an orthographic camera (ADR 0016): an affine map,
    widget = (a x + b y + tx, c x + d y + ty) for the model point (x, y).

    Its two stretches (singular values) are `scale` and `scale * facing`, where `facing` is
    how squarely the plane faces the view (1 straight on, 0 edge on). Seen from behind, it is
    `mirrored`.
    """

    a: float
    b: float
    c: float
    d: float
    tx: float
    ty: float
    scale: float
    """Pixels per mm along the plane's direction square to the tilt."""
    facing: float

    def to_widget(self, point: Point2) -> tuple[float, float]:
        return (
            self.a * point.x + self.b * point.y + self.tx,
            self.c * point.x + self.d * point.y + self.ty,
        )

    def to_model(self, x: float, y: float) -> Point2:
        det = self.a * self.d - self.b * self.c
        dx, dy = x - self.tx, y - self.ty
        return Point2(x=(self.d * dx - self.b * dy) / det, y=(self.a * dy - self.c * dx) / det)

    def length_to_model(self, pixels: float) -> float:
        """A screen distance in mm for drawing (arrows, gaps): never longer than intended."""
        return pixels / self.scale

    def pick_length_to_model(self, pixels: float) -> float:
        """The radius in mm that covers `pixels` on screen in every direction: the smallest
        circle on the plane holding the screen circle's pre-image (ADR 0016)."""
        return pixels / (self.scale * self.facing)

    def direction_to_widget(self, dx: float, dy: float) -> tuple[float, float]:
        wx, wy = self.a * dx + self.b * dy, self.c * dx + self.d * dy
        length = math.hypot(wx, wy)
        return (wx / length, wy / length) if length else (0.0, 0.0)

    @property
    def grid_scale(self) -> float:
        """The grid is spaced for the plane's most foreshortened direction."""
        return self.scale * self.facing

    @property
    def mirrored(self) -> bool:
        """Seen from behind: the plane's x and y turn the other way round on screen."""
        return self.a * self.d - self.b * self.c > 0

    def key(self) -> tuple[float, ...]:
        return (self.a, self.b, self.c, self.d, self.tx, self.ty)


class View(Protocol):
    """What draws and picks on the canvas: a `ViewTransform` facing the sketch, or a
    `PlaneView` at an angle."""

    @property
    def scale(self) -> float: ...

    @property
    def grid_scale(self) -> float: ...

    @property
    def mirrored(self) -> bool: ...

    def to_widget(self, point: Point2) -> tuple[float, float]: ...

    def to_model(self, x: float, y: float) -> Point2: ...

    def length_to_model(self, pixels: float) -> float: ...

    def pick_length_to_model(self, pixels: float) -> float: ...

    def direction_to_widget(self, dx: float, dy: float) -> tuple[float, float]: ...

    def key(self) -> tuple[float, ...]: ...
