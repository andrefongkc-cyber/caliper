"""What the 3D view draws (ADR 0012, ADR 0015): the part's origin and its Top, Front, and Right
planes, its sketches on their planes, and its solid; and picking, which reads it back.

A painter and a camera, no widget: the 3D view draws the scene, and so does the canvas behind
a sketch being edited in 3D, so the two always agree. Nothing here is in the document: the
planes are the part's frame (`part.FRAMES`), and the rest is read from the document and the
engine's mesh.
"""

import math
from dataclasses import dataclass, field
from itertools import pairwise

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

from caliper.app import theme
from caliper.app.viewport.camera3d import Camera, Projected, facing, normal
from caliper.contracts.document import (
    Arc,
    Circle,
    Document,
    EntityId,
    Line,
    Plane,
    Point,
    Point2,
    Rectangle,
    Sketch,
)
from caliper.contracts.kernel import Frame
from caliper.contracts.queries import BoundingBox3, Mesh, Point3
from caliper.engine import part

PLANES = (Plane.XY, Plane.XZ, Plane.YZ)
"""In the order they're listed: Top, Front, Right."""
PLANE_NAMES = {Plane.XY: "Top", Plane.XZ: "Front", Plane.YZ: "Right"}
"""Onshape's names for the part's planes, with Z up: Top is XY."""
MIN_HALF = 50.0
"""Half the side of a plane, in mm, for a part smaller than that."""
SEGMENTS = 64
"""Straight pieces a whole circle is drawn with."""
PICK_PX = 6.0
"""How near, in pixels, a click has to be to a sketch's curve to pick it."""
CREASE_DEGREES = 25.0
"""Faces meeting at more than this show the edge between them."""
AMBIENT = 1 / 3
"""How much light a face turned away from the light still gets."""


@dataclass(frozen=True, slots=True)
class Curve:
    """A sketch's curve where it is in the part: a polyline in 3D."""

    sketch: EntityId
    points: tuple[Point3, ...]
    construction: bool = False


Picked = EntityId | Plane | None
"""What a click found: a sketch (by id), a plane, or nothing."""


