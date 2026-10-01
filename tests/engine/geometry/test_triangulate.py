"""The analytic kernel's triangulation: triangles that exactly cover a polygon less its holes."""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from caliper.contracts.document import Point2
from caliper.engine.geometry.triangulate import area, triangulate


def square(x: float, y: float, side: float) -> list[Point2]:
    return [
        Point2(x=x, y=y),
        Point2(x=x + side, y=y),
        Point2(x=x + side, y=y + side),
        Point2(x=x, y=y + side),
    ]


def ring(cx: float, cy: float, r: float, count: int = 24) -> list[Point2]:
    return [
        Point2(
            x=cx + r * math.cos(2 * math.pi * k / count),
            y=cy + r * math.sin(2 * math.pi * k / count),
        )
        for k in range(count)
    ]


def covered(triangles: list[tuple[Point2, Point2, Point2]]) -> float:
    return sum(area(list(t)) for t in triangles)


def test_a_square_is_two_triangles() -> None:
    triangles = triangulate(square(0, 0, 10))
    assert len(triangles) == 2
    assert covered(triangles) == 100.0


def test_any_winding_comes_out_counter_clockwise() -> None:
    for t in triangulate(square(0, 0, 10)[::-1]):
        assert area(list(t)) > 0


def test_an_l_shape_with_a_reflex_corner() -> None:
    shape = [Point2(x=x, y=y) for x, y in [(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)]]
    triangles = triangulate(shape)
    assert covered(triangles) == 300.0
    assert all(area(list(t)) > 0 for t in triangles)


def test_holes_are_left_out() -> None:
    outer = square(0, 0, 100)
    holes = [ring(25, 25, 10), ring(75, 75, 10), square(60, 10, 20)]
    triangles = triangulate(outer, holes)
    expected = area(outer) - sum(abs(area(h)) for h in holes)
    assert math.isclose(covered(triangles), expected, rel_tol=1e-12)
    assert all(area(list(t)) > 0 for t in triangles)
    for t in triangles:  # nothing drawn inside a hole
        cx, cy = sum(p.x for p in t) / 3, sum(p.y for p in t) / 3
        assert math.dist((cx, cy), (25, 25)) > 9.5
        assert math.dist((cx, cy), (75, 75)) > 9.5


@settings(max_examples=60, deadline=None)
@given(
    size=st.floats(min_value=1.0, max_value=1e3),
    count=st.integers(min_value=0, max_value=6),
    seed=st.randoms(use_true_random=False),
)
def test_random_holes_in_a_square_are_covered_exactly(size: float, count: int, seed) -> None:  # type: ignore[no-untyped-def]
    """Holes on a grid of cells, each inside its own cell, so none touch."""
    outer = square(0, 0, size)
    cells = [(i, j) for i in range(3) for j in range(3)]
    seed.shuffle(cells)
    cell = size / 3
    holes = [
        ring((i + 0.5) * cell, (j + 0.5) * cell, cell * seed.uniform(0.1, 0.4), seed.randint(3, 16))
        for i, j in cells[:count]
    ]
    triangles = triangulate(outer, holes)
    expected = area(outer) - sum(abs(area(h)) for h in holes)
    assert math.isclose(covered(triangles), expected, rel_tol=1e-9)
    assert all(area(list(t)) > 0 for t in triangles)
