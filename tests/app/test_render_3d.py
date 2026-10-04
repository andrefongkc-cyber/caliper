"""The 3D view and the part behind a sketch edited in 3D, drawn with less work and exactly as
before (Performance V2.2, Perf-8): the camera's axes worked out once a frame, a mesh's normals
and creases once per mesh, the part behind a sketch kept as an image until it changes, and no
3D scene built for the 2D tab's sketch.
"""

import math
from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

from caliper.app import theme
from caliper.app.main_window import MainWindow
from caliper.app.viewport import scene3d
from caliper.app.viewport.backdrop import Backdrop
from caliper.app.viewport.camera3d import Camera, facing, normal
from caliper.app.viewport.scene3d import Scene
from caliper.contracts.commands import (
    Applied,
    CreateCircle,
    CreateDistanceDimension,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    ModifyEntity,
)
from caliper.contracts.document import DistanceOrientation, Feature, Plane, Point2, Ref
from caliper.contracts.queries import Mesh, Point3
from caliper.engine import features, geometry
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.app.parts import plate, sketch_on

finite = st.floats(min_value=-1e4, max_value=1e4, allow_nan=False)


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same solids with OCCT or without it, as `test_view3d.py` has them."""
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def projected_per_point(camera: Camera, p: Point3, width: float, height: float) -> tuple:
    """`Camera.project` as it was before Perf-8: the axes worked out for every point."""
    yaw, pitch = math.radians(camera.yaw), math.radians(camera.pitch)
    back = Point3(
        x=math.cos(pitch) * math.cos(yaw), y=math.cos(pitch) * math.sin(yaw), z=math.sin(pitch)
    )
    right = Point3(x=-math.sin(yaw), y=math.cos(yaw), z=0.0)
    up = Point3(
        x=back.y * right.z - back.z * right.y,
        y=back.z * right.x - back.x * right.z,
        z=back.x * right.y - back.y * right.x,
    )
    t = camera.target
    d = Point3(x=p.x - t.x, y=p.y - t.y, z=p.z - t.z)

    def dot(a: Point3, b: Point3) -> float:
        return a.x * b.x + a.y * b.y + a.z * b.z

    return (
        width / 2 + dot(d, right) * camera.scale,
        height / 2 - dot(d, up) * camera.scale,
        dot(d, back),
    )


@given(
    target=st.builds(Point3, x=finite, y=finite, z=finite),
    yaw=st.floats(min_value=0.0, max_value=360.0),
    pitch=st.floats(min_value=-89.0, max_value=89.0),
    scale=st.floats(min_value=1e-3, max_value=1e3),
    points=st.lists(st.builds(Point3, x=finite, y=finite, z=finite), max_size=10),
    size=st.tuples(st.integers(1, 3000), st.integers(1, 3000)),
)
def test_a_frames_projector_projects_each_point_as_before_to_the_bit(
    target: Point3, yaw: float, pitch: float, scale: float, points: list[Point3], size: tuple
) -> None:
    camera = Camera(target=target, yaw=yaw, pitch=pitch, scale=scale)
    project = camera.projector(*size)
    for p in points:
        assert project(p) == projected_per_point(camera, p, *size)
        seen = camera.project(p, *size)
        assert (seen.x, seen.y, seen.depth) == projected_per_point(camera, p, *size)


def test_a_meshs_normals_and_creases_are_worked_out_once(window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    plate(window)
    view = window.view3d
    view.refresh()
    mesh = view.mesh
    assert mesh is not None
    assert mesh.triangles
    v = mesh.vertices
    normals = [normal(v[a], v[b], v[c]) for a, b, c in mesh.triangles]
    assert view.scene.normals == normals
    assert view.scene.creases == scene3d._creases(mesh, normals)
    worked: list[object] = []
    real = scene3d.normal
    monkeypatch.setattr(scene3d, "normal", lambda *p: (worked.append(1), real(*p))[1])
    Scene.of(window.session.document, mesh)  # another document, the same solid
    assert worked == []
    again = Scene.of(window.session.document, Mesh(vertices=v, triangles=mesh.triangles))
    assert len(worked) == len(mesh.triangles)  # an equal mesh, but not the engine's
    assert again.normals == normals


def test_the_part_behind_a_sketch_is_drawn_again_only_when_it_changes(
    window: MainWindow,
) -> None:
    _, _, extrude = plate(window)
    sketch_on(window, Plane.XZ)
    canvas, session = window.canvas, window.session
    backdrop = canvas.backdrop
    assert backdrop is not None
    drawn: list[object] = []
    real = backdrop.paint_scene
    backdrop.paint_scene = lambda *a, **k: (drawn.append(1), real(*a, **k))[1]
    canvas.repaint()
    drawn.clear()
    session.execute(CreateLine(start=Point2(x=10.0, y=20.0), end=Point2(x=60.0, y=40.0)))
    canvas.repaint()
    assert drawn == []  # an edit to the sketch drawn over it leaves the part as it was
    session.execute(ModifyEntity(id=extrude, changes={"depth": 25.0}))
    canvas.repaint()
    assert drawn == [1]  # the solid changed
    canvas.view.scale *= 1.25
    canvas.repaint()
    assert drawn == [1, 1]  # the view moved
    session.execute(CreateLine(start=Point2(x=0.0, y=0.0), end=Point2(x=900.0, y=10.0)))
    canvas.repaint()
    assert drawn == [1, 1, 1]  # the sketch outgrew the planes, which grow with it
    kept = canvas.grab().toImage()
    backdrop._image = None
    canvas._layer = None
    assert canvas.grab().toImage() == kept


def test_the_sketch_over_the_part_is_drawn_as_over_the_scene_painted_in_place(
    window: MainWindow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """The canvas draws the sketch over the kept image of the part, with the painter as
    painting the scene in place left it, so the grid and the dimensions' labels come out as
    they did: the same pixels as painting the part in place, as before Perf-8."""
    plate(window)
    sketch_on(window, Plane.XZ)
    session = window.session
    (line,) = session.execute(  # type: ignore[union-attr]
        CreateLine(start=Point2(x=10.0, y=20.0), end=Point2(x=90.0, y=60.0))
    ).created_ids
    session.execute(
        CreateDistanceDimension(
            a=Ref(entity=line, feature=Feature.START),
            b=Ref(entity=line, feature=Feature.END),
            orientation=DistanceOrientation.ALIGNED,
            offset=8.0,
        )
    )
    canvas = window.canvas
    kept = canvas.grab().toImage()
    monkeypatch.setattr(
        Backdrop, "draw", lambda self, qp, view, w, h, ratio: self.paint(qp, view, w, h)
    )
    canvas._layer = None
    assert canvas.grab().toImage() == kept


def solid_per_point(
    scene: Scene,
    painter: QPainter,
    camera: Camera,
    width: float,
    height: float,
    tinted: frozenset[int] = frozenset(),
) -> None:
    """`Scene._solid` as it was before Perf-8: every vertex and crease end projected with
    `Camera.project`, and each face's light from `facing`, the axes worked out each time."""
    mesh = scene.mesh
    assert mesh is not None
    flat = [camera.project(p, width, height) for p in mesh.vertices]
    depth: dict[int, float] = {}
    light: dict[int, float] = {}
    for index, (a, b, c) in enumerate(mesh.triangles):
        lit = facing(scene.normals[index], camera)
        if lit > 0:
            depth[index] = (flat[a].depth + flat[b].depth + flat[c].depth) / 3
            light[index] = lit
    order: list[tuple[float, int, int]] = [(d, 0, i) for i, d in depth.items()]
    for k, (_, _, left, right) in enumerate(scene.creases):
        seen = [depth[f] for f in (left, right) if f in depth]
        if seen:
            order.append((max(seen), 1, k))
    order.sort()
    base = theme.SOLID
    edge = scene3d._pen(theme.SOLID_EDGE, theme.GEOMETRY_WIDTH)
    for _, kind, index in order:
        if kind == 1:
            p, q, _, _ = scene.creases[index]
            v = mesh.vertices
            a, b = camera.project(v[p], width, height), camera.project(v[q], width, height)
            painter.setPen(edge)
            painter.drawLine(QPointF(a.x, a.y), QPointF(b.x, b.y))
            continue
        a, b, c = mesh.triangles[index]
        shade = scene3d.AMBIENT + (1 - scene3d.AMBIENT) * light[index]
        color = QColor.fromRgbF(base.redF() * shade, base.greenF() * shade, base.blueF() * shade)
        painter.setPen(QPen(color, 0.75))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(flat[i].x, flat[i].y) for i in (a, b, c)]))


