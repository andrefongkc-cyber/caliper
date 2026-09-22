"""Forward-mode automatic differentiation over a few unknowns.

A `Dual` carries a value and its partial derivatives with respect to the solver's unknowns,
keyed by unknown index. Constraint equations are written once as ordinary arithmetic on
Duals, and the solver reads exact gradients from the result, so no equation needs a
hand-derived Jacobian.
"""

import math
from typing import Self

type Grad = dict[int, float]

_NO_GRAD: Grad = {}


class Dual:
    __slots__ = ("g", "v")

    def __init__(self, v: float, g: Grad = _NO_GRAD) -> None:
        self.v = v
        self.g = g
        """Partial derivatives by unknown index. Shared, never mutated after construction."""

    @classmethod
    def variable(cls, value: float, index: int) -> Self:
        return cls(value, {index: 1.0})

    def __repr__(self) -> str:
        return f"Dual({self.v!r}, {self.g!r})"

    def __add__(self, other: "Dual | float") -> "Dual":
        if isinstance(other, Dual):
            return Dual(self.v + other.v, _combine(self.g, 1.0, other.g, 1.0))
        return Dual(self.v + other, self.g)

    def __radd__(self, other: float) -> "Dual":
        return Dual(other + self.v, self.g)

    def __sub__(self, other: "Dual | float") -> "Dual":
        if isinstance(other, Dual):
            return Dual(self.v - other.v, _combine(self.g, 1.0, other.g, -1.0))
        return Dual(self.v - other, self.g)

    def __rsub__(self, other: float) -> "Dual":
        return Dual(other - self.v, _scale(self.g, -1.0))

    def __mul__(self, other: "Dual | float") -> "Dual":
        if isinstance(other, Dual):
            return Dual(self.v * other.v, _combine(self.g, other.v, other.g, self.v))
        return Dual(self.v * other, _scale(self.g, other))

    def __rmul__(self, other: float) -> "Dual":
        return Dual(other * self.v, _scale(self.g, other))

    def __truediv__(self, other: "Dual | float") -> "Dual":
        if isinstance(other, Dual):
            q = self.v / other.v
            return Dual(q, _combine(self.g, 1.0 / other.v, other.g, -q / other.v))
        return Dual(self.v / other, _scale(self.g, 1.0 / other))

    def __neg__(self) -> "Dual":
        return Dual(-self.v, _scale(self.g, -1.0))


def const(value: float) -> Dual:
    return Dual(value)


def sqrt(x: Dual) -> Dual:
    root = math.sqrt(x.v)
    # The derivative is infinite at 0; a zero gradient there keeps the solver finite, and
    # every quantity square-rooted here is a length the domain rules keep positive.
    return Dual(root, _scale(x.g, 0.5 / root) if root > 0.0 else {})


def hypot(x: Dual, y: Dual) -> Dual:
    """Same value as `math.hypot`, so solved lengths agree bit for bit with queries."""
    h = math.hypot(x.v, y.v)
    if h == 0.0:
        return Dual(0.0)
    return Dual(h, _combine(x.g, x.v / h, y.g, y.v / h))


def atan2(y: Dual, x: Dual) -> Dual:
    """Radians, like `math.atan2`."""
    denominator = x.v * x.v + y.v * y.v
    if denominator == 0.0:
        return Dual(0.0)
    return Dual(math.atan2(y.v, x.v), _combine(y.g, x.v / denominator, x.g, -y.v / denominator))


def unit(degrees: Dual) -> tuple[Dual, Dual]:
    """(cos, sin) of an angle in degrees, with values exact at multiples of 90°.

    Queries place arc points the same way, so a solved arc end lands exactly where
    `feature_point` says it is.
    """
    quarter = degrees.v / 90.0
    if quarter.is_integer():
        c, s = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))[int(quarter) % 4]
    else:
        radians = math.radians(degrees.v)
        c, s = math.cos(radians), math.sin(radians)
    per_degree = math.pi / 180.0
    return Dual(c, _scale(degrees.g, -s * per_degree)), Dual(s, _scale(degrees.g, c * per_degree))


def _scale(g: Grad, k: float) -> Grad:
    if not g or k == 1.0:
        return g
    return {i: k * d for i, d in g.items()}


def _combine(a: Grad, ka: float, b: Grad, kb: float) -> Grad:
    if not b:
        return _scale(a, ka)
    if not a:
        return _scale(b, kb)
    out = {i: ka * d for i, d in a.items()}
    for i, d in b.items():
        out[i] = out.get(i, 0.0) + kb * d
    return out
