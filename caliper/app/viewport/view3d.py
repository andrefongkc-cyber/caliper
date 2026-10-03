"""The 3D view (V2's F5, ADR 0012, ADR 0015): the part, from its planes up.

It shows the part's origin and its Top, Front, and Right planes, its sketches on their
planes, and its solid (`scene3d`). It reads the session's queries (`Queries.mesh`,
`Queries.solid_properties`) and holds no model of its own. The mesh is asked for only when
the view is showing and the document has changed since; the engine keeps solids and meshes
by identity (ADR 0013), so asking again costs little.

A click picks a plane or a sketch, which is what Sketch and Extrude act on; a double-click on
one asks to sketch on it or edit it. A left drag orbits, a right or middle drag (or Shift with
a left drag) pans, the wheel zooms about the pointer, and F fits. The camera is UI state.
"""

import math
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

from caliper.app import theme
from caliper.app.session import DocumentSession, Space
from caliper.app.viewport.camera3d import Camera
from caliper.app.viewport.scene3d import Picked, Scene
from caliper.contracts.document import EntityId, Extrude, Plane, Sketch
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import Mesh, Point3

DRAG_PX = 3.0
"""A press that moves less than this before its release is a click, not an orbit."""


class View3D(QWidget):
    open_requested = Signal(object)
    """A double-click on a plane (a `Plane`) or a sketch (its id): sketch on it, or edit it."""

    def __init__(self, session: DocumentSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.camera = Camera()
        self.scene = Scene()
        self.mesh: Mesh | None = None
        """The solid's mesh, or the last good one while the part fails."""
        self.problem: str | None = None
        """Why there's no solid, or why the one shown is out of date."""
        self.frame_ms: deque[float] = deque(maxlen=120)
        """How long the last frames took to draw, in milliseconds."""
        self._stale = True
        self._fit = True
        self._space = session.space
        self._drag: tuple[Qt.MouseButton, QPointF, bool] | None = None
        self._pressed: QPointF | None = None
        """Where a left press was, until it moves far enough to be an orbit."""
        self.setObjectName("view-3d")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 150)
        session.document_changed.connect(self._changed)
        session.document_replaced.connect(self._replaced)
        session.selection_changed.connect(self.update)

    # --- What's drawn -------------------------------------------------------------------

    def _changed(self) -> None:
        self._stale = True
        if self.isVisible():
            self.refresh()
            self.update()

    def _replaced(self) -> None:
        """Another document: fit it, unless it's only the 2D tab's coming and going, which
        leaves the part as it was."""
        space = self.session.space
        if space is Space.PART and self._space is Space.PART:
            self.mesh, self._fit = None, True
        self._space = space
        self._changed()

    def refresh(self) -> None:
        """Ask the engine for the part's solid again, if the document changed since."""
        if not self._stale:
            return
        if self.session.space is not Space.PART:
            # The 2D tab's sketch is never drawn here, so nothing is built for it (Performance
            # V2.2, Perf-8). It has no solid: the part's is asked for again on coming back.
            self.mesh = None
            return
        self._stale = False
        document = self.session.document
        self.problem = None
        if not any(isinstance(f, Extrude) for f in document.features):
            self.mesh = None
            if not any(isinstance(f, Sketch) for f in document.features):
                self.problem = "Pick a plane and press Sketch, or double-click a plane"
        else:
            self._solid()
        self.scene = Scene.of(document, self.mesh)
        if self._fit:
            self.camera = self.camera.fitted(self.scene.box(), self.width(), self.height())
            self._fit = False

    def _solid(self) -> None:
        queries = self.session.queries
        solid = queries.solid_properties()
        if isinstance(solid, Error):
            self._failed(solid)
            return
        box = solid.bounding_box
        if box is None:
            self.mesh = Mesh(vertices=(), triangles=())
            self.problem = "The part's solid is empty: everything was cut away"
            return
        size = math.dist((box.x_min, box.y_min, box.z_min), (box.x_max, box.y_max, box.z_max))
        mesh = queries.mesh(tolerance=min(1.0, max(0.01, size / 1000)))
        if isinstance(mesh, Error):
            self._failed(mesh)
            return
        self.mesh = mesh

    def _failed(self, error: Error) -> None:
        if error.code is ErrorCode.SELECTION_EMPTY:
            self.mesh = None
        elif error.code is ErrorCode.KERNEL_UNAVAILABLE:
            self.mesh = None
            self.problem = "Solids need a geometry kernel: uv sync --extra occt"
        else:
            # The last good solid stays on screen, marked out of date (core.md, item 5).
            self.problem = f"Showing the last solid that worked out. {error.message}"

    def fit(self) -> None:
        self._fit = True
        self._stale = True
        self.refresh()
        self.update()

    def showEvent(self, event: object) -> None:  # noqa: N802
        self.refresh()
        super().showEvent(event)  # type: ignore[arg-type]

    def paint_scene(
        self, painter: QPainter, camera: Camera, width: float, height: float, **options: object
    ) -> None:
        """The scene as `camera` sees it: for the canvas behind a sketch edited in 3D."""
        self.refresh()
        painter.fillRect(QRectF(0, 0, width, height), theme.CANVAS)
        self.scene.paint(painter, camera, width, height, **options)  # type: ignore[arg-type]
        self.paint_triad(painter, camera, height)

    def shows(self, hidden: EntityId | None) -> object:
        """What `paint_scene` draws with the sketch `hidden` left out, as a value equal only
        when it draws the same: the solid (the engine keeps its mesh by identity, so an
        unchanged one compares at once), the other sketches' curves, and the planes' size."""
        self.refresh()
        scene = self.scene
        return (scene.mesh, tuple(c for c in scene.curves if c.sketch != hidden), scene.half)

    # --- Drawing ------------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        started = time.perf_counter()
        painter = QPainter(self)
        width, height = self.width(), self.height()
        painter.fillRect(self.rect(), theme.CANVAS)
        self.scene.paint(
            painter,
            self.camera,
            width,
            height,
            picked=self.session.picked_plane,
            selected=self.session.selection,
        )
        self.paint_triad(painter, self.camera, height)
        if self.problem:
            painter.setPen(theme.TEXT_DIM)
            painter.setFont(theme.font())
            box = QRectF(0, height - 40, width, 32)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.problem)
        painter.end()
        self.frame_ms.append(1e3 * (time.perf_counter() - started))

    @staticmethod
    def paint_triad(painter: QPainter, camera: Camera, height: float) -> None:
        """A small triad in the corner: X, Y, and Z as the camera sees them."""
        right, up, _ = camera.axes()
        origin = QPointF(36, height - 36)
        for axis, color in (
            (Point3(x=1.0, y=0.0, z=0.0), theme.AXIS_X),
            (Point3(x=0.0, y=1.0, z=0.0), theme.AXIS_Y),
            (Point3(x=0.0, y=0.0, z=1.0), theme.AXIS_Z),
        ):
            x = axis.x * right.x + axis.y * right.y + axis.z * right.z
            y = axis.x * up.x + axis.y * up.y + axis.z * up.z
            painter.setPen(QPen(color, 2.0))
            painter.drawLine(origin, QPointF(origin.x() + 22 * x, origin.y() - 22 * y))

    # --- Picking ------------------------------------------------------------------------

    def pick(self, x: float, y: float) -> Picked:
        self.refresh()
        return self.scene.pick(self.camera, self.width(), self.height(), x, y)

    def _choose(self, found: Picked) -> None:
        if isinstance(found, Plane):
            self.session.set_picked_plane(found)
        elif found is None:
            self.session.set_picked_plane(None)
            self.session.set_selection(frozenset())
        else:
            self.session.set_selection(frozenset({found}))

    # --- Moving the camera --------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        panning = event.button() in (
            Qt.MouseButton.RightButton,
            Qt.MouseButton.MiddleButton,
        ) or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._drag = (event.button(), event.position(), panning)
        self._pressed = event.position() if event.button() == Qt.MouseButton.LeftButton else None
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is None:
            return
        button, last, panning = self._drag
        if self._pressed is not None:
            moved = event.position() - self._pressed
            if math.hypot(moved.x(), moved.y()) < DRAG_PX:
                return  # still a click
            self._pressed = None
        delta = event.position() - last
        if panning:
            self.camera = self.camera.panned(delta.x(), delta.y())
        else:
            self.camera = self.camera.orbited(delta.x(), delta.y())
        self._drag = (button, event.position(), panning)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._pressed is not None and event.button() == Qt.MouseButton.LeftButton:
            at = event.position()
            self._choose(self.pick(at.x(), at.y()))
        self._drag, self._pressed = None, None
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            at = event.position()
            found = self.pick(at.x(), at.y())
            if found is not None:
                self._choose(found)
                self.open_requested.emit(found)
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120
        if steps:
            at = event.position()
            self.camera = self.camera.zoomed(
                1.15**steps, (at.x(), at.y()), (self.width(), self.height())
            )
            self.update()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F:
            self.fit()
            return
        super().keyPressEvent(event)
