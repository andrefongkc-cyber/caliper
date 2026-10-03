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

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtGui import QPainter, QPixmap

from caliper.app import theme
from caliper.app.viewport.camera3d import Camera
from caliper.app.viewport.scene3d import facing_camera, on_plane
from caliper.app.viewport.transform import ViewTransform
from caliper.contracts.document import EntityId, Plane, Point2

Painter = Callable[..., None]
"""`View3D.paint_scene`: (painter, camera, width, height, **options)."""
Shows = Callable[[EntityId | None], object]
"""`View3D.shows`: what the scene draws with a sketch left out, equal only when it's the same."""


@dataclass(slots=True)
class Backdrop:
    plane: Plane
    sketch: EntityId | None
    """The sketch the canvas draws, left out of the scene while the view faces it."""
    paint_scene: Painter
    shows: Shows
    free: Camera | None = None
    """The camera while orbited away from the plane; None while facing it."""
    _image: QPixmap | None = None
    _image_key: object = None

    @property
    def facing(self) -> bool:
        return self.free is None

    def camera(self, view: ViewTransform, width: float, height: float) -> Camera:
        """The camera the scene is drawn from now."""
        if self.free is not None:
            return self.free
        center = view.to_model(width / 2, height / 2)
        return facing_camera(self.plane, center, view.scale)

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
        hidden = self.sketch if self.facing else None
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
            look(view, self.plane, self.free, width, height)
            self.free = None


def look(view: ViewTransform, plane: Plane, camera: Camera, width: float, height: float) -> None:
    """Set the canvas's `view` to face `plane` at `camera`'s scale, centred where `camera`
    looks: the target seen along the plane's normal."""
    center: Point2 = on_plane(plane, camera.target)
    view.scale = camera.scale
    view.origin_x = width / 2 - center.x * camera.scale
    view.origin_y = height / 2 + center.y * camera.scale
