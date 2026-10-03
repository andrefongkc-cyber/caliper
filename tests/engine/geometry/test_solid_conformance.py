"""Kernel conformance for solids (V2's F2, ADR 0001): every Kernel passes this file.

It runs against FakeKernel always, and OCCTKernel whenever the `occt` extra is installed.
Expected values are formulas, never one kernel's answer. Tolerances are the contract:
relative 1e-9, or an absolute bound growing with the shape's size (a real kernel's integrals
are accurate to its geometric tolerance, not to the last bit). A mesh is checked against the
solid it draws: the volume it encloses, its bounds, and every triangle facing out.
"""

import math
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from caliper.contracts.document import Arc, Circle, Geometry, Line, Plane, Point2, Rectangle
from caliper.contracts.errors import ErrorCode
from caliper.contracts.kernel import Frame, Kernel, KernelError, Loop, Shape
from caliper.contracts.queries import BoundingBox3, Mesh, Point3
from caliper.engine.geometry.fake_kernel import FakeKernel
from caliper.engine.part import frame

LINEAR_TOLERANCE = 1e-6
"""mm. About 10x OCCT's default geometric precision (1e-7 mm)."""
MESH_TOLERANCE = 0.01
"""mm: how far a mesh may stray from the surface it draws."""


@pytest.fixture(scope="module", params=["fake", "occt"])
def kernel(request: pytest.FixtureRequest) -> Kernel:
    if request.param == "fake":
        return FakeKernel()
    occt = pytest.importorskip(
        "caliper.engine.geometry.occt_kernel", reason="the occt extra isn't installed"
    )
    made: Kernel = occt.OCCTKernel()
    return made


def approx(expected: float, *, scale: float, dimension: int) -> Any:
    return pytest.approx(expected, rel=1e-9, abs=LINEAR_TOLERANCE * scale ** (dimension - 1))


def plate(
    kernel: Kernel,
    width: float = 120.0,
    height: float = 50.0,
    depth: float = 10.0,
    *,
    corner: Point2 | None = None,
    plane: Plane = Plane.XY,
    holes: tuple[Geometry, ...] = (),
) -> Shape:
    rectangle = Rectangle(corner=corner or Point2(x=0.0, y=0.0), width=width, height=height)
    face = kernel.make_face(Loop(edges=(rectangle,)), [Loop(edges=(h,)) for h in holes])
    return kernel.extrude(face, frame(plane), depth)


def mesh_volume(mesh: Mesh) -> float:
    """The volume a closed mesh encloses, by the divergence theorem: positive when every
    triangle faces out."""
    total = 0.0
    v = mesh.vertices
    for i, j, k in mesh.triangles:
        a, b, c = v[i], v[j], v[k]
        total += (
            a.x * (b.y * c.z - b.z * c.y)
            - a.y * (b.x * c.z - b.z * c.x)
            + a.z * (b.x * c.y - b.y * c.x)
        )
    return total / 6


def mesh_box(mesh: Mesh) -> BoundingBox3:
    v = mesh.vertices
    return BoundingBox3(
        x_min=min(p.x for p in v),
        y_min=min(p.y for p in v),
        z_min=min(p.z for p in v),
        x_max=max(p.x for p in v),
        y_max=max(p.y for p in v),
        z_max=max(p.z for p in v),
    )


def box_tuple(box: BoundingBox3) -> tuple[float, ...]:
    return (box.x_min, box.y_min, box.z_min, box.x_max, box.y_max, box.z_max)


# --- Extrude ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plane", "bounds"),
    [
        (Plane.XY, (0.0, 0.0, 0.0, 120.0, 50.0, 10.0)),  # up +Z
        (Plane.XZ, (0.0, -10.0, 0.0, 120.0, 0.0, 50.0)),  # along -Y
        (Plane.YZ, (0.0, 0.0, 0.0, 10.0, 120.0, 50.0)),  # along +X
    ],
)
def test_the_milestone_plate_on_each_plane(
    kernel: Kernel, plane: Plane, bounds: tuple[float, ...]
) -> None:
    """V2's milestone: 120 x 50, 10 deep, is 60,000 mm3, wherever its sketch sits."""
    solid = plate(kernel, plane=plane)
    assert kernel.is_valid(solid)
    assert kernel.volume(solid) == approx(60_000.0, scale=120.0, dimension=3)
    got = box_tuple(kernel.bounding_box_3d(solid))
    assert got == pytest.approx(bounds, abs=1e-6)


