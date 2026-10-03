"""Where the engine says a face is, against what each kernel builds (ADR 0016).

A face's plane is worked out from its extrude's inputs, never asked of a kernel. This holds
that arithmetic to both kernels' solids: for random plates on any of the three planes, swept
either way, a point inside every named face lies on a triangle of the kernel's mesh, in that
plane and facing the same way; and every triangle of the mesh is on a named face whose plane
it lies in. Runs on the analytic kernel always, and on OCCT when the `occt` extra is there.
"""

import math
from collections.abc import Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from caliper.contracts.commands import CreateExtrude, CreateLine, CreateRectangle, CreateSketch
from caliper.contracts.document import EntityId, ExtrudeOperation, FaceRef, Plane, Point2
from caliper.contracts.errors import Error
from caliper.contracts.kernel import Kernel
from caliper.contracts.queries import Frame, Mesh, Point3
from caliper.engine import part
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry.fake_kernel import FakeKernel

ON_PLANE = 1e-6
"""mm: how far a mesh's vertices may sit from the plane they're on (OCCT's precision)."""
FACING = 1e-9
"""How far a triangle's unit normal may stray from its face's."""


@pytest.fixture(scope="module", params=["fake", "occt"])
def kernel(request: pytest.FixtureRequest) -> Callable[[], Kernel]:
    if request.param == "fake":
        return FakeKernel
    occt = pytest.importorskip(
        "caliper.engine.geometry.occt_kernel", reason="the occt extra isn't installed"
    )
    return occt.OCCTKernel  # type: ignore[no-any-return]


def dot(a: Point3, b: Point3) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def minus(a: Point3, b: Point3) -> Point3:
    return Point3(x=a.x - b.x, y=a.y - b.y, z=a.z - b.z)


def cross(a: Point3, b: Point3) -> Point3:
    return Point3(x=a.y * b.z - a.z * b.y, y=a.z * b.x - a.x * b.z, z=a.x * b.y - a.y * b.x)


def unit(v: Point3) -> Point3:
    size = dot(v, v) ** 0.5
    return Point3(x=v.x / size, y=v.y / size, z=v.z / size)


def lifted(f: Frame, u: float, v: float, w: float = 0.0) -> Point3:
    """(u, v) on the frame, `w` along its normal."""
    n = part.normal(f)
    o = f.origin
    return Point3(
        x=o.x + u * f.x.x + v * f.y.x + w * n.x,
        y=o.y + u * f.x.y + v * f.y.y + w * n.y,
        z=o.z + u * f.x.z + v * f.y.z + w * n.z,
    )


def on_triangle(mesh: Mesh, p: Point3, n: Point3) -> bool:
    """Whether `p` lies on a triangle of `mesh` facing `n`."""
    for i, j, k in mesh.triangles:
        a, b, c = mesh.vertices[i], mesh.vertices[j], mesh.vertices[k]
        normal = cross(minus(b, a), minus(c, a))
        if dot(normal, normal) == 0 or dot(unit(normal), n) < 1 - FACING:
            continue
        if abs(dot(minus(p, a), unit(normal))) > ON_PLANE:
            continue
        # Inside, by the three edges' turns about the normal (the edges included).
        if all(
            dot(cross(minus(q, r), minus(p, r)), normal) >= -1e-9
            for r, q in ((a, b), (b, c), (c, a))
        ):
            return True
    return False


@st.composite
def plates(draw: st.DrawFn) -> tuple[Plane, list[Point2] | None, tuple[float, float], float, bool]:
    """A plane; a convex polygon of lines (or None for a rectangle); the rectangle's size; the
    depth; and whether it's reversed."""
    plane = draw(st.sampled_from(list(Plane)))
    corners = draw(
        st.sampled_from(
            [
                None,
                [Point2(x=0.0, y=0.0), Point2(x=40.0, y=0.0), Point2(x=10.0, y=30.0)],
                [
                    Point2(x=0.0, y=0.0),
                    Point2(x=30.0, y=-10.0),
                    Point2(x=50.0, y=20.0),
                    Point2(x=5.0, y=35.0),
                ],
            ]
        )
    )
    if corners is not None and draw(st.booleans()):
        corners = corners[::-1]  # drawn clockwise
    width = draw(st.integers(5, 200)) / 2
    height = draw(st.integers(5, 200)) / 2
    depth = draw(st.integers(1, 60)) / 2
    return plane, corners, (width, height), depth, draw(st.booleans())


