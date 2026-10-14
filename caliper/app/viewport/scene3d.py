"""What the 3D view draws (ADR 0012, ADR 0015): the part's origin and its Top, Front, and Right
planes, its sketches on their planes, and its solid; and picking, which reads it back.

A painter and a camera, no widget: the 3D view draws the scene, and so does the canvas behind
a sketch being edited in 3D, so the two always agree. Nothing here is in the document: the
planes are the part's frame (`part.FRAMES`), and the rest is read from the document and the
engine's mesh.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

from caliper.app import theme
from caliper.app.viewport.camera3d import Camera, Projected, normal
from caliper.contracts.document import (
    Arc,
    Circle,
    Document,
    EntityId,
    FaceRef,
    Line,
    Plane,
    Point,
    Point2,
    Rectangle,
)
from caliper.contracts.kernel import Frame
from caliper.contracts.queries import BoundingBox3, Mesh, Point3
from caliper.engine import faces, part

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
FACE_TOLERANCE = 1e-4
"""How far, in mm, a point of the mesh may stray from its face's plane and still be on it."""
FACING = math.cos(math.radians(0.5))
"""A triangle facing within half a degree of a face's way is facing it (as `face_at`)."""


@dataclass(frozen=True, slots=True)
class Curve:
    """A sketch's curve where it is in the part: a polyline in 3D."""

    sketch: EntityId
    points: tuple[Point3, ...]
    construction: bool = False


Picked = EntityId | Plane | FaceRef | None
"""What a click found: a sketch (by id), a plane, a flat face of the solid (ADR 0016), or
nothing."""
FaceNamer = Callable[[Point3, Point3], FaceRef | None]
"""`Queries.face_at` for a point of the solid and the way its triangle faces."""