def test_a_wider_plate_is_more_volume(kernel: Kernel) -> None:
    assert kernel.volume(plate(kernel, width=140.0)) == approx(70_000.0, scale=140, dimension=3)


lengths = st.floats(min_value=0.5, max_value=500.0)
coordinates = st.floats(min_value=-500.0, max_value=500.0)


@settings(max_examples=60, deadline=None)
@given(
    width=lengths,
    height=lengths,
    depth=lengths,
    x=coordinates,
    y=coordinates,
    plane=st.sampled_from(list(Plane)),
)
def test_volume_is_area_times_depth_for_any_rectangle(
    kernel: Kernel, width: float, height: float, depth: float, x: float, y: float, plane: Plane
) -> None:
    solid = plate(kernel, width, height, depth, corner=Point2(x=x, y=y), plane=plane)
    scale = max(width, height, depth, abs(x), abs(y), 1.0)
    assert kernel.volume(solid) == approx(width * height * depth, scale=scale, dimension=3)


def rounded(width: float, height: float, radius: float) -> Loop:
    """A rectangle with its corners rounded: four lines and four quarter arcs, in order."""
    w, h, r = width, height, radius
    p = Point2
    edges: list[Geometry] = [
        Line(start=p(x=r, y=0.0), end=p(x=w - r, y=0.0)),
        Arc(center=p(x=w - r, y=r), radius=r, start_angle=270.0, sweep_angle=90.0),
        Line(start=p(x=w, y=r), end=p(x=w, y=h - r)),
        Arc(center=p(x=w - r, y=h - r), radius=r, start_angle=0.0, sweep_angle=90.0),
        Line(start=p(x=w - r, y=h), end=p(x=r, y=h)),
        Arc(center=p(x=r, y=h - r), radius=r, start_angle=90.0, sweep_angle=90.0),
        Line(start=p(x=0.0, y=h - r), end=p(x=0.0, y=r)),
        Arc(center=p(x=r, y=r), radius=r, start_angle=180.0, sweep_angle=90.0),
    ]
    return Loop(edges=tuple(edges))


@settings(max_examples=40, deadline=None)
@given(
    width=st.floats(min_value=10.0, max_value=300.0),
    height=st.floats(min_value=10.0, max_value=300.0),
    corner=st.floats(min_value=0.05, max_value=0.45),
    hole=st.floats(min_value=0.05, max_value=0.4),
    depth=lengths,
    plane=st.sampled_from(list(Plane)),
)
def test_volume_is_area_times_depth_with_arcs_and_holes(
    kernel: Kernel,
    width: float,
    height: float,
    corner: float,
    hole: float,
    depth: float,
    plane: Plane,
) -> None:
    """A rounded plate with a round hole in the middle: lines, arcs, and a hole."""
    small = min(width, height)
    r, hr = corner * small, hole * small
    center = Point2(x=width / 2, y=height / 2)
    face = kernel.make_face(
        rounded(width, height, r), [Loop(edges=(Circle(center=center, radius=hr),))]
    )
    area = width * height - (4 - math.pi) * r * r - math.pi * hr * hr
    solid = kernel.extrude(face, frame(plane), depth)
    scale = max(width, height, depth)
    assert kernel.area_properties(face).area == approx(area, scale=scale, dimension=2)
    assert kernel.volume(solid) == approx(area * depth, scale=scale, dimension=3)


def test_a_depth_must_be_more_than_nothing(kernel: Kernel) -> None:
    face = kernel.make_face(Loop(edges=(Rectangle(corner=Point2(x=0, y=0), width=1, height=1),)))
    for depth in (0.0, -1.0):
        with pytest.raises(KernelError) as raised:
            kernel.extrude(face, frame(Plane.XY), depth)
        assert raised.value.code is ErrorCode.VALUE_NOT_POSITIVE


# --- Union and cut ------------------------------------------------------------------------


def test_a_union_of_solids_apart_adds_their_volumes(kernel: Kernel) -> None:
    a = plate(kernel, 10.0, 10.0, 10.0)
    b = plate(kernel, 10.0, 10.0, 10.0, corner=Point2(x=20.0, y=0.0))
    touching = plate(kernel, 10.0, 10.0, 10.0, corner=Point2(x=10.0, y=0.0))  # shares a face
    joined = kernel.union(kernel.union(a, b), touching)
    assert kernel.volume(joined) == approx(3_000.0, scale=30.0, dimension=3)
    assert box_tuple(kernel.bounding_box_3d(joined)) == pytest.approx(
        (0.0, 0.0, 0.0, 30.0, 10.0, 10.0), abs=1e-6
    )


