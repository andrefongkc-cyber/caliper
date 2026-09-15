"""Viewport math: the model ↔ widget transform and grid spacing. Qt-free."""

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
