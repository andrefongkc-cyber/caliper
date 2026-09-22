"""The dimension placement rule: one counter-clockwise convention for every orientation."""

import pytest

from caliper.app.dimension_layout import choose_orientation, layout, offset_for
from caliper.contracts.document import DistanceOrientation as O
from caliper.contracts.document import Point2

P = Point2


def test_horizontal_positive_offset_sits_above_the_midpoint() -> None:
    geo = layout(O.HORIZONTAL, P(x=0, y=0), P(x=100, y=30), offset=10)
    assert geo.start == P(x=0, y=25)  # midpoint y 15, plus 10
    assert geo.end == P(x=100, y=25)
    assert geo.along == (1.0, 0.0)


def test_vertical_positive_offset_sits_left_of_the_midpoint() -> None:
    geo = layout(O.VERTICAL, P(x=0, y=0), P(x=40, y=100), offset=10)
    assert geo.start == P(x=10, y=0)  # midpoint x 20, minus 10
    assert geo.end == P(x=10, y=100)


def test_aligned_positive_offset_is_left_of_a_to_b() -> None:
    geo = layout(O.ALIGNED, P(x=0, y=0), P(x=10, y=0), offset=5)
    assert (geo.start, geo.end) == (P(x=0, y=5), P(x=10, y=5))
    flipped = layout(O.ALIGNED, P(x=10, y=0), P(x=0, y=0), offset=5)
    assert flipped.start.y == -5  # swapping a and b flips the side, for aligned only


def test_swapping_points_keeps_the_side_for_horizontal() -> None:
    one = layout(O.HORIZONTAL, P(x=0, y=0), P(x=100, y=0), offset=8)
    two = layout(O.HORIZONTAL, P(x=100, y=0), P(x=0, y=0), offset=8)
    assert one.start.y == two.start.y == 8


def test_zero_length_aligned_uses_plus_x() -> None:
    geo = layout(O.ALIGNED, P(x=3, y=3), P(x=3, y=3), offset=2)
    assert geo.start == P(x=3, y=5)
    assert geo.along == (1.0, 0.0)


@pytest.mark.parametrize("orientation", list(O))
def test_offset_for_puts_the_line_through_the_placement(orientation: O) -> None:
    a, b, placement = P(x=10, y=5), P(x=70, y=45), P(x=33, y=90)
    geo = layout(orientation, a, b, offset_for(orientation, a, b, placement))
    ux, uy = geo.along
    cross = (placement.x - geo.start.x) * uy - (placement.y - geo.start.y) * ux
    assert cross == pytest.approx(0, abs=1e-9)


@pytest.mark.parametrize(
    ("placement", "expected"),
    [
        (P(x=50, y=80), O.HORIZONTAL),  # above the pair
        (P(x=150, y=20), O.VERTICAL),  # beside it
        (P(x=150, y=80), O.ALIGNED),  # out past both ends
    ],
)
def test_placement_chooses_the_orientation(placement: Point2, expected: O) -> None:
    assert choose_orientation(P(x=0, y=0), P(x=100, y=40), placement) is expected
