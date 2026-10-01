"""The 3D view (V2's F5, ADR 0012): the part's solid, drawn from its mesh.

It reads the same document as the 2D canvas, through the session's queries
(`Queries.mesh`, `Queries.solid_properties`), and holds no model of its own. Switching
views changes what's shown, never the document. The mesh is asked for only when the view
is showing and the document has changed since; the engine keeps solids and meshes by identity
(ADR 0013), so asking again costs little.

Drawn with QPainter, no GPU: the triangles facing the viewer, nearest last (the painter's
algorithm), each lit by a light at the viewer, then the edges where faces meet at an angle.
That is enough for parts made of extrusions; a larger part, or one that needs exact hiding
of crossing faces, is where a GPU renderer would take over (ADR 0012).

Left drag orbits, right or middle drag (or Shift with left) pans, the wheel zooms about the
pointer, and F fits the part. The camera is UI state.
"""

import math
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from caliper.app import theme
from caliper.app.session import DocumentSession
from caliper.app.viewport.camera3d import Camera, facing, normal
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import Mesh, Point3

CREASE_DEGREES = 25.0
"""Faces meeting at more than this show the edge between them."""
AMBIENT = 1 / 3
"""How much light a face turned away from the light still gets."""


class View3D(QWidget):
    def __init__(self, session: DocumentSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.camera = Camera()
        self.mesh: Mesh | None = None
        """What's drawn: the solid's mesh, or the last good one while the part fails."""
        self.problem: str | None = None
        """Why there's no solid, or why the one shown is out of date."""
        self.frame_ms: deque[float] = deque(maxlen=120)
        """How long the last frames took to draw, in milliseconds."""
        self._normals: list[Point3] = []
        self._creases: list[tuple[Point3, Point3, int, int]] = []
        self._stale = True
        self._fit = True
        self._drag: tuple[Qt.MouseButton, QPointF, bool] | None = None
        self.setObjectName("view-3d")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 150)
        session.document_changed.connect(self._changed)
        session.document_replaced.connect(self._replaced)

    # --- What's drawn -------------------------------------------------------------------

    def _changed(self) -> None:
        self._stale = True
        if self.isVisible():
            self.refresh()
            self.update()

    def _replaced(self) -> None:
        self.mesh, self._fit = None, True
        self._changed()

    def refresh(self) -> None:
        """Ask the engine for the part's solid again, if the document changed since."""
        if not self._stale:
            return
        self._stale = False
        queries = self.session.queries
        solid = queries.solid_properties()
        if isinstance(solid, Error):
            self._failed(solid)
            return
        box = solid.bounding_box
        if box is None:
            self._show(Mesh(vertices=(), triangles=()))
            self.problem = "The part's solid is empty: everything was cut away"
            return
        size = math.dist((box.x_min, box.y_min, box.z_min), (box.x_max, box.y_max, box.z_max))
        mesh = queries.mesh(tolerance=min(1.0, max(0.01, size / 1000)))
        if isinstance(mesh, Error):
            self._failed(mesh)
            return
        self._show(mesh)
        self.problem = None
        if self._fit:
            self.camera = self.camera.fitted(box, self.width(), self.height())
            self._fit = False

    def _failed(self, error: Error) -> None:
        if error.code is ErrorCode.SELECTION_EMPTY:
            self._show(None)
            self.problem = "No solid yet: draw a closed profile and extrude it"
        elif error.code is ErrorCode.KERNEL_UNAVAILABLE:
            self._show(None)
            self.problem = "Solids need a geometry kernel: uv sync --extra occt"
        else:
            # The last good solid stays on screen, marked out of date (core.md, item 5).
            self.problem = f"Showing the last solid that worked out. {error.message}"

    def _show(self, mesh: Mesh | None) -> None:
        self.mesh = mesh
        self._normals, self._creases = [], []
        if mesh is None:
            return
        v = mesh.vertices
        self._normals = [normal(v[a], v[b], v[c]) for a, b, c in mesh.triangles]
        self._creases = _creases(mesh, self._normals)

    def fit(self) -> None:
        self._fit = True
        self._stale = True
        self.refresh()
        self.update()

    def showEvent(self, event: object) -> None:  # noqa: N802
        self.refresh()
        super().showEvent(event)  # type: ignore[arg-type]

    # --- Drawing ------------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        started = time.perf_counter()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), theme.CANVAS)
        width, height = self.width(), self.height()
        self._axes(painter)
        if self.mesh is not None and self.mesh.triangles:
            self._solid(painter, width, height)
        if self.problem:
            painter.setPen(theme.TEXT_DIM)
            painter.setFont(theme.font())
            box = QRectF(0, height - 40, width, 32)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.problem)
        painter.end()
        self.frame_ms.append(1e3 * (time.perf_counter() - started))

    def _solid(self, painter: QPainter, width: int, height: int) -> None:
        mesh, camera = self.mesh, self.camera
        assert mesh is not None
        flat = [camera.project(p, width, height) for p in mesh.vertices]
        drawn: list[tuple[float, int, float]] = []
        for index, (a, b, c) in enumerate(mesh.triangles):
            light = facing(self._normals[index], camera)
            if light <= 0:
                continue  # facing away: hidden behind the faces that face the viewer
            drawn.append(((flat[a].depth + flat[b].depth + flat[c].depth) / 3, index, light))
        drawn.sort()
        base = theme.SOLID
        for _, index, light in drawn:
            a, b, c = mesh.triangles[index]
            shade = AMBIENT + (1 - AMBIENT) * light
            color = QColor.fromRgbF(
                base.redF() * shade, base.greenF() * shade, base.blueF() * shade
            )
            painter.setPen(QPen(color, 0.75))  # covers the hairline seams between triangles
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([QPointF(flat[i].x, flat[i].y) for i in (a, b, c)]))
        pen = QPen(theme.SOLID_EDGE, theme.GEOMETRY_WIDTH)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for p, q, left, right in self._creases:
            if max(facing(self._normals[left], camera), facing(self._normals[right], camera)) <= 0:
                continue
            a, b = camera.project(p, width, height), camera.project(q, width, height)
            painter.drawLine(QPointF(a.x, a.y), QPointF(b.x, b.y))

    def _axes(self, painter: QPainter) -> None:
        """A small triad in the corner: X, Y, and Z as the camera sees them."""
        right, up, _ = self.camera.axes()
        origin = QPointF(36, self.height() - 36)
        for axis, color in (
            (Point3(x=1.0, y=0.0, z=0.0), theme.AXIS_X),
            (Point3(x=0.0, y=1.0, z=0.0), theme.AXIS_Y),
            (Point3(x=0.0, y=0.0, z=1.0), theme.AXIS_Z),
        ):
            x = axis.x * right.x + axis.y * right.y + axis.z * right.z
            y = axis.x * up.x + axis.y * up.y + axis.z * up.z
            pen = QPen(color, 2.0)
            painter.setPen(pen)
            painter.drawLine(origin, QPointF(origin.x() + 22 * x, origin.y() - 22 * y))

    # --- Moving the camera --------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        panning = event.button() in (
            Qt.MouseButton.RightButton,
            Qt.MouseButton.MiddleButton,
        ) or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._drag = (event.button(), event.position(), panning)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is None:
            return
        button, last, panning = self._drag
        delta = event.position() - last
        if panning:
            self.camera = self.camera.panned(delta.x(), delta.y())
        else:
            self.camera = self.camera.orbited(delta.x(), delta.y())
        self._drag = (button, event.position(), panning)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag = None
        event.accept()

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


