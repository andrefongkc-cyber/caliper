"""Viewport math: the model ↔ widget transform, grid spacing, and a plane seen at an angle
(ADR 0016). Qt-free but for the backdrop helpers the plane view is built with."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.app.viewport.grid import grid_lines, major_every, minor_spacing, snap_to_grid
from caliper.app.viewport.transform import MAX_SCALE, MIN_SCALE, ViewTransform
from caliper.contracts.document import Point2
from caliper.contracts.queries import BoundingBox

coords = st.floats(min_value=-1e5, max_value=1e5, allow_nan=False)
scales = st.floats(min_value=MIN_SCALE, max_value=MAX_SCALE)


def test_y_axis_flips() -> None:
    view = ViewTransform(scale=2.0, origin_x=100.0, origin_y=100.0)
    assert view.to_widget(Point2(x=10.0, y=10.0)) == (120.0, 80.0)


@given(x=coords, y=coords, scale=scales, ox=coords, oy=coords)
def test_round_trip(x: float, y: float, scale: float, ox: float, oy: float) -> None:
    view = ViewTransform(scale=scale, origin_x=ox, origin_y=oy)
    back = view.to_model(*view.to_widget(Point2(x=x, y=y)))
    tolerance = 1e-9 * max(1.0, abs(x), abs(y), abs(ox) / scale, abs(oy) / scale)
    assert back.x == pytest.approx(x, abs=tolerance)
    assert back.y == pytest.approx(y, abs=tolerance)


@given(factor=st.floats(min_value=0.1, max_value=10.0), px=coords, py=coords)
def test_zoom_keeps_the_point_under_the_cursor(factor: float, px: float, py: float) -> None:
    view = ViewTransform(scale=3.0, origin_x=50.0, origin_y=-20.0)
    before = view.to_model(px, py)
    view.zoom_about(factor, px, py)
    after = view.to_model(px, py)
    assert after.x == pytest.approx(before.x, rel=1e-9, abs=1e-6)
    assert after.y == pytest.approx(before.y, rel=1e-9, abs=1e-6)


def test_zoom_is_clamped() -> None:
    view = ViewTransform()
    view.zoom_about(1e30, 0, 0)
    assert view.scale == MAX_SCALE
    view.zoom_about(1e-30, 0, 0)
    assert view.scale == MIN_SCALE


def test_fit_centers_and_leaves_a_margin() -> None:
    view = ViewTransform()
    view.fit(BoundingBox(x_min=0, y_min=0, x_max=100, y_max=50), 840, 440, margin=20)
    assert view.scale == pytest.approx(8.0)  # 800 / 100 and 400 / 50
    assert view.to_widget(Point2(x=50, y=25)) == pytest.approx((420, 220))


def test_fit_handles_a_zero_height_box() -> None:
    view = ViewTransform()
    view.fit(BoundingBox(x_min=0, y_min=5, x_max=10, y_max=5), 140, 100, margin=20)
    assert view.scale == pytest.approx(10.0)


@given(scale=scales)
def test_minor_spacing_is_1_2_5_and_not_too_dense(scale: float) -> None:
    spacing = minor_spacing(scale)
    assert spacing * scale >= 12.0 * (1 - 1e-9)
    mantissa = spacing / 10.0 ** math.floor(math.log10(spacing))
    assert round(mantissa, 9) in (1.0, 2.0, 5.0)
    assert major_every(spacing) in (2, 5)


def test_grid_lines_include_both_ends() -> None:
    assert list(grid_lines(-10.0, 10.0, 5.0)) == [-2, -1, 0, 1, 2]


def test_snap_to_grid_avoids_float_noise() -> None:
    snapped = snap_to_grid(Point2(x=0.31, y=-0.04), 0.1)
    assert snapped == Point2(x=0.3, y=0.0)
    assert math.copysign(1, snapped.y) == 1


# --- A plane seen at an angle (ADR 0016) ---------------------------------------------------

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from caliper.app.viewport.backdrop import look, plane_view  # noqa: E402
from caliper.app.viewport.camera3d import Camera  # noqa: E402
from caliper.app.viewport.scene3d import facing_camera  # noqa: E402
from caliper.contracts.document import Plane  # noqa: E402
from caliper.contracts.queries import Point3  # noqa: E402
from caliper.engine import faces, part  # noqa: E402

frames = st.sampled_from(
    [
        *(part.frame(plane) for plane in Plane),
        faces.canonical(Point3(x=0.0, y=0.0, z=-1.0), Point3(x=0.0, y=0.0, z=4.0)),
        faces.canonical(Point3(x=0.6, y=-0.8, z=0.0), Point3(x=3.0, y=1.0, z=2.0)),
    ]
)
cameras = st.builds(
    lambda yaw, pitch, scale, x, y, z: Camera(
        target=Point3(x=x, y=y, z=z), yaw=yaw, pitch=pitch, scale=scale
    ),
    st.floats(0.0, 360.0),
    st.floats(-89.0, 89.0),
    st.floats(0.05, 50.0),
    st.floats(-100.0, 100.0),
    st.floats(-100.0, 100.0),
    st.floats(-100.0, 100.0),
)
points = st.builds(Point2, x=st.floats(-200.0, 200.0), y=st.floats(-200.0, 200.0))


def _drawable(camera: Camera, frame: object) -> bool:
    return plane_view(camera, frame, 800, 600).facing > 0.34  # type: ignore[arg-type]


@given(camera=cameras, frame=frames, p=points)
def test_a_plane_view_is_the_cameras_projection_and_round_trips(
    camera: Camera, frame: object, p: Point2
) -> None:
    if not _drawable(camera, frame):
        return
    view = plane_view(camera, frame, 800, 600)  # type: ignore[arg-type]
    seen = camera.project(_on(frame, p), 800, 600)
    assert view.to_widget(p) == pytest.approx((seen.x, seen.y), abs=1e-6)
    back = view.to_model(*view.to_widget(p))
    assert (back.x, back.y) == pytest.approx((p.x, p.y), abs=1e-6)


@given(camera=cameras, frame=frames, angle=st.floats(0.0, 6.283))
def test_the_pick_radius_covers_six_pixels_on_screen_every_way(
    camera: Camera, frame: object, angle: float
) -> None:
    """Every model point within the pick radius's screen circle is within the radius: the
    plane view's stretches are its scale and scale x facing (ADR 0016)."""
    if not _drawable(camera, frame):
        return
    view = plane_view(camera, frame, 800, 600)  # type: ignore[arg-type]
    centre = view.to_widget(Point2(x=0.0, y=0.0))
    at = view.to_model(centre[0] + 6 * math.cos(angle), centre[1] + 6 * math.sin(angle))
    assert math.hypot(at.x, at.y) <= view.pick_length_to_model(6.0) * (1 + 1e-9)
    det = view.a * view.d - view.b * view.c
    assert abs(det) == pytest.approx(view.scale**2 * view.facing, rel=1e-9)


