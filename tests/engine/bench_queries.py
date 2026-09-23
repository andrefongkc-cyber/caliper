"""Time the queries the shell calls on every change and pointer move.

Run `uv run python tests/engine/bench_queries.py`. Not a test: pytest doesn't collect it. It
builds the sketch `tests/app/bench_canvas.py --constrained` uses, without Qt: rectangles,
circles, lines, and arcs on a grid, 1 in 20 a dimension, every line horizontal and fixed at
its start. Then it times, as medians in ms:

- `solve_status` on a document seen for the first time, and after one edit (an unconstrained
  rectangle, a constrained line), which reuses every cluster the edit didn't touch
- picking with the grid built, and the first pick after an edit, which builds it
- `suggest_constraints` for one of the constrained lines (#28's case), and for a new line
  drawn on a rectangle
"""

import argparse
import math
import statistics
import sys
import time
from collections.abc import Callable
from functools import partial
from types import MappingProxyType

from caliper.contracts.commands import Applied, CreateLine, ModifyEntity
from caliper.contracts.document import (
    Arc,
    Circle,
    Constraint,
    ConstraintType,
    DistanceDimension,
    DistanceOrientation,
    Document,
    Entity,
    EntityId,
    Feature,
    Line,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.queries import BoundingBox
from caliper.engine.commands.bus import Bus

CELL_MM = 20.0


def sketch(count: int) -> Document:
    """`bench_canvas.sketch(count, constrained=True)`, entity for entity."""
    entities: dict[EntityId, Entity] = {}
    constraints: list[Constraint] = []
    side = math.ceil(math.sqrt(count))
    n = 0
    last_rectangle: EntityId | None = None
    while n < count:
        row, col = divmod(n, side)
        x, y = col * CELL_MM, row * CELL_MM
        n += 1
        eid = EntityId(f"e{n}")
        if n % 20 == 0 and last_rectangle is not None:
            entities[eid] = DistanceDimension(
                a=Ref(entity=last_rectangle, feature=Feature.BOTTOM_LEFT),
                b=Ref(entity=last_rectangle, feature=Feature.BOTTOM_RIGHT),
                orientation=DistanceOrientation.HORIZONTAL,
                offset=-3.0,
            )
            continue
        match n % 4:
            case 0:
                entities[eid] = Rectangle(corner=Point2(x=x + 2, y=y + 2), width=14.0, height=9.0)
                last_rectangle = eid
            case 1:
                entities[eid] = Circle(center=Point2(x=x + 10, y=y + 10), radius=6.0)
            case 2:
                entities[eid] = Line(start=Point2(x=x + 2, y=y + 9), end=Point2(x=x + 17, y=y + 9))
                constraints += [
                    Constraint(
                        type=ConstraintType.HORIZONTAL,
                        refs=(Ref(entity=eid, feature=Feature.CURVE),),
                    ),
                    Constraint(
                        type=ConstraintType.FIX, refs=(Ref(entity=eid, feature=Feature.START),)
                    ),
                ]
            case _:
                entities[eid] = Arc(
                    center=Point2(x=x + 10, y=y + 8),
                    radius=7.0,
                    start_angle=15.0,
                    sweep_angle=150.0,
                )
    for constraint in constraints:
        n += 1
        entities[EntityId(f"e{n}")] = constraint
    return Document(entities=MappingProxyType(entities), next_id=n + 1)


def once(action: Callable[[], object]) -> float:
    start = time.perf_counter_ns()
    action()
    return (time.perf_counter_ns() - start) / 1e6


def bench(count: int, runs: int) -> list[tuple[str, float]]:
    median = statistics.median
    rows: list[tuple[str, float]] = []
    first_sight = [once(Bus(sketch(count), kernel=None).queries.solve_status) for _ in range(3)]
    rows.append(("solve_status, first sight of a document", median(first_sight)))

    bus = Bus(sketch(count), kernel=None)
    bus.queries.solve_status()
    entities = bus.document.entities
    rectangle = next(i for i, e in entities.items() if isinstance(e, Rectangle))
    line = next(i for i, e in entities.items() if isinstance(e, Line))

    def edit_rectangle(k: int) -> None:
        bus.execute(ModifyEntity(id=rectangle, changes={"width": 14.5 + k % 2}))

    def edit_line(k: int) -> None:
        current = bus.document.entities[line]
        assert isinstance(current, Line)
        moved = Point2(x=current.end.x + (1 if k % 2 else -1), y=current.end.y)
        bus.execute(ModifyEntity(id=line, changes={"end": moved}))

    for label, edit in (
        ("an unconstrained rectangle", edit_rectangle),
        ("a constrained line", edit_line),
    ):
        samples = []
        for k in range(runs):
            edit(k)
            samples.append(once(bus.queries.solve_status))
        rows.append((f"solve_status after editing {label}", median(samples)))

    box = bus.queries.bounding_box()
    assert isinstance(box, BoundingBox)
    clicks = [  # spread over the sketch, the same every run
        Point2(
            x=box.x_min + box.width * ((k * 37) % 100) / 100,
            y=box.y_min + box.height * ((k * 61) % 100) / 100,
        )
        for k in range(runs)
    ]
    queries = bus.queries
    queries.entity_at_point(clicks[0], 0.5)  # builds the grid
    for name in ("entity_at_point", "nearest_feature", "reference_at_point"):
        pick = getattr(queries, name)
        rows.append((f"{name}, grid built", median(once(partial(pick, c, 0.5)) for c in clicks)))

    firsts = []
    for k, click in enumerate(clicks):
        edit_rectangle(k)
        firsts.append(once(partial(bus.queries.entity_at_point, click, 0.5)))
    rows.append(("first entity_at_point after an edit (builds the grid)", median(firsts)))

    corner = bus.document.entities[rectangle]
    assert isinstance(corner, Rectangle)
    result = bus.execute(
        CreateLine(
            start=corner.corner,
            end=Point2(x=corner.corner.x + corner.width, y=corner.corner.y + 0.2),
        )
    )
    assert isinstance(result, Applied)
    (drawn,) = result.created_ids
    for label, ids in (("a constrained line", [line]), ("a new line on a rectangle", [drawn])):
        suggest = partial(bus.queries.suggest_constraints, ids, tolerance=0.5)
        rows.append((f"suggest_constraints for {label}", median(once(suggest) for _ in range(3))))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[2000, 10000])
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args(argv)
    for count in args.sizes:
        print(f"\n{count} entities + 2 constraints per line")
        for label, ms in bench(count, args.runs):
            print(f"  {label:<52} {ms:9.3f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