def test_a_frame_is_drawn_as_it_was_with_the_axes_worked_out_per_point(
    window: MainWindow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    sketch_on(window, Plane.XY)  # a plate with holes: creases inside, faces edge on
    for command in (
        CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0),
        *(CreateCircle(center=Point2(x=x, y=25.0), radius=8.0) for x in (20.0, 60.0, 100.0)),
    ):
        assert isinstance(window.session.execute(command), Applied)
    window.finish_sketch()
    assert isinstance(window.session.execute(CreateExtrude(depth=10.0)), Applied)
    view = window.view3d
    view.resize(480, 320)
    view.fit()
    base = view.camera
    cameras = [
        replace(base, yaw=yaw, pitch=pitch, scale=base.scale * zoom)
        for yaw, pitch, zoom in ((-45, 35, 1.0), (10, 20, 1.5), (135, -30, 0.8), (270, 89, 1.0))
    ]
    assert view.scene.creases
    drawn = []
    for camera in cameras:
        view.camera = camera
        drawn.append(view.grab().toImage())
    monkeypatch.setattr(Scene, "_solid", solid_per_point)
    for camera, image in zip(cameras, drawn, strict=True):
        view.camera = camera
        assert view.grab().toImage() == image


def test_no_3d_scene_is_built_for_the_2d_tabs_sketch(window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    plate(window)
    part = window.session.document
    built: list[object] = []
    real = Scene.of
    monkeypatch.setattr(
        Scene,
        "of",
        classmethod(lambda cls, d, m, **options: (built.append(d), real(d, m, **options))[1]),
    )
    window.set_mode("2d")
    assert built == []
    window.set_mode("3d")
    assert built
    assert all(d is part for d in built)
    assert window.view3d.mesh is not None