def test_a_union_with_what_it_already_holds_changes_nothing(kernel: Kernel) -> None:
    big = plate(kernel)
    small = plate(kernel, 10.0, 10.0, 5.0, corner=Point2(x=50.0, y=20.0))
    assert kernel.volume(kernel.union(big, small)) == approx(60_000.0, scale=120, dimension=3)
    assert kernel.volume(kernel.union(small, big)) == approx(60_000.0, scale=120, dimension=3)


def test_a_cut_through_the_whole_depth_leaves_a_hole(kernel: Kernel) -> None:
    cylinder = Circle(center=Point2(x=60.0, y=25.0), radius=10.0)
    face = kernel.make_face(Loop(edges=(cylinder,)))
    tool = kernel.extrude(face, frame(Plane.XY), 10.0)
    holed = kernel.cut(plate(kernel), tool)
    expected = (6_000.0 - math.pi * 100.0) * 10.0
    assert kernel.volume(holed) == approx(expected, scale=120, dimension=3)
    assert kernel.is_valid(holed)
    # The same as a plate drawn with the hole in it.
    drawn = plate(kernel, holes=(cylinder,))
    assert kernel.volume(drawn) == approx(expected, scale=120, dimension=3)


def test_a_cut_that_misses_changes_nothing_and_one_that_covers_leaves_nothing(
    kernel: Kernel,
) -> None:
    solid = plate(kernel, 10.0, 10.0, 10.0)
    far = plate(kernel, 10.0, 10.0, 10.0, corner=Point2(x=50.0, y=0.0))
    assert kernel.volume(kernel.cut(solid, far)) == approx(1_000.0, scale=60, dimension=3)
    covering = plate(kernel, 30.0, 30.0, 20.0, corner=Point2(x=-10.0, y=-10.0))
    nothing = kernel.cut(solid, covering)
    assert kernel.volume(nothing) == pytest.approx(0.0, abs=1e-6)
    with pytest.raises(KernelError) as raised:
        kernel.bounding_box_3d(nothing)
    assert raised.value.code is ErrorCode.SELECTION_EMPTY


def test_overlapping_solids_are_joined_exactly_or_refused_never_guessed(kernel: Kernel) -> None:
    """Two 10 mm cubes overlapping by half: 1,500 mm3 joined, 500 left after a cut. The
    analytic kernel says it can't; OCCT does it."""
    a = plate(kernel, 10.0, 10.0, 10.0)
    b = plate(kernel, 10.0, 10.0, 10.0, corner=Point2(x=5.0, y=0.0))
    if isinstance(kernel, FakeKernel):
        for combine in (kernel.union, kernel.cut):
            with pytest.raises(KernelError) as raised:
                combine(a, b)
            assert raised.value.code is ErrorCode.KERNEL_UNSUPPORTED
        return
    assert kernel.volume(kernel.union(a, b)) == approx(1_500.0, scale=15, dimension=3)
    assert kernel.volume(kernel.cut(a, b)) == approx(500.0, scale=15, dimension=3)


# --- Parallel planes (ADR 0016) -----------------------------------------------------------
# A sketch on a face sits on its extrude's plane moved by the depth, or turned over for a bottom
# face, and a cut from it goes back into the part. Both kernels combine these exactly.

XY = frame(Plane.XY)
TURNED = Frame(origin=XY.origin, x=XY.x, y=Point3(x=0.0, y=-1.0, z=0.0))
"""The plate's bottom face, seen from below: x as XY's, y reversed, the normal down."""


def lifted(on: Frame, w: float) -> Frame:
    """`on` moved `w` along its normal."""
    n = Point3(
        x=on.x.y * on.y.z - on.x.z * on.y.y,
        y=on.x.z * on.y.x - on.x.x * on.y.z,
        z=on.x.x * on.y.y - on.x.y * on.y.x,
    )
    o = on.origin
    return Frame(origin=Point3(x=o.x + w * n.x, y=o.y + w * n.y, z=o.z + w * n.z), x=on.x, y=on.y)


def prism(kernel: Kernel, edge: Geometry | Loop, on: Frame, depth: float) -> Shape:
    loop = edge if isinstance(edge, Loop) else Loop(edges=(edge,))
    return kernel.extrude(kernel.make_face(loop), on, depth)