@dataclass(slots=True)
class Scene:
    mesh: Mesh | None = None
    """The solid, or the last good one while the part fails."""
    curves: tuple[Curve, ...] = ()
    half: float = MIN_HALF
    """Half the side of each plane: they grow with the part."""
    normals: list[Point3] = field(default_factory=list)
    creases: list[tuple[int, int, int, int]] = field(default_factory=list)
    """Each crease's two vertices and the faces either side of it."""
    proposed: bool = False
    """The solid is one an agent's proposal would leave, not the part's: drawn in the agent's
    colour, its edges dashed, until the proposal is accepted or rejected (C-16)."""

    @classmethod
    def of(
        cls,
        document: Document,
        mesh: Mesh | None,
        *,
        proposed: bool = False,
        curves: tuple[Curve, ...] | None = None,
    ) -> "Scene":
        """`curves`, when given, are `document`'s sketches as a scene already worked them
        out: a proposal changes the solid shown, not the document."""
        if curves is None:
            curves = tuple(_curves(document))
        normals: list[Point3] = []
        creases: list[tuple[int, int, int, int]] = []
        reach = max(
            (max(abs(p.x), abs(p.y), abs(p.z)) for c in curves for p in c.points), default=0.0
        )
        if mesh is not None:
            normals, creases, solid = _shape(mesh)
            reach = max(reach, solid)
        return cls(
            mesh=mesh,
            curves=curves,
            half=max(MIN_HALF, 0.6 * reach),
            normals=normals,
            creases=creases,
            proposed=proposed,
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
        picked: Plane | FaceRef | None = None,
        selected: frozenset[EntityId] = frozenset(),
        hidden: EntityId | None = None,
        tinted: frozenset[int] = frozenset(),
    ) -> None:
        """The planes behind everything, then the sketches, then the solid, which hides what's
        behind it; a picked sketch last, in sight wherever it is. `hidden` is the sketch the
        canvas draws itself; `tinted`, the triangles of a picked face."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._planes(painter, camera, width, height, picked if isinstance(picked, Plane) else None)
        self._sketches(painter, camera, width, height, selected, hidden, picked_only=False)
        if self.mesh is not None and self.mesh.triangles:
            self._solid(painter, camera, width, height, tinted)
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
        project = camera.projector(width, height)
        for plane in PLANES:
            frame = part.frame(plane)
            corners = [
                project(_on(frame, Point2(x=u, y=v)))
                for u, v in ((-h, -h), (h, -h), (h, h), (-h, h))
            ]
            chosen = plane is picked
            painter.setPen(_pen(theme.ACCENT if chosen else theme.PLANE, 1.5 if chosen else 1.0))
            painter.setBrush(theme.PICKED_PLANE_FILL if chosen else theme.PLANE_FILL)
            painter.drawPolygon(QPolygonF([QPointF(c[0], c[1]) for c in corners]))
            corner = corners[3]  # the plane's own top left: where Onshape names it
            painter.setPen(theme.ACCENT if chosen else theme.PLANE)
            painter.setFont(theme.font())
            painter.drawText(QPointF(corner[0] + 6, corner[1] + 16), PLANE_NAMES[plane])

    def _solid(
        self,
        painter: QPainter,
        camera: Camera,
        width: float,
        height: float,
        tinted: frozenset[int] = frozenset(),
    ) -> None:
        """The triangles facing the viewer, farthest first (the painter's algorithm), each
        edge drawn just after the nearer of its two faces: a nearer face then covers the
        stretch of it that's out of sight, as the top of a plate covers its hole's far edge."""
        mesh = self.mesh
        assert mesh is not None
        project = camera.projector(width, height)
        flat = [project(p) for p in mesh.vertices]  # each vertex once, for faces and creases
        back = camera.axes()[2]
        bx, by, bz = back.x, back.y, back.z
        normals = self.normals
        depth: dict[int, float] = {}
        light: dict[int, float] = {}
        for index, (a, b, c) in enumerate(mesh.triangles):
            n = normals[index]
            lit = n.x * bx + n.y * by + n.z * bz  # `facing`, the axes worked out once
            if lit > 0:  # facing away: hidden behind the faces that face the viewer
                depth[index] = (flat[a][2] + flat[b][2] + flat[c][2]) / 3
                light[index] = lit
        # (depth, 0 for a face or 1 for an edge, which) so an edge comes after its face.
        order: list[tuple[float, int, int]] = [(d, 0, i) for i, d in depth.items()]
        for k, (_, _, left, right) in enumerate(self.creases):
            seen = [depth[f] for f in (left, right) if f in depth]
            if seen:
                order.append((max(seen), 1, k))
        order.sort()
        if self.proposed:  # as the canvas draws what a proposal adds: the agent's colour
            base = theme.PROPOSED_SOLID
            edge = _pen(theme.AGENT, theme.GEOMETRY_WIDTH, Qt.PenStyle.DashLine)
        else:
            base = theme.SOLID
            edge = _pen(theme.SOLID_EDGE, theme.GEOMETRY_WIDTH)
        for _, kind, index in order:
            if kind == 1:
                p, q, _, _ = self.creases[index]
                a, b = flat[p], flat[q]
                painter.setPen(edge)
                painter.drawLine(QPointF(a[0], a[1]), QPointF(b[0], b[1]))
                continue
            a, b, c = mesh.triangles[index]
            shade = AMBIENT + (1 - AMBIENT) * light[index]
            tint = theme.ACCENT if index in tinted else base
            color = QColor.fromRgbF(
                tint.redF() * shade, tint.greenF() * shade, tint.blueF() * shade
            )
            painter.setPen(QPen(color, 0.75))  # covers the hairline seams between triangles
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([QPointF(flat[i][0], flat[i][1]) for i in (a, b, c)]))

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
        project = camera.projector(width, height)
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
            flat = [project(p) for p in curve.points]
            if len(flat) == 1:
                painter.drawEllipse(QPointF(flat[0][0], flat[0][1]), 2.0, 2.0)
            else:
                painter.drawPolyline(QPolygonF([QPointF(p[0], p[1]) for p in flat]))

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
        faces: FaceNamer | None = None,
    ) -> Picked:
        """What's under the pixel (x, y): a sketch's curve within `PICK_PX` (one the solid is
        in front of is hidden by it); else, with `faces` to name them, the solid there (its
        flat face, or nothing on a curved one), since the planes are drawn see-through over it;
        else the nearest plane there; else nothing."""
        solid = self.solid_hit(camera, width, height, x, y) if faces is not None else None
        # A curve the solid is in front of is hidden by it; one on its surface isn't.
        behind = 2 * PICK_PX / camera.scale
        best: tuple[float, EntityId] | None = None
        for curve in self.curves:
            if curve.sketch == hidden:
                continue
            flat = [camera.project(p, width, height) for p in curve.points]
            gap, depth = _nearest(flat, x, y)
            if solid is not None and solid[0] - depth > behind:
                continue
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
        if solid is not None:  # the planes are drawn see-through: the solid is what's clicked
            assert faces is not None
            return faces(solid[1], solid[2])
        return nearest[1] if nearest is not None else None

    def solid_hit(
        self, camera: Camera, width: float, height: float, x: float, y: float
    ) -> tuple[float, Point3, Point3, int] | None:
        """The solid under the pixel (x, y): the nearest triangle facing the viewer there, as
        its depth (larger is nearer), the point on it, its outward normal, and its index."""
        mesh = self.mesh
        if mesh is None or not mesh.triangles:
            return None
        project = camera.projector(width, height)
        back = camera.axes()[2]
        v = mesh.vertices
        best: tuple[float, Point3, Point3, int] | None = None
        for index, (i, j, k) in enumerate(mesh.triangles):
            n = self.normals[index]
            if n.x * back.x + n.y * back.y + n.z * back.z <= 0:
                continue
            (ax, ay, ad), (bx, by, bd), (cx, cy, cd) = project(v[i]), project(v[j]), project(v[k])
            area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if area == 0:
                continue
            u = ((bx - x) * (cy - y) - (by - y) * (cx - x)) / area
            w = ((cx - x) * (ay - y) - (cy - y) * (ax - x)) / area
            r = 1.0 - u - w
            if min(u, w, r) < -1e-9:
                continue
            depth = u * ad + w * bd + r * cd
            if best is None or depth > best[0]:
                a, b, c = v[i], v[j], v[k]
                point = Point3(
                    x=u * a.x + w * b.x + r * c.x,
                    y=u * a.y + w * b.y + r * c.y,
                    z=u * a.z + w * b.z + r * c.z,
                )
                best = (depth, point, n, index)
        return best

    def face_region(self, seed: int) -> frozenset[int]:
        """The triangles of the flat face triangle `seed` is on: it and every triangle joined
        to it, edge to edge, in its plane and facing its way. Neighbours are found by where
        edges' ends are (`_creases`' rule), worked out once per mesh."""
        mesh = self.mesh
        if mesh is None or not 0 <= seed < len(mesh.triangles):
            return frozenset()
        touching = _adjacency(mesh)
        v, n = mesh.vertices, self.normals[seed]
        anchor = v[mesh.triangles[seed][0]]
        found, waiting = {seed}, [seed]
        while waiting:
            for other in touching[waiting.pop()]:
                if other in found or _dot(self.normals[other], n) < FACING:
                    continue
                corner = v[mesh.triangles[other][0]]
                gap = Point3(x=corner.x - anchor.x, y=corner.y - anchor.y, z=corner.z - anchor.z)
                if abs(_dot(gap, n)) > FACE_TOLERANCE:
                    continue
                found.add(other)
                waiting.append(other)
        return frozenset(found)

    def face_triangles(self, frame: Frame, ref: FaceRef, faces: FaceNamer) -> frozenset[int]:
        """The triangles of the face `ref`: on its plane, facing its way, and named it."""
        mesh = self.mesh
        if mesh is None:
            return frozenset()
        n = _cross(frame.x, frame.y)
        o = frame.origin
        v = mesh.vertices
        found = set()
        for index, (i, j, k) in enumerate(mesh.triangles):
            m = self.normals[index]
            if _dot(m, n) < FACING:
                continue
            a = v[i]
            if abs(_dot(Point3(x=a.x - o.x, y=a.y - o.y, z=a.z - o.z), n)) > FACE_TOLERANCE:
                continue
            b, c = v[j], v[k]
            centre = Point3(
                x=(a.x + b.x + c.x) / 3, y=(a.y + b.y + c.y) / 3, z=(a.z + b.z + c.z) / 3
            )
            if faces(centre, m) == ref:
                found.add(index)
        return frozenset(found)


def facing_camera(frame: Frame, center: Point2, scale: float) -> Camera:
    """A camera looking straight at the plane `frame` lies on, from its front (the side its
    normal points to), its x to the right and its y up, with `center` (in the plane's own
    coordinates) in the middle of the view: what sketching in 3D sees. The plane then maps to
    the screen as the 2D canvas maps a sketch. A sketch's frame has a level x (ADR 0016), so
    no roll is needed."""
    back = _cross(frame.x, frame.y)
    yaw = math.degrees(math.atan2(-frame.x.x, frame.x.y))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, back.z))))
    return Camera(target=_on(frame, center), yaw=yaw, pitch=pitch, scale=scale)


def on_plane(frame: Frame, p: Point3) -> Point2:
    """`p` seen along the frame's normal: its coordinates in the plane."""
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
    """Every sketch's geometry where it is in the part: on its plane, or on its face (ADR
    0016). A sketch whose face is gone isn't drawn."""
    frames = faces.sketch_frames(document)
    found = []
    for id in sorted(document.entities):
        entity = document.entities[id]
        points = outline(entity)
        sketch = getattr(entity, "sketch", None)
        frame = frames.get(sketch) if sketch is not None else None
        if not points or not isinstance(frame, Frame):
            continue
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


