"""Where geometry is: a grid of each geometry entity's covering box, one per document.

Queries that only care about geometry near a point or a box (picking, box selection,
constraint suggestions) ask the grid for candidates instead of checking every entity. The
grid only narrows the search: it returns every entity whose covering box meets the query
box, and perhaps a few more, and callers still run their exact test on each. So no answer
depends on it, only how fast it comes.

A covering box holds the entity's whole outline, its inside, and every point feature. An
arc's is its full circle's, which holds its centre.
"""

import math
from collections import OrderedDict
from collections.abc import Iterable

from caliper.contracts.document import (
    Arc,
    Circle,
    Document,
    Entity,
    EntityId,
    Line,
    Point,
    Rectangle,
)

type Box = tuple[float, float, float, float]
"""x_min, y_min, x_max, y_max."""

WIDE_CELLS = 64
"""An entity covering more cells than this is kept apart and checked on every query, so a
huge shape doesn't fill the grid."""


def cover(entity: Entity) -> Box | None:
    """The covering box of a geometry entity; None for anything else."""
    match entity:
        case Point(position=p):
            return (p.x, p.y, p.x, p.y)
        case Line(start=a, end=b):
            return (min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))
        case Circle(center=c, radius=r) | Arc(center=c, radius=r):
            return (c.x - r, c.y - r, c.x + r, c.y + r)
        case Rectangle(corner=c, width=w, height=h):
            return (c.x, c.y, c.x + w, c.y + h)
    return None


def around(box: Box, reach: float) -> Box:
    """`box` grown by `reach` on every side."""
    return (box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach)


class Grid:
    def __init__(self, document: Document) -> None:
        self.boxes: dict[EntityId, Box] = {
            id: box
            for id, entity in document.entities.items()
            if (box := cover(entity)) is not None
        }
        self._cells: dict[tuple[int, int], list[EntityId]] = {}
        self._wide: list[EntityId] = []
        if not self.boxes:
            self._origin, self._cell = (0.0, 0.0), 1.0
            return
        x_min = min(b[0] for b in self.boxes.values())
        y_min = min(b[1] for b in self.boxes.values())
        width = max(b[2] for b in self.boxes.values()) - x_min
        height = max(b[3] for b in self.boxes.values()) - y_min
        # About one entity per cell, even for a sketch that is long and thin.
        n = len(self.boxes)
        side = max(width, height)
        self._origin = (x_min, y_min)
        self._cell = math.sqrt(max(width * height, side * side / n) / n) or 1.0
        for id, box in self.boxes.items():
            i0, j0, i1, j1 = self._span(box)
            if (i1 - i0 + 1) * (j1 - j0 + 1) > WIDE_CELLS:
                self._wide.append(id)
                continue
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    self._cells.setdefault((i, j), []).append(id)

    def near(self, x: float, y: float, reach: float) -> list[EntityId]:
        """Candidates within `reach` of (x, y), sorted by id."""
        return self.overlapping((x - reach, y - reach, x + reach, y + reach))

    def overlapping(self, box: Box) -> list[EntityId]:
        """Candidates whose covering box meets `box`, sorted by id.

        `box` is first grown by a hair, so a caller's exact test that passes by rounding
        at the very edge of its reach still finds its entity here.
        """
        slack = 1e-9 * (1.0 + max(abs(v) for v in box))
        box = around(box, slack)
        found: Iterable[EntityId]
        lo_x, lo_y, hi_x, hi_y = self._scaled(box)
        cells = (hi_x - lo_x + 1) * (hi_y - lo_y + 1)
        if not math.isfinite(cells) or cells > len(self._cells):
            found = (id for ids in self._cells.values() for id in ids)
        else:
            i0, j0, i1, j1 = math.floor(lo_x), math.floor(lo_y), math.floor(hi_x), math.floor(hi_y)
            found = (
                id
                for i in range(i0, i1 + 1)
                for j in range(j0, j1 + 1)
                for id in self._cells.get((i, j), ())
            )
        hits = {id for id in (*found, *self._wide) if _meets(self.boxes[id], box)}
        return sorted(hits)

    def _scaled(self, box: Box) -> Box:
        (x0, y0), cell = self._origin, self._cell
        return (
            (box[0] - x0) / cell,
            (box[1] - y0) / cell,
            (box[2] - x0) / cell,
            (box[3] - y0) / cell,
        )

    def _span(self, box: Box) -> tuple[int, int, int, int]:
        lo_x, lo_y, hi_x, hi_y = self._scaled(box)
        return math.floor(lo_x), math.floor(lo_y), math.floor(hi_x), math.floor(hi_y)


def _meets(a: Box, b: Box) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


_GRIDS: OrderedDict[int, tuple[Document, Grid]] = OrderedDict()
"""Grids of recent documents by identity, as `DocumentQueries` keeps solve status: documents
are immutable, and holding one keeps its id from being reused."""


def grid(document: Document) -> Grid:
    """The grid of `document`, built on first use."""
    key = id(document)
    cached = _GRIDS.get(key)
    if cached is not None and cached[0] is document:
        _GRIDS.move_to_end(key)
        return cached[1]
    built = Grid(document)
    _GRIDS[key] = (document, built)
    while len(_GRIDS) > 4:
        _GRIDS.popitem(last=False)
    return built
