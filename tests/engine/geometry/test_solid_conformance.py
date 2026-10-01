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
from caliper.contracts.kernel import Kernel, KernelError, Loop, Shape
from caliper.contracts.queries import BoundingBox3, Mesh
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