def _nearest(flat: list[Projected], x: float, y: float) -> tuple[float, float]:
    """How far the polyline is from (x, y) on screen, and its depth there."""
    if len(flat) == 1:
        return math.hypot(flat[0].x - x, flat[0].y - y), flat[0].depth
    best = (math.inf, 0.0)
    for a, b in pairwise(flat):
        dx, dy = b.x - a.x, b.y - a.y
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - a.x) * dx + (y - a.y) * dy) / length))
        gap = math.hypot(a.x + t * dx - x, a.y + t * dy - y)
        if gap < best[0]:
            best = (gap, a.depth + t * (b.depth - a.depth))
    return best


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


_SHAPES: list[tuple[Mesh, tuple[list[Point3], list[tuple[int, int, int, int]], float]]] = []
"""The last few meshes' normals, creases, and reach, each kept with its mesh. The engine keeps
a solid's mesh by identity (ADR 0013), so a scene for a document whose solid didn't change, as
after a sketch edit, doesn't work them out again (Performance V2.2, Perf-8)."""
SHAPES_KEPT = 4


def _shape(mesh: Mesh) -> tuple[list[Point3], list[tuple[int, int, int, int]], float]:
    for kept, shape in _SHAPES:
        if kept is mesh:
            return shape
    v = mesh.vertices
    normals = [normal(v[a], v[b], v[c]) for a, b, c in mesh.triangles]
    reach = max((max(abs(p.x), abs(p.y), abs(p.z)) for p in v), default=0.0)
    shape = (normals, _creases(mesh, normals), reach)
    _SHAPES.append((mesh, shape))
    del _SHAPES[:-SHAPES_KEPT]
    return shape