def build(
    kernel: Kernel, made: tuple[Plane, list[Point2] | None, tuple[float, float], float, bool]
) -> tuple[Bus, EntityId, list[Point2]]:
    plane, corners, (width, height), depth, back = made
    bus = Bus(kernel=kernel)
    sketch = bus.execute(CreateSketch(plane=plane)).created_ids[0]  # type: ignore[union-attr]
    if corners is None:
        bus.execute(
            CreateRectangle(corner=Point2(x=0.0, y=0.0), width=width, height=height, sketch=sketch)
        )
        outline = [
            Point2(x=0.0, y=0.0),
            Point2(x=width, y=0.0),
            Point2(x=width, y=height),
            Point2(x=0.0, y=height),
        ]
    else:
        for a, b in zip(corners, [*corners[1:], corners[0]], strict=True):
            bus.execute(CreateLine(start=a, end=b, sketch=sketch))
        outline = corners
    extrude = bus.execute(CreateExtrude(depth=depth, sketch=sketch, reversed=back))
    return bus, extrude.created_ids[0], outline  # type: ignore[union-attr]


def sample(bus: Bus, ref: FaceRef, outline: list[Point2], depth: float, f: Frame) -> Point3:
    """A point inside the face: the outline's centroid on a cap, the middle of a side."""
    extrude = part.feature(bus.document, ref.feature)
    assert extrude is not None
    if ref.face in ("start", "end"):
        cx = sum(p.x for p in outline) / len(outline)
        cy = sum(p.y for p in outline) / len(outline)
        sketch = part.feature(bus.document, extrude.sketch)  # type: ignore[union-attr]
        on = bus.queries.plane_frame(sketch.plane)  # type: ignore[union-attr]
        assert isinstance(on, Frame)
        w = depth if ref.face == "end" else 0.0
        if extrude.reversed:  # type: ignore[union-attr]
            w = -w
        return lifted(on, cx, cy, w)
    # A side: the middle of its segment, half-way across the depth.
    sketch = part.feature(bus.document, extrude.sketch)  # type: ignore[union-attr]
    on = bus.queries.plane_frame(sketch.plane)  # type: ignore[union-attr]
    assert isinstance(on, Frame)
    name = ref.face.removeprefix("side ")
    entity = bus.document.entities.get(EntityId(name.split(".")[0]))
    if "." in name:
        side = name.split(".")[1]
        x0, y0 = entity.corner.x, entity.corner.y  # type: ignore[union-attr]
        w_, h_ = entity.width, entity.height  # type: ignore[union-attr]
        mid = {
            "bottom": (x0 + w_ / 2, y0),
            "right": (x0 + w_, y0 + h_ / 2),
            "top": (x0 + w_ / 2, y0 + h_),
            "left": (x0, y0 + h_ / 2),
        }[side]
    else:
        a, b = entity.start, entity.end  # type: ignore[union-attr]
        mid = ((a.x + b.x) / 2, (a.y + b.y) / 2)
    w = depth / 2 * (-1.0 if extrude.reversed else 1.0)  # type: ignore[union-attr]
    return lifted(on, mid[0], mid[1], w)


@settings(max_examples=25, deadline=None)
@given(made=plates())
def test_every_named_face_is_on_the_kernels_surface_facing_the_same_way(
    kernel: Callable[[], Kernel],
    made: tuple[Plane, list[Point2] | None, tuple[float, float], float, bool],
) -> None:
    bus, extrude, outline = build(kernel(), made)
    mesh = bus.queries.mesh(tolerance=0.01)
    assert not isinstance(mesh, Error), mesh
    names = bus.queries.faces(extrude)
    assert not isinstance(names, Error), names
    assert len(names) == 2 + len(outline)
    for ref in names:
        f = bus.queries.plane_frame(ref)
        assert isinstance(f, Frame), f
        point = sample(bus, ref, outline, made[3], f)
        assert abs(dot(minus(point, f.origin), part.normal(f))) <= ON_PLANE, ref
        assert on_triangle(mesh, point, part.normal(f)), ref


