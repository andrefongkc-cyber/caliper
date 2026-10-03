"""The 3D view's camera, as plain maths (no Qt): orthographic, Z up, orbit, pan, zoom, fit."""

import math

import pytest

from caliper.app.viewport.camera3d import ISOMETRIC_PITCH, PITCH_LIMIT, Camera, facing, normal
from caliper.contracts.queries import BoundingBox3, Point3

O = Point3(x=0.0, y=0.0, z=0.0)  # noqa: E741
SIZE = (800.0, 600.0)


def dot(a: Point3, b: Point3) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def test_the_target_is_in_the_middle_and_the_axes_are_square() -> None:
    camera = Camera()
    p = camera.project(O, *SIZE)
    assert (p.x, p.y) == (400.0, 300.0)
    right, up, back = camera.axes()
    for a in (right, up, back):
        assert dot(a, a) == pytest.approx(1.0)
    assert dot(right, up) == pytest.approx(0.0, abs=1e-12)
    assert dot(up, back) == pytest.approx(0.0, abs=1e-12)
    assert up.z > 0  # Z points up the screen


def test_the_default_view_is_isometric_from_the_front_right() -> None:
    camera = Camera()
    assert camera.pitch == pytest.approx(ISOMETRIC_PITCH)
    x = camera.project(Point3(x=1.0, y=0.0, z=0.0), *SIZE)
    y = camera.project(Point3(x=0.0, y=1.0, z=0.0), *SIZE)
    z = camera.project(Point3(x=0.0, y=0.0, z=1.0), *SIZE)
    assert (x.x > 400, x.y > 300) == (True, True)  # +X runs right and down
    assert (y.x > 400, y.y < 300) == (True, True)  # +Y right and up
    assert z.x == pytest.approx(400)  # +Z straight up
    assert z.y < 300
    # Every axis the same length on screen: a cube's diagonal points at the viewer.
    lengths = [math.hypot(p.x - 400, p.y - 300) for p in (x, y, z)]
    assert lengths == pytest.approx([lengths[0]] * 3)


def test_an_orbit_turns_about_z_and_tilts_no_further_than_almost_straight_down() -> None:
    camera = Camera()
    turned = camera.orbited(100.0, 0.0)
    assert turned.yaw != camera.yaw
    assert turned.pitch == camera.pitch
    assert Camera().orbited(0.0, 10_000.0).pitch == PITCH_LIMIT
    assert Camera().orbited(0.0, -10_000.0).pitch == -PITCH_LIMIT


def test_a_pan_keeps_what_was_under_the_pointer_under_it() -> None:
    camera = Camera(scale=5.0)
    p = Point3(x=10.0, y=-3.0, z=7.0)
    before = camera.project(p, *SIZE)
    after = camera.panned(25.0, -40.0).project(p, *SIZE)
    assert (after.x - before.x, after.y - before.y) == pytest.approx((25.0, -40.0))


def test_a_zoom_keeps_the_point_under_the_pointer_still() -> None:
    camera = Camera(scale=5.0)
    at = (620.0, 150.0)
    # The point of the part's space under `at`, on the plane through the target.
    right, up, _ = camera.axes()
    du, dv = (at[0] - 400) / 5.0, -(at[1] - 300) / 5.0
    under = Point3(
        x=du * right.x + dv * up.x, y=du * right.y + dv * up.y, z=du * right.z + dv * up.z
    )
    zoomed = camera.zoomed(2.0, at, SIZE)
    assert zoomed.scale == 10.0
    p = zoomed.project(under, *SIZE)
    assert (p.x, p.y) == pytest.approx(at)


def test_fitting_shows_the_whole_part_from_any_side() -> None:
    box = BoundingBox3(x_min=0.0, y_min=0.0, z_min=0.0, x_max=140.0, y_max=50.0, z_max=10.0)
    for yaw, pitch in ((-45.0, ISOMETRIC_PITCH), (0.0, 0.0), (90.0, 80.0), (200.0, -30.0)):
        camera = Camera(yaw=yaw, pitch=pitch).fitted(box, *SIZE)
        for x in (box.x_min, box.x_max):
            for y in (box.y_min, box.y_max):
                for z in (box.z_min, box.z_max):
                    p = camera.project(Point3(x=x, y=y, z=z), *SIZE)
                    assert 0 <= p.x <= SIZE[0]
                    assert 0 <= p.y <= SIZE[1]


def test_a_face_facing_the_viewer_is_lit_and_one_facing_away_is_not() -> None:
    camera = Camera()
    up = normal(O, Point3(x=1.0, y=0.0, z=0.0), Point3(x=0.0, y=1.0, z=0.0))
    assert (up.x, up.y, up.z) == (0.0, 0.0, 1.0)  # counter-clockwise from above faces up
    assert facing(up, camera) > 0
    assert facing(Point3(x=0.0, y=0.0, z=-1.0), camera) < 0