_ADJACENT: list[tuple[Mesh, list[list[int]]]] = []
"""The last few meshes' triangle neighbours, each kept with its mesh (as `_SHAPES`)."""


def _adjacency(mesh: Mesh) -> list[list[int]]:
    """Each triangle's neighbours: the triangles sharing an edge with it, by where the edge's
    ends are, since faces don't share vertices."""
    for kept, found in _ADJACENT:
        if kept is mesh:
            return found
    v = mesh.vertices
    by_edge: dict[tuple[tuple[float, ...], ...], list[int]] = {}
    for index, triangle in enumerate(mesh.triangles):
        for i, j in ((0, 1), (1, 2), (2, 0)):
            key = tuple(sorted((_key(v[triangle[i]]), _key(v[triangle[j]]))))
            by_edge.setdefault(key, []).append(index)
    found: list[list[int]] = [[] for _ in mesh.triangles]
    for sharing in by_edge.values():
        for a in sharing:
            found[a].extend(b for b in sharing if b != a)
    _ADJACENT.append((mesh, found))
    del _ADJACENT[:-SHAPES_KEPT]
    return found


def _creases(mesh: Mesh, normals: list[Point3]) -> list[tuple[int, int, int, int]]:
    """Edges where two triangles meet at more than `CREASE_DEGREES`, or a triangle meets none:
    the part's own edges, not the mesh's, by their vertices and the faces either side. Faces
    don't share vertices, so edges are matched by where their ends are."""
    limit = math.cos(math.radians(CREASE_DEGREES))
    seen: dict[tuple[tuple[float, ...], ...], tuple[int, int, int]] = {}
    found: list[tuple[int, int, int, int]] = []
    v = mesh.vertices
    for index, triangle in enumerate(mesh.triangles):
        for i, j in ((0, 1), (1, 2), (2, 0)):
            p, q = triangle[i], triangle[j]
            key = tuple(sorted((_key(v[p]), _key(v[q]))))
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
