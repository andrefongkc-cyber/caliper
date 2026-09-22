"""The solver's building blocks: exact derivatives and the rank-revealing factorization."""

import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from caliper.engine.constraints.ad import Dual, atan2, hypot, sqrt, unit
from caliper.engine.constraints.linalg import RowBasis, symmetric_rank

finite = st.floats(min_value=-50, max_value=50)


def numeric(f: object, x: float, y: float) -> tuple[float, float]:
    """Central differences of f(x, y) with respect to x and y."""
    h = 1e-6
    call = f  # type: ignore[assignment]
    fx = (call(x + h, y) - call(x - h, y)) / (2 * h)  # type: ignore[operator]
    fy = (call(x, y + h) - call(x, y - h)) / (2 * h)  # type: ignore[operator]
    return fx, fy


@given(x=finite, y=finite)
def test_derivatives_match_finite_differences(x: float, y: float) -> None:
    assume(math.hypot(x, y) > 1e-3)
    assume(not (x < 0 and abs(y) < 1e-3))  # atan2's branch cut: no derivative to compare

    def expression(a: float | Dual, b: float | Dual) -> float | Dual:
        if isinstance(a, Dual) and isinstance(b, Dual):
            c, s = unit(a * 3.0)
            return hypot(a, b) * c + atan2(b, a) - s / (b * b + 2.0) + sqrt(a * a + 1.0)
        assert isinstance(a, float)
        assert isinstance(b, float)
        radians = math.radians(a * 3.0)
        return (
            math.hypot(a, b) * math.cos(radians)
            + math.atan2(b, a)
            - math.sin(radians) / (b * b + 2.0)
            + math.sqrt(a * a + 1.0)
        )

    result = expression(Dual.variable(x, 0), Dual.variable(y, 1))
    assert isinstance(result, Dual)
    fx, fy = numeric(expression, x, y)
    assert result.g[0] == pytest.approx(fx, rel=1e-5, abs=1e-5)
    assert result.g[1] == pytest.approx(fy, rel=1e-5, abs=1e-5)


def test_unit_is_exact_at_right_angles_like_queries() -> None:
    for degrees, expected in ((0.0, (1.0, 0.0)), (90.0, (0.0, 1.0)), (-90.0, (0.0, -1.0))):
        c, s = unit(Dual.variable(degrees, 0))
        assert (c.v, s.v) == expected
        assert c.g[0] == pytest.approx(-expected[1] * math.pi / 180)


def test_constants_carry_no_derivative() -> None:
    assert (Dual(2.0) * Dual(3.0) + 1.0).g == {}


def test_dependent_rows_are_found_and_explained_by_the_rows_they_repeat() -> None:
    basis = RowBasis(3)
    assert basis.add(0, [1.0, 0.0, 0.0])
    assert basis.add(1, [0.0, 2.0, 0.0])
    assert not basis.add(2, [3.0, -4.0, 0.0])  # 3·row0 - 2·row1
    assert basis.rank == 2
    assert basis.dependent[2] == pytest.approx([3.0, -2.0])
    assert not basis.add(3, [0.0, 0.0, 0.0])  # a row with no direction
    assert basis.dependent[3] == [0.0, 0.0]


def test_the_step_is_the_smallest_change_that_satisfies_the_kept_rows() -> None:
    basis = RowBasis(3)
    basis.add(0, [1.0, 1.0, 0.0])  # x + y must change by -r
    step = basis.step([-4.0])
    assert step == pytest.approx([2.0, 2.0, 0.0])  # split evenly, z untouched


def test_free_dimensions_count_what_each_group_of_unknowns_can_still_do() -> None:
    basis = RowBasis(4)
    basis.add(0, [1.0, 0.0, -1.0, 0.0])  # x0 = x1
    basis.add(1, [0.0, 1.0, 0.0, -1.0])  # y0 = y1
    assert basis.free_dimensions([0, 1]) == 2  # the point can still go anywhere
    assert basis.free_dimensions([0, 1, 2, 3]) == 2  # but the pair moves together
    assert symmetric_rank([[1.0, 0.0], [0.0, 0.0]]) == 1