@settings(max_examples=25, deadline=None)
@given(made=plates())
def test_every_triangle_of_the_kernels_mesh_is_on_a_named_face(
    kernel: Callable[[], Kernel],
    made: tuple[Plane, list[Point2] | None, tuple[float, float], float, bool],
) -> None:
    bus, _, _ = build(kernel(), made)
    mesh = bus.queries.mesh(tolerance=0.01)
    assert not isinstance(mesh, Error), mesh
    for i, j, k in mesh.triangles:
        a, b, c = mesh.vertices[i], mesh.vertices[j], mesh.vertices[k]
        normal = cross(minus(b, a), minus(c, a))
        if dot(normal, normal) < 1e-12:
            continue  # a sliver: no direction to speak of
        n = unit(normal)
        centre = Point3(x=(a.x + b.x + c.x) / 3, y=(a.y + b.y + c.y) / 3, z=(a.z + b.z + c.z) / 3)
        found = bus.queries.face_at(centre, n, ON_PLANE * 10)
        assert found is not None, (centre, n)
        f = bus.queries.plane_frame(found)
        assert isinstance(f, Frame)
        for corner in (a, b, c):
            assert abs(dot(minus(corner, f.origin), part.normal(f))) <= ON_PLANE * 10


def test_a_pockets_floor_and_walls_are_on_the_kernels_surface(
    kernel: Callable[[], Kernel],
) -> None:
    """A pocket cut 4 mm down from the plate's top face. Its `start` is where it opens, so no
    surface: a cut's faces are its floor and its walls."""
    bus = Bus(kernel=kernel())
    bus.execute(CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0))
    plate = bus.execute(CreateExtrude(depth=10.0)).created_ids[0]  # type: ignore[union-attr]
    top = bus.execute(CreateSketch(plane=FaceRef(feature=plate, face="end"))).created_ids[0]  # type: ignore[union-attr]
    pocket = bus.execute(
        CreateRectangle(corner=Point2(x=50.0, y=20.0), width=20.0, height=10.0, sketch=top)
    ).created_ids[0]  # type: ignore[union-attr]
    cut = bus.execute(
        CreateExtrude(depth=4.0, sketch=top, operation=ExtrudeOperation.REMOVE)
    ).created_ids[0]  # type: ignore[union-attr]
    mesh = bus.queries.mesh(tolerance=0.01)
    assert not isinstance(mesh, Error), mesh
    samples = {
        "end": Point3(x=60.0, y=25.0, z=6.0),
        f"side {pocket}.bottom": Point3(x=60.0, y=20.0, z=8.0),
        f"side {pocket}.right": Point3(x=70.0, y=25.0, z=8.0),
        f"side {pocket}.top": Point3(x=60.0, y=30.0, z=8.0),
        f"side {pocket}.left": Point3(x=50.0, y=25.0, z=8.0),
    }
    for name, point in samples.items():
        f = bus.queries.plane_frame(FaceRef(feature=cut, face=name))
        assert isinstance(f, Frame)
        assert on_triangle(mesh, point, part.normal(f)), name
        assert bus.queries.face_at(point, part.normal(f), 1e-6) == FaceRef(feature=cut, face=name)


def test_the_replayed_part_modelled_on_its_faces_has_the_volume_its_numbers_give(
    kernel: Callable[[], Kernel],
) -> None:
    """tests/engine/fixtures/face-sketches.caliper: a 120 x 50 plate 12 deep, a 20 x 10 pocket
    4 deep, a 20 x 6 boss 5 out of the side, and a hole of radius 3, 2 deep in the floor."""
    from pathlib import Path

    from caliper.engine.io import snapshot

    path = Path(__file__).resolve().parents[1] / "fixtures" / "face-sketches.caliper"
    bus = Bus(snapshot.load(path), kernel=kernel())
    found = bus.queries.solid_properties()
    assert not isinstance(found, Error), found
    expected = 120 * 50 * 12 - 20 * 10 * 4 + 20 * 6 * 5 - math.pi * 9 * 2
    assert found.volume == pytest.approx(expected, rel=1e-9)
    box = found.bounding_box
    assert box is not None
    assert (box.x_max, box.z_max) == pytest.approx((125.0, 12.0))
