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

from PySide6.QtGui import QPainter

from caliper.app.viewport.camera3d import Camera
from caliper.app.viewport.scene3d import facing_camera, on_plane
from caliper.app.viewport.transform import ViewTransform
from caliper.contracts.document import EntityId, Plane, Point2

Painter = Callable[..., None]
"""`View3D.paint_scene`: (painter, camera, width, height, **options)."""


@dataclass(slots=True)
class Backdrop:
    plane: Plane
    sketch: EntityId | None
    """The sketch the canvas draws, left out of the scene while the view faces it."""
    paint_scene: Painter
    free: Camera | None = None
    """The camera while orbited away from the plane; None while facing it."""

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