def slot(cx: float, cy: float, length: float, radius: float) -> Loop:
    """A stadium of two lines and two arcs, centred on (cx, cy)."""
    a, b = cx - length / 2, cx + length / 2
    return Loop(
        edges=(
            Line(start=Point2(x=a, y=cy - radius), end=Point2(x=b, y=cy - radius)),
            Arc(center=Point2(x=b, y=cy), radius=radius, start_angle=270.0, sweep_angle=180.0),
            Line(start=Point2(x=b, y=cy + radius), end=Point2(x=a, y=cy + radius)),
            Arc(center=Point2(x=a, y=cy), radius=radius, start_angle=90.0, sweep_angle=180.0),
        )
    )


def closed_mesh_volume(kernel: Kernel, solid: Shape, curved: float, depth: float) -> None:
    """The mesh encloses the solid's volume, to the mesh tolerance along `curved` mm of arcs."""
    mesh = kernel.mesh(solid, MESH_TOLERANCE)
    allowed = 2 * MESH_TOLERANCE * curved * depth + LINEAR_TOLERANCE * 1e4
    assert abs(mesh_volume(mesh) - kernel.volume(solid)) <= allowed


def test_a_pocket_cut_down_from_the_top_face(kernel: Kernel) -> None:
    circle = Circle(center=Point2(x=60.0, y=25.0), radius=10.0)
    pocket = kernel.cut(plate(kernel), prism(kernel, circle, lifted(XY, 6.0), 4.0))
    expected = 60_000.0 - math.pi * 100.0 * 4.0
    assert kernel.volume(pocket) == approx(expected, scale=120, dimension=3)
    assert box_tuple(kernel.bounding_box_3d(pocket)) == pytest.approx(
        (0.0, 0.0, 0.0, 120.0, 50.0, 10.0), abs=1e-6
    )
    closed_mesh_volume(kernel, pocket, 2 * math.pi * 10.0, 4.0)


def test_pockets_cut_up_from_the_bottom_face_turned_over(kernel: Kernel) -> None:
    """In the bottom face's frame y runs the other way: (u, v) is (u, -v) on the plate. A
    rectangle and a slot of lines and arcs, each cut 3 mm up into it."""
    up = lifted(TURNED, -3.0)  # back along the downward normal: the cut goes up 3 mm
    rectangle = Rectangle(corner=Point2(x=10.0, y=-40.0), width=20.0, height=15.0)
    once = kernel.cut(plate(kernel), prism(kernel, rectangle, up, 3.0))
    twice = kernel.cut(once, prism(kernel, slot(80.0, -25.0, 20.0, 5.0), up, 3.0))
    slot_area = 20.0 * 10.0 + math.pi * 25.0
    expected = 60_000.0 - (300.0 + slot_area) * 3.0
    assert kernel.volume(twice) == approx(expected, scale=120, dimension=3)
    closed_mesh_volume(kernel, twice, 2 * math.pi * 5.0, 3.0)


def test_a_slot_inside_the_depth_and_a_cut_through_from_the_top(kernel: Kernel) -> None:
    inside = Rectangle(corner=Point2(x=50.0, y=20.0), width=20.0, height=10.0)
    hollow = kernel.cut(plate(kernel), prism(kernel, inside, lifted(XY, 3.0), 4.0))
    assert kernel.volume(hollow) == approx(60_000.0 - 800.0, scale=120, dimension=3)
    closed_mesh_volume(kernel, hollow, 0.0, 4.0)
    circle = Circle(center=Point2(x=20.0, y=25.0), radius=5.0)
    through = kernel.cut(plate(kernel), prism(kernel, circle, lifted(XY, -5.0), 15.0))
    expected = 60_000.0 - math.pi * 25.0 * 10.0
    assert kernel.volume(through) == approx(expected, scale=120, dimension=3)


@settings(max_examples=40, deadline=None)
@given(
    x=st.integers(1, 90),
    y=st.integers(1, 30),
    width=st.integers(1, 28),
    height=st.integers(1, 18),
    start=st.integers(-12, 12),
    depth=st.integers(1, 24),
    below=st.booleans(),
)
def test_any_pocket_along_the_normal_removes_its_overlap_with_the_plate(
    kernel: Kernel, x: int, y: int, width: int, height: int, start: int, depth: int, below: bool
) -> None:
    """A rectangle inside the 120 x 50 plate, cut over any stretch of its 10 mm depth (or
    none), drawn from above or turned over from below: exactly area x overlap is removed."""
    if below:  # in the turned-over frame, y is reversed and w runs down from z = 0
        tool = prism(
            kernel,
            Rectangle(corner=Point2(x=x, y=-(y + height)), width=width, height=height),
            lifted(TURNED, -float(start + depth)),
            float(depth),
        )
    else:
        rectangle = Rectangle(corner=Point2(x=x, y=y), width=width, height=height)
        tool = prism(kernel, rectangle, lifted(XY, float(start)), float(depth))
    overlap = max(0, min(start + depth, 10) - max(start, 0))
    expected = 60_000.0 - width * height * overlap
    assert kernel.volume(kernel.cut(plate(kernel), tool)) == approx(
        expected, scale=120, dimension=3
    )


