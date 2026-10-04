"""The 3D scene behind a sketch edited in 3D (ADR 0015).

Looking straight at a sketch's plane, an orthographic camera maps the plane to the screen
exactly as the 2D canvas maps a sketch: a scale and an offset. So a sketch is edited in 3D by
the canvas itself, every tool as it is, over the part drawn from the camera that faces the
plane at the canvas's own scale and centre. Panning and zooming the canvas moves the part
behind it with the sketch.

A right drag orbits away from the plane: the camera is then free, the canvas draws only the
scene (the sketch on its plane, like the others), and drawing waits until the view faces the
sketch again (N). No Qt widget here: the canvas asks it to paint and to move.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtGui import QPainter, QPixmap

from caliper.app import theme
from caliper.app.viewport.camera3d import Camera
from caliper.app.viewport.scene3d import facing_camera, on_plane
from caliper.app.viewport.transform import MAX_SCALE, MIN_SCALE, PlaneView, ViewTransform
from caliper.contracts.document import EntityId, FaceRef, Plane, Point2
from caliper.contracts.queries import BoundingBox, Frame, Point3

Painter = Callable[..., None]
"""`View3D.paint_scene`: (painter, camera, width, height, **options)."""
Shows = Callable[[EntityId | None], object]
"""`View3D.shows`: what the scene draws with a sketch left out, equal only when it's the same."""
Solid = Callable[[], object]
"""`View3D.solid_shown`: the solid the scene draws, the part's or a proposal's."""


@dataclass(slots=True)
class Backdrop:
    plane: Plane | FaceRef
    """Where the sketch sits: one of the part's planes, or a face (ADR 0016)."""
    frame: Frame
    """Where that is now: it moves when the face does."""
    sketch: EntityId | None
    """The sketch the canvas draws, left out of the scene while the view faces it."""
    paint_scene: Painter
    shows: Shows
    solid: Solid = lambda: None
    """Changes when the solid behind the sketch does, as when a proposal gains an extrude:
    the canvas then draws the part again, though the document is as it was."""
    free: Camera | None = None
    """The camera while orbited away from the plane; None while facing it."""
    _image: QPixmap | None = None
    _image_key: object = None

    @property
    def facing(self) -> bool:
        return self.free is None

    @property
    def tilt(self) -> float:
        """How squarely the view faces the plane: 1 straight on, 0 edge on (|normal . back|)."""
        if self.free is None:
            return 1.0
        n, back = _normal(self.frame), self.free.axes()[2]
        return abs(n.x * back.x + n.y * back.y + n.z * back.z)

    @property
    def drawable(self) -> bool:
        """Whether the sketch can be drawn on as the view is: facing it, or turned away by up
        to `DRAW_LIMIT` (ADR 0016)."""
        return self.free is None or self.tilt >= DRAW_LIMIT

    def plane_view(self, width: float, height: float) -> PlaneView:
        """The sketch's plane as the free camera sees it."""
        assert self.free is not None
        return plane_view(self.free, self.frame, width, height)

    def fit(
        self, box: BoundingBox, width: float, height: float, margin: float, right_inset: float = 0.0
    ) -> None:
        """Turned away: look at the middle of `box` (on the plane) from where the free camera
        looks, scaled so its corners fit, leaving `right_inset` pixels free on the right."""
        assert self.free is not None
        right, up, _ = self.free.axes()
        corners = [
            _on(self.frame, Point2(x=x, y=y))
            for x in (box.x_min, box.x_max)
            for y in (box.y_min, box.y_max)
        ]
        across = [_dot(c, right) for c in corners]
        down = [_dot(c, up) for c in corners]
        usable_w = max(width - right_inset - 2 * margin, 1.0)
        usable_h = max(height - 2 * margin, 1.0)
        spans = (max(across) - min(across), max(down) - min(down))
        scale = min(
            usable_w / spans[0] if spans[0] > 0 else math.inf,
            usable_h / spans[1] if spans[1] > 0 else math.inf,
        )
        scale = self.free.scale if not math.isfinite(scale) else scale
        scale = min(MAX_SCALE, max(MIN_SCALE, scale))
        middle = _on(self.frame, box.center)
        shift = right_inset / 2 / scale  # the middle of what's left of the inset
        target = Point3(
            x=middle.x + shift * right.x, y=middle.y + shift * right.y, z=middle.z + shift * right.z
        )
        self.free = replace(self.free, target=target, scale=scale)

    def camera(self, view: ViewTransform, width: float, height: float) -> Camera:
        """The camera the scene is drawn from now."""
        if self.free is not None:
            return self.free
        center = view.to_model(width / 2, height / 2)
        return facing_camera(self.frame, center, view.scale)

    def paint(self, painter: QPainter, view: ViewTransform, width: float, height: float) -> None:
        self.paint_scene(
            painter,
            self.camera(view, width, height),
            width,
            height,
            hidden=self.sketch if self.facing else None,
        )

    def draw(
        self, painter: QPainter, view: ViewTransform, width: int, height: int, ratio: float
    ) -> None:
        """What `paint` draws, from the kept `image`, leaving `painter` as painting the scene
        in place leaves it (antialiased, in the theme's font), so what the canvas draws over
        the part comes out as it did."""
        painter.drawPixmap(0, 0, self.image(view, width, height, ratio))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(theme.font())

    def image(self, view: ViewTransform, width: int, height: int, ratio: float) -> QPixmap:
        """What `paint` draws, kept until the camera or what the scene shows changes: an edit
        to the sketch the canvas draws leaves the part behind it as it was, so the canvas
        redraws only the sketch over this (Performance V2.2, Perf-8)."""
        camera = self.camera(view, width, height)
        hidden = self.sketch if self.drawable else None  # the canvas draws it over this
        key = (camera, width, height, ratio, self.shows(hidden))
        if self._image is None or key != self._image_key:
            image = QPixmap(round(width * ratio), round(height * ratio))
            image.setDevicePixelRatio(ratio)
            painter = QPainter(image)
            self.paint_scene(painter, camera, width, height, hidden=hidden)
            painter.end()
            self._image, self._image_key = image, key
        return self._image

    def orbit(self, view: ViewTransform, width: float, height: float) -> None:
        """Leave the plane: the camera that faced it is free to turn from now on."""
        if self.free is None:
            self.free = self.camera(view, width, height)

    def face(self, view: ViewTransform, width: float, height: float) -> None:
        """Look straight at the plane again, from where the free camera was looking."""
        if self.free is not None:
            look(view, self.frame, self.free, width, height)
            self.free = None


