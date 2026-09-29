"""The solver's numerical tolerances, in one place: what counts as solved, broken, unchanged,
degenerate, independent, and equivalent, and why each is what it is.

Every tolerance is relative to the size of the geometry it's about, because a sketch can be a
watch part or a floor plan. That size is the `scale`: the largest magnitude among the values in
play, and at least 1 so values near zero don't make a tolerance vanish. Values are millimetres
and degrees together, one scale for both: a sketch with arcs has a scale of at least its largest
angle, up to 360.

The rules, smallest first:

| Name | Relative to scale | Decides |
|---|---|---|
| `SOLVED` | 1e-10 | a relation holds (Newton's convergence test) |
| `UNCHANGED` | 1e-9 | two values are the same geometry; a line has collapsed |
| `INDEPENDENT` | 1e-9 of a row's size | a relation adds something new (rank, redundancy) |
| `BROKEN` | 1e-7 | status reports a relation as conflicting |
| `PRECISION` | 1e-5 | two solvers' answers are the same solution (see below) |

A relation holding within `SOLVED` fixes the geometry that well only where the relations cross
cleanly. Where two meet tangentially (an aligned and a horizontal distance of 1 to the same
point: a double root), a residual of `SOLVED` leaves the point free by about its square root,
`PRECISION`. So two correct solvers, or the same solver stopping one step apart, can differ by
that much there, and by round-off everywhere else.

A solve's tolerances come from the scale of the values it starts from, and whether its
result has collapsed is judged at the larger of that and the scale it ends at: a solve that
squeezes a rectangle to nothing to make a constraint hold is refused, not accepted at the tiny
scale it made (C-12, fixed 2026-09-29). A stored value a solve didn't need to change is only
kept when every relation holds at both scales (`sketch._kept`).
"""

import math
from collections.abc import Iterable

SOLVED = 1e-10
"""A relation holds when its residual is within `SOLVED * scale`: Newton's convergence test.
A million times a double's round-off at that size, so round-off never stops a solve short of
it; and a tenth of a nanometre on a 1 m part, so nothing short of it is visible."""

UNCHANGED = 1e-9
"""Two values of geometry at scale s are the same when they differ by at most `UNCHANGED * s`.
It's also how short a line may get before it has collapsed: shrinking it that far satisfied its
equations by making it vanish, which nobody asked for. A solve keeps a stored value whose change
is within it, as long as every relation still holds within `SOLVED` without the change."""

INDEPENDENT = 1e-9
"""A relation's Jacobian row adds a new direction when what's left of it, after taking out the
directions of the rows before it, exceeds this fraction of its own length. Below it, the
relation repeats the others (redundant) and doesn't count toward the rank."""

ZERO_ROW = 1e-14
"""A Jacobian row shorter than this carries no direction at all (absolute: rows are unit-free
derivatives)."""

IMPLYING = 1e-8
"""Among the relations a redundant one is a combination of, those weighing less than this
fraction of the heaviest are round-off, not causes, and aren't named in the message."""

BROKEN = 1e3 * SOLVED
"""Status calls a relation conflicting when its residual exceeds `BROKEN * scale`. A thousand
times `SOLVED`: geometry stored within tolerance, left unpolished or read back from a file, is
never reported, while a relation that really fails (by a tenth of a micrometre on a 1 m part,
and up) is."""

PRECISION = math.sqrt(SOLVED)
"""Solutions of the same relations agree within `PRECISION * scale`: round-off where the
relations cross cleanly, up to this where they meet tangentially (see the module docstring).
Beyond it, two answers are different solutions."""

DECIDED = 10.0
"""How far from its threshold a split cluster's decision must be to stand. A split cluster
decides alone only what the whole sketch would decide the same way; a relation whose row is
within `DECIDED * INDEPENDENT` of repeating the others, or geometry within `DECIDED *
UNCHANGED` of collapsing at the whole document's scale, could go either way, so the whole
sketch decides."""

NUDGE = 1e-3
"""A solve stuck on a saddle starts again from values moved by up to this fraction of the
scale, in a fixed pattern (`sketch._newton`)."""


def scale(values: Iterable[float]) -> float:
    """The size tolerances are relative to: the largest magnitude, and at least 1."""
    return max(1.0, max((abs(v) for v in values), default=0.0))


def solved(values: Iterable[float]) -> float:
    """The residual within which every relation of a system at these values holds."""
    return SOLVED * scale(values)


def broken(values: Iterable[float]) -> float:
    """The residual beyond which status reports a relation of these values as conflicting."""
    return 1e3 * solved(values)  # BROKEN, in the order it has always been computed


def unchanged(values: Iterable[float]) -> float:
    """The difference within which two values at this scale are the same geometry."""
    return UNCHANGED * scale(values)