@dataclass(slots=True)
class Scene:
    mesh: Mesh | None = None
    """The solid, or the last good one while the part fails."""
    curves: tuple[Curve, ...] = ()
    half: float = MIN_HALF
    """Half the side of each plane: they grow with the part."""
    normals: list[Point3] = field(default_factory=list)
    creases: list[tuple[Point3, Point3, int, int]] = field(default_factory=list)

    @classmethod
    def of(cls, document: Document, mesh: Mesh | None) -> "Scene":
        curves = tuple(_curves(document))
        normals: list[Point3] = []
        creases: list[tuple[Point3, Point3, int, int]] = []
        if mesh is not None:
            v = mesh.vertices
            normals = [normal(v[a], v[b], v[c]) for a, b, c in mesh.triangles]
            creases = _creases(mesh, normals)
        reach = max(
            (
                max(abs(p.x), abs(p.y), abs(p.z))
                for points in (
                    *(c.points for c in curves),
                    mesh.vertices if mesh is not None else (),
                )
                for p in points
            ),
            default=0.0,
        )
        return cls(
            mesh=mesh,
            curves=curves,
            half=max(MIN_HALF, 0.6 * reach),
            normals=normals,
            creases=creases,
        )

    def box(self) -> BoundingBox3:
        """What fitting the view shows: the solid, or else the planes."""
        if self.mesh is not None and self.mesh.vertices:
            v = self.mesh.vertices
            return BoundingBox3(
                x_min=min(p.x for p in v),
                y_min=min(p.y for p in v),
                z_min=min(p.z for p in v),
                x_max=max(p.x for p in v),
                y_max=max(p.y for p in v),
                z_max=max(p.z for p in v),
            )
        h = self.half
        return BoundingBox3(x_min=-h, y_min=-h, z_min=-h, x_max=h, y_max=h, z_max=h)

    # --- Drawing ------------------------------------------------------------------------

    def paint(
        self,
        painter: QPainter,
        camera: Camera,
        width: float,
        height: float,
        *,
        picked: Plane | None = None,
        selected: frozenset[EntityId] = frozenset(),
        hidden: EntityId | None = None,
    ) -> None:
        """The planes behind everything, then the sketches, then the solid, which hides what's
        behind it; a picked sketch last, in sight wherever it is. `hidden` is the sketch the
        canvas draws itself."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._planes(painter, camera, width, height, picked)
        self._sketches(painter, camera, width, height, selected, hidden, picked_only=False)
        if self.mesh is not None and self.mesh.triangles:
            self._solid(painter, camera, width, height)
        self._sketches(painter, camera, width, height, selected, hidden, picked_only=True)
        o = camera.project(part.frame(Plane.XY).origin, width, height)
        painter.setPen(_pen(theme.TEXT_DIM, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(o.x, o.y), 4.0, 4.0)
        painter.drawPoint(QPointF(o.x, o.y))

    def _planes(
        self, painter: QPainter, camera: Camera, width: float, height: float, picked: Plane | None
    ) -> None:
        h = self.half
        for plane in PLANES:
            frame = part.frame(plane)
            corners = [
                camera.project(_on(frame, Point2(x=u, y=v)), width, height)
                for u, v in ((-h, -h), (h, -h), (h, h), (-h, h))
            ]
            chosen = plane is picked
            painter.setPen(_pen(theme.ACCENT if chosen else theme.PLANE, 1.5 if chosen else 1.0))
            painter.setBrush(theme.PICKED_PLANE_FILL if chosen else theme.PLANE_FILL)
            painter.drawPolygon(QPolygonF([QPointF(c.x, c.y) for c in corners]))
            corner = corners[3]  # the plane's own top left: where Onshape names it
            painter.setPen(theme.ACCENT if chosen else theme.PLANE)
            painter.setFont(theme.font())
            painter.drawText(QPointF(corner.x + 6, corner.y + 16), PLANE_NAMES[plane])

    def _solid(self, painter: QPainter, camera: Camera, width: float, height: float) -> None:
        mesh = self.mesh
        assert mesh is not None
        flat = [camera.project(p, width, height) for p in mesh.vertices]
        drawn: list[tuple[float, int, float]] = []
        for index, (a, b, c) in enumerate(mesh.triangles):
            light = facing(self.normals[index], camera)
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
        painter.setPen(_pen(theme.SOLID_EDGE, theme.GEOMETRY_WIDTH))
        for p, q, left, right in self.creases:
            if max(facing(self.normals[left], camera), facing(self.normals[right], camera)) <= 0:
                continue
            a, b = camera.project(p, width, height), camera.project(q, width, height)
            painter.drawLine(QPointF(a.x, a.y), QPointF(b.x, b.y))

    def _sketches(
        self,
        painter: QPainter,
        camera: Camera,
        width: float,
        height: float,
        selected: frozenset[EntityId],
        hidden: EntityId | None,
        *,
        picked_only: bool,
    ) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for curve in self.curves:
            if curve.sketch == hidden or (curve.sketch in selected) != picked_only:
                continue
            if curve.sketch in selected:
                pen = _pen(theme.ACCENT, theme.HIGHLIGHT_WIDTH)
            elif curve.construction:
                pen = _pen(theme.CONSTRUCTION, theme.GUIDE_WIDTH, Qt.PenStyle.DashLine)
            else:
                pen = _pen(theme.GEOMETRY, theme.GEOMETRY_WIDTH)
            painter.setPen(pen)
            flat = [camera.project(p, width, height) for p in curve.points]
            if len(flat) == 1:
                painter.drawEllipse(QPointF(flat[0].x, flat[0].y), 2.0, 2.0)
            else:
                painter.drawPolyline(QPolygonF([QPointF(p.x, p.y) for p in flat]))

    # --- Picking ------------------------------------------------------------------------

    def pick(
        self,
        camera: Camera,
        width: float,
        height: float,
        x: float,
        y: float,
        *,
        hidden: EntityId | None = None,
    ) -> Picked:
        """What's under the pixel (x, y): a sketch's curve within `PICK_PX`, else the nearest
        plane there, else nothing. The solid isn't picked yet: faces have no names (ADR 0014)."""
        best: tuple[float, EntityId] | None = None
        for curve in self.curves:
            if curve.sketch == hidden:
                continue
            flat = [camera.project(p, width, height) for p in curve.points]
            gap = _distance(flat, x, y)
            if gap <= PICK_PX and (best is None or gap < best[0]):
                best = (gap, curve.sketch)
        if best is not None:
            return best[1]
        right, up, back = camera.axes()
        t = camera.target
        su, sv = (x - width / 2) / camera.scale, -(y - height / 2) / camera.scale
        start = Point3(
            x=t.x + su * right.x + sv * up.x,
            y=t.y + su * right.y + sv * up.y,
            z=t.z + su * right.z + sv * up.z,
        )
        nearest: tuple[float, Plane] | None = None
        for plane in PLANES:
            frame = part.frame(plane)
            n = _cross(frame.x, frame.y)
            across = _dot(back, n)
            if abs(across) < 1e-6:
                continue  # edge on
            # The ray start - s * back meets the plane where (start - s * back - origin) . n = 0.
            o = frame.origin
            s = _dot(Point3(x=start.x - o.x, y=start.y - o.y, z=start.z - o.z), n) / across
            hit = Point3(x=start.x - s * back.x, y=start.y - s * back.y, z=start.z - s * back.z)
            d = Point3(x=hit.x - o.x, y=hit.y - o.y, z=hit.z - o.z)
            if abs(_dot(d, frame.x)) <= self.half and abs(_dot(d, frame.y)) <= self.half:
                depth = -s  # larger is nearer the viewer
                if nearest is None or depth > nearest[0]:
                    nearest = (depth, plane)
        return nearest[1] if nearest is not None else None