DRAW_LIMIT = math.cos(math.radians(70.0))
"""Turned further from the sketch's plane than 70°, a click covers too much of the plane one
way to pick reliably, so drawing waits (ADR 0016)."""


def plane_view(camera: Camera, frame: Frame, width: float, height: float) -> PlaneView:
    """`camera`'s view of the plane `frame` lies on, as an affine map of its 2D coordinates
    (`Camera.project` of `_on(frame, p)`, worked out once)."""
    right, up, back = camera.axes()
    s, t, o = camera.scale, camera.target, frame.origin
    d = Point3(x=o.x - t.x, y=o.y - t.y, z=o.z - t.z)
    n = _normal(frame)
    return PlaneView(
        a=s * _dot(frame.x, right),
        b=s * _dot(frame.y, right),
        c=-s * _dot(frame.x, up),
        d=-s * _dot(frame.y, up),
        tx=width / 2 + s * _dot(d, right),
        ty=height / 2 - s * _dot(d, up),
        scale=s,
        facing=abs(_dot(n, back)),
    )


def _dot(a: Point3, b: Point3) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def _normal(frame: Frame) -> Point3:
    x, y = frame.x, frame.y
    return Point3(x=x.y * y.z - x.z * y.y, y=x.z * y.x - x.x * y.z, z=x.x * y.y - x.y * y.x)


def _on(frame: Frame, p: Point2) -> Point3:
    o, x, y = frame.origin, frame.x, frame.y
    return Point3(
        x=o.x + p.x * x.x + p.y * y.x,
        y=o.y + p.x * x.y + p.y * y.y,
        z=o.z + p.x * x.z + p.y * y.z,
    )


def look(view: ViewTransform, frame: Frame, camera: Camera, width: float, height: float) -> None:
    """Set the canvas's `view` to face the plane of `frame` at `camera`'s scale, centred where
    `camera` looks: the target seen along the plane's normal."""
    center: Point2 = on_plane(frame, camera.target)
    view.scale = min(MAX_SCALE, max(MIN_SCALE, camera.scale))
    view.origin_x = width / 2 - center.x * camera.scale
    view.origin_y = height / 2 + center.y * camera.scale