@pytest.mark.parametrize("plane", list(Plane))
def test_facing_a_plane_the_plane_view_is_the_canvas_view(plane: Plane) -> None:
    """Built from the camera that faces the plane at the canvas's scale and centre, the plane
    view puts every point where the canvas's own view does, to a millionth of a pixel."""
    frame = part.frame(plane)
    view = ViewTransform(scale=3.7, origin_x=410.0, origin_y=290.0)
    camera = facing_camera(frame, view.to_model(400, 300), view.scale)
    angled = plane_view(camera, frame, 800, 600)
    assert not angled.mirrored
    assert angled.facing == pytest.approx(1.0)
    for p in (Point2(x=0, y=0), Point2(x=55.5, y=-20.25), Point2(x=-130, y=77)):
        assert angled.to_widget(p) == pytest.approx(view.to_widget(p), abs=1e-6)
    again = ViewTransform()
    look(again, frame, camera, 800, 600)
    assert (again.scale, again.origin_x, again.origin_y) == pytest.approx(
        (view.scale, view.origin_x, view.origin_y)
    )


def test_seen_from_behind_a_plane_view_is_mirrored() -> None:
    frame = part.frame(Plane.XY)
    above = Camera(yaw=-45.0, pitch=60.0, scale=2.0)
    below = Camera(yaw=-45.0, pitch=-60.0, scale=2.0)
    assert not plane_view(above, frame, 800, 600).mirrored
    assert plane_view(below, frame, 800, 600).mirrored


def _on(frame: object, p: Point2) -> Point3:
    o, x, y = frame.origin, frame.x, frame.y  # type: ignore[attr-defined]
    return Point3(
        x=o.x + p.x * x.x + p.y * y.x, y=o.y + p.x * x.y + p.y * y.y, z=o.z + p.x * x.z + p.y * y.z
    )
