"""Grid spacing and grid snapping. UI conveniences, not geometry."""

import math

from caliper.contracts.document import Point2

MIN_MINOR_PIXELS = 12.0
"""Minor grid lines closer than this on screen are too dense to be useful."""


def minor_spacing(scale: float) -> float:
    """The smallest 1-2-5 x 10^n mm step whose lines are at least MIN_MINOR_PIXELS apart."""
    target = MIN_MINOR_PIXELS / scale
    exponent = math.floor(math.log10(target))
    for step in (1.0, 2.0, 5.0, 10.0):
        spacing = step * 10.0**exponent
        if spacing >= target * (1 - 1e-9):
            return spacing
    raise AssertionError("unreachable: 10 x 10^n is always >= target")


def major_every(minor: float) -> int:
    """How many minor steps make a major line: 5 for 1- and 10-based steps, else 5 or 2."""
    leading = round(minor / 10.0 ** math.floor(math.log10(minor)))
    return 5 if leading in (1, 2) else 2


def grid_lines(lo: float, hi: float, spacing: float) -> range:
    """Integer multiples k of `spacing` with lo ≤ k·spacing ≤ hi."""
    return range(math.ceil(lo / spacing), math.floor(hi / spacing) + 1)


def snap_to_grid(point: Point2, spacing: float) -> Point2:
    """Nearest grid point, rounded to the spacing's decimals so files store 0.3, not
    0.30000000000000004."""
    digits = max(0, -math.floor(math.log10(spacing)))

    def snap(value: float) -> float:
        return round(round(value / spacing) * spacing, digits) + 0.0  # + 0.0 turns -0.0 into 0.0

    return Point2(x=snap(point.x), y=snap(point.y))