def facing_camera(plane: Plane, center: Point2, scale: float) -> Camera:
    """A camera looking straight at `plane` from its front, its x to the right and its y up,
    with `center` (in the plane's own coordinates) in the middle of the view: what sketching
    in 3D sees. The plane then maps to the screen as the 2D canvas maps a sketch."""
    frame = part.frame(plane)
    back = _cross(frame.x, frame.y)
    yaw = math.degrees(math.atan2(-frame.x.x, frame.x.y))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, back.z))))
    return Camera(target=_on(frame, center), yaw=yaw, pitch=pitch, scale=scale)


def on_plane(plane: Plane, p: Point3) -> Point2:
    """`p` seen along the plane's normal: its coordinates in the plane."""
    frame = part.frame(plane)
    o = frame.origin
    d = Point3(x=p.x - o.x, y=p.y - o.y, z=p.z - o.z)
    return Point2(x=_dot(d, frame.x), y=_dot(d, frame.y))


def outline(entity: object) -> list[Point2]:
    """A sketch entity as a polyline in its sketch's own coordinates; empty for what isn't
    geometry."""
    match entity:
        case Point(position=p):
            return [p]
        case Line(start=a, end=b):
            return [a, b]
        case Rectangle(corner=c, width=w, height=h):
            return [
                c,
                Point2(x=c.x + w, y=c.y),
                Point2(x=c.x + w, y=c.y + h),
                Point2(x=c.x, y=c.y + h),
                c,
            ]
        case Circle(center=c, radius=r):
            return _arc(c, r, 0.0, 360.0)
        case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
            return _arc(c, r, start, sweep)
    return []


def _curves(document: Document) -> list[Curve]:
    planes = {f.id: f.plane for f in document.features if isinstance(f, Sketch)}
    found = []
    for id in sorted(document.entities):
        entity = document.entities[id]
        points = outline(entity)
        sketch = getattr(entity, "sketch", None)
        if not points or sketch not in planes:
            continue
        frame = part.frame(planes[sketch])
        found.append(
            Curve(
                sketch=sketch,
                points=tuple(_on(frame, p) for p in points),
                construction=bool(getattr(entity, "construction", False)),
            )
        )
    return found


def _arc(center: Point2, radius: float, start: float, sweep: float) -> list[Point2]:
    steps = max(2, math.ceil(SEGMENTS * abs(sweep) / 360.0))
    return [
        Point2(
            x=center.x + radius * math.cos(math.radians(start + sweep * i / steps)),
            y=center.y + radius * math.sin(math.radians(start + sweep * i / steps)),
        )
        for i in range(steps + 1)
    ]


def _on(frame: Frame, p: Point2) -> Point3:
    o, x, y = frame.origin, frame.x, frame.y
    return Point3(
        x=o.x + p.x * x.x + p.y * y.x,
        y=o.y + p.x * x.y + p.y * y.y,
        z=o.z + p.x * x.z + p.y * y.z,
    )


def _distance(flat: list[Projected], x: float, y: float) -> float:
    if len(flat) == 1:
        return math.hypot(flat[0].x - x, flat[0].y - y)
    best = math.inf
    for a, b in pairwise(flat):
        dx, dy = b.x - a.x, b.y - a.y
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - a.x) * dx + (y - a.y) * dy) / length))
        best = min(best, math.hypot(a.x + t * dx - x, a.y + t * dy - y))
    return best


def _pen(color: QColor, width: float, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    return pen


def _dot(a: Point3, b: Point3) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def _cross(a: Point3, b: Point3) -> Point3:
    return Point3(x=a.y * b.z - a.z * b.y, y=a.z * b.x - a.x * b.z, z=a.x * b.y - a.y * b.x)


def _creases(mesh: Mesh, normals: list[Point3]) -> list[tuple[Point3, Point3, int, int]]:
    """Edges where two triangles meet at more than `CREASE_DEGREES`, or a triangle meets none:
    the part's own edges, not the mesh's. Faces don't share vertices, so edges are matched by
    where their ends are."""
    limit = math.cos(math.radians(CREASE_DEGREES))
    seen: dict[tuple[tuple[float, ...], ...], tuple[Point3, Point3, int]] = {}
    found: list[tuple[Point3, Point3, int, int]] = []
    v = mesh.vertices
    for index, triangle in enumerate(mesh.triangles):
        for i, j in ((0, 1), (1, 2), (2, 0)):
            p, q = v[triangle[i]], v[triangle[j]]
            key = tuple(sorted((_key(p), _key(q))))
            other = seen.pop(key, None)
            if other is None:
                seen[key] = (p, q, index)
                continue
            n, m = normals[index], normals[other[2]]
            if n.x * m.x + n.y * m.y + n.z * m.z < limit:
                found.append((p, q, index, other[2]))
    found += [(p, q, index, index) for p, q, index in seen.values()]  # open edges
    return found


def _key(p: Point3) -> tuple[float, ...]:
    return (round(p.x, 6), round(p.y, 6), round(p.z, 6))