def _creases(mesh: Mesh, normals: list[Point3]) -> list[tuple[Point3, Point3, int, int]]:
    """Edges where two triangles meet at more than `CREASE_DEGREES`, or a triangle meets none:
    the part's own edges, not the mesh's. Faces don't share vertices, so edges are matched by
    where their ends are."""
    limit = math.cos(math.radians(CREASE_DEGREES))
    seen: dict[tuple[tuple[float, ...], tuple[float, ...]], tuple[Point3, Point3, int]] = {}
    found: list[tuple[Point3, Point3, int, int]] = []
    v = mesh.vertices
    for index, triangle in enumerate(mesh.triangles):
        for i, j in ((0, 1), (1, 2), (2, 0)):
            p, q = v[triangle[i]], v[triangle[j]]
            key = tuple(sorted((_key(p), _key(q))))
            other = seen.pop(key, None)  # type: ignore[arg-type]
            if other is None:
                seen[key] = (p, q, index)  # type: ignore[index]
                continue
            n, m = normals[index], normals[other[2]]
            if n.x * m.x + n.y * m.y + n.z * m.z < limit:
                found.append((p, q, index, other[2]))
    found += [(p, q, index, index) for p, q, index in seen.values()]  # open edges
    return found


def _key(p: Point3) -> tuple[float, ...]:
    return (round(p.x, 6), round(p.y, 6), round(p.z, 6))