def test_a_boss_joined_on_the_top_face(kernel: Kernel) -> None:
    boss = Rectangle(corner=Point2(x=10.0, y=10.0), width=30.0, height=20.0)
    joined = kernel.union(plate(kernel), prism(kernel, boss, lifted(XY, 10.0), 5.0))
    assert kernel.volume(joined) == approx(60_000.0 + 3_000.0, scale=120, dimension=3)
    assert box_tuple(kernel.bounding_box_3d(joined)) == pytest.approx(
        (0.0, 0.0, 0.0, 120.0, 50.0, 15.0), abs=1e-6
    )


def test_a_pocket_across_the_edge_is_exact_or_refused(kernel: Kernel) -> None:
    """Half the tool hangs off the plate: 10 x 20 of it cuts 4 deep. Only OCCT does this."""
    across = Rectangle(corner=Point2(x=110.0, y=10.0), width=20.0, height=20.0)
    tool = prism(kernel, across, lifted(XY, 6.0), 4.0)
    if isinstance(kernel, FakeKernel):
        with pytest.raises(KernelError) as raised:
            kernel.cut(plate(kernel), tool)
        assert raised.value.code is ErrorCode.KERNEL_UNSUPPORTED
        return
    expected = 60_000.0 - 200.0 * 4.0
    assert kernel.volume(kernel.cut(plate(kernel), tool)) == approx(
        expected, scale=120, dimension=3
    )


def test_the_box_of_a_prism_on_a_slanted_plane_is_exact(kernel: Kernel) -> None:
    """A 10 mm circle on a plane whose x is (0.6, 0.8, 0) and y is +Z, 5 deep along its normal
    (0.8, -0.6, 0): the box is the circle's reach along each axis, plus the depth's."""
    slanted = Frame(
        origin=Point3(x=0.0, y=0.0, z=0.0),
        x=Point3(x=0.6, y=0.8, z=0.0),
        y=Point3(x=0.0, y=0.0, z=1.0),
    )
    solid = prism(kernel, Circle(center=Point2(x=0.0, y=0.0), radius=10.0), slanted, 5.0)
    assert box_tuple(kernel.bounding_box_3d(solid)) == pytest.approx(
        (-6.0, -11.0, -10.0, 10.0, 8.0, 10.0), abs=1e-6
    )


# --- Meshes -------------------------------------------------------------------------------


@pytest.mark.parametrize("plane", list(Plane))
def test_a_mesh_of_straight_edges_encloses_the_volume_exactly_facing_out(
    kernel: Kernel, plane: Plane
) -> None:
    solid = plate(kernel, plane=plane)
    mesh = kernel.mesh(solid, MESH_TOLERANCE)
    assert mesh.triangles
    assert mesh_volume(mesh) == approx(60_000.0, scale=120, dimension=3)  # > 0: outward
    assert box_tuple(mesh_box(mesh)) == pytest.approx(
        box_tuple(kernel.bounding_box_3d(solid)), abs=1e-6
    )
    assert all(0 <= i < len(mesh.vertices) for t in mesh.triangles for i in t)


def test_a_mesh_of_arcs_and_holes_is_within_its_tolerance(kernel: Kernel) -> None:
    hole = Circle(center=Point2(x=60.0, y=25.0), radius=10.0)
    face = kernel.make_face(rounded(120.0, 50.0, 12.0), [Loop(edges=(hole,))])
    solid = kernel.extrude(face, frame(Plane.XZ), 10.0)
    volume = kernel.volume(solid)
    mesh = kernel.mesh(solid, MESH_TOLERANCE)
    curved = 2 * math.pi * 12.0 + 2 * math.pi * 10.0  # the arcs' length
    assert abs(mesh_volume(mesh) - volume) <= 2 * MESH_TOLERANCE * curved * 10.0
    assert mesh_volume(mesh) > 0  # facing out


def test_a_mesh_of_nothing_is_no_triangles(kernel: Kernel) -> None:
    solid = plate(kernel, 10.0, 10.0, 10.0)
    covering = plate(kernel, 30.0, 30.0, 20.0, corner=Point2(x=-10.0, y=-10.0))
    assert kernel.mesh(kernel.cut(solid, covering), MESH_TOLERANCE).triangles == ()
