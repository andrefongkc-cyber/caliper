"""Dense linear algebra for the solver: one rank-revealing factorization, three uses.

`RowBasis` orthogonalizes Jacobian rows in the order given. A row that adds nothing new is
dependent, and the factorization says which earlier rows it is a combination of. That one
pass gives the solver its step (minimum-norm, over the independent rows), the rank for
degrees of freedom, and the constraints that imply a redundant one.

Sizes are a sketch cluster's unknowns (tens to a few hundred), so plain lists suffice.
Jacobian rows are sparse, though (a constraint touches a few unknowns), and so are the
orthonormal rows built from them early on. Each kept row remembers where it is nonzero, and a
product is taken only where both rows can be: skipping the rest changes no result, because
those terms are exactly zero and the compensated sum of the others is the same sum.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from operator import mul

RELATIVE_TOLERANCE = 1e-9
"""A row is dependent when what's left of it after removing the earlier rows' directions
is below this fraction of its own size."""

ZERO_ROW = 1e-14
"""Rows smaller than this carry no direction at all."""


@dataclass
class RowBasis:
    """Rows of a matrix split into an independent set and the rest.

    With the independent rows stacked as J_R, `J_R = L Q`: `q` holds orthonormal rows and
    `lower` is L, lower triangular.
    """

    width: int
    q: list[list[float]] = field(default_factory=list)
    lower: list[list[float]] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)
    """Indices of the independent rows, in the order they were added."""
    dependent: dict[int, list[float]] = field(default_factory=dict)
    """Each dependent row as a combination of the kept rows (aligned with `kept`)."""
    supports: list[tuple[int, ...]] = field(default_factory=list)
    """Where each row of `q` is nonzero, ascending."""
    masks: list[int] = field(default_factory=list)
    """`supports` as bit sets, to see at once that two rows share no column."""

    @property
    def rank(self) -> int:
        return len(self.kept)

    def add(self, index: int, row: Sequence[float]) -> bool:
        """Add row `index`. True if it is independent of the rows already kept."""
        size = _norm(row)
        v = list(row)
        c = [0.0] * len(self.q)
        if size > ZERO_ROW:
            mask = _mask(v)
            for _ in range(2):  # the second pass repairs cancellation in the first
                for j, qj in enumerate(self.q):
                    if not self.masks[j] & mask:
                        continue  # no column in common: the product is exactly zero
                    support = self.supports[j]
                    p = _dot(map(v.__getitem__, support), map(qj.__getitem__, support))
                    if p != 0.0:
                        c[j] += p
                        for i in support:
                            v[i] -= p * qj[i]
                        mask |= self.masks[j]
        rest = _norm(v)
        if size > ZERO_ROW and rest > RELATIVE_TOLERANCE * size:
            unit = [x / rest for x in v]
            support = tuple(i for i, x in enumerate(unit) if x != 0.0)
            self.q.append(unit)
            self.supports.append(support)
            self.masks.append(sum(1 << i for i in support))
            self.lower.append([*c, rest])
            self.kept.append(index)
            return True
        self.dependent[index] = self._in_kept_rows(c) if size > ZERO_ROW else [0.0] * len(c)
        return False

    def step(self, residuals: Sequence[float]) -> list[float]:
        """The minimum-norm Δ with J_R Δ = -r_R, for the kept rows' residuals."""
        k = len(self.kept)
        z = [0.0] * k
        for i in range(k):
            total = -residuals[self.kept[i]]
            row = self.lower[i]
            for j in range(i):
                total -= row[j] * z[j]
            z[i] = total / row[i]
        delta = [0.0] * self.width
        for zi, qi, support in zip(z, self.q, self.supports, strict=True):
            if zi != 0.0:
                for j in support:
                    delta[j] += zi * qi[j]
        return delta

    def free_dimensions(self, columns: Sequence[int]) -> int:
        """How many independent directions the null space has within `columns`.

        That is an entity's remaining degrees of freedom when `columns` are its unknowns:
        the rank of P = I - Qᵀ Q restricted to them.
        """
        n = len(columns)
        p = [
            [
                (1.0 if a == b else 0.0) - sum(qi[columns[a]] * qi[columns[b]] for qi in self.q)
                for b in range(n)
            ]
            for a in range(n)
        ]
        return symmetric_rank(p)

    def _in_kept_rows(self, c: list[float]) -> list[float]:
        """Coefficients a with a · J_R = c · Q, i.e. a L = c, solved by back substitution."""
        k = len(c)
        a = [0.0] * k
        for i in reversed(range(k)):
            total = c[i]
            for j in range(i + 1, k):
                total -= a[j] * self.lower[j][i]
            a[i] = total / self.lower[i][i]
        return a


def symmetric_rank(matrix: list[list[float]], tolerance: float = 1e-8) -> int:
    """Rank of a symmetric positive semi-definite matrix, by pivoted Cholesky."""
    a = [row[:] for row in matrix]
    n = len(a)
    rank = 0
    remaining = list(range(n))
    while remaining:
        pivot = max(remaining, key=lambda i: a[i][i])
        if a[pivot][pivot] <= tolerance:
            break
        rank += 1
        remaining.remove(pivot)
        root = math.sqrt(a[pivot][pivot])
        column = {i: a[i][pivot] / root for i in remaining}
        for i in remaining:
            for j in remaining:
                a[i][j] -= column[i] * column[j]
    return rank


def _dot(a: Iterable[float], b: Iterable[float]) -> float:
    # `sum` of floats is compensated (Neumaier) since Python 3.12: accurate and the same on
    # every platform. Zero terms don't change it, which is what lets `add` leave them out.
    total: float = sum(map(mul, a, b))
    return total


def _mask(v: Sequence[float]) -> int:
    """The columns where `v` is nonzero, as a bit set."""
    bits = 0
    for i, x in enumerate(v):
        if x != 0.0:
            bits |= 1 << i
    return bits


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(map(mul, v, v)))
