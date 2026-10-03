"""Closed profiles from lines and arcs: the region a selection bounds (N4).

`find` turns a selection into loops, each in order (`Loop`), and checks that they make one
profile: one outer loop and holes directly inside it. A kernel is handed the loops
(`Kernel.make_face`); it never works out which edge joins which. `properties` works out a
profile's area properties exactly, by Green's theorem over lines and arcs, for the analytic
kernel; `bounds` its bounding box.

The rules, each refused with the reason (`profile.not_closed`, unless said otherwise):

- A Rectangle or Circle is a loop on its own. Lines and arcs join end to end, where their
  ends are within the joining tolerance (`tolerance.BROKEN` of the profile's size, the same
  margin within which the solver calls a relation held); each end must meet exactly one
  other, so a boundary neither stops short nor branches.
- A Point isn't part of a boundary.
- Boundaries don't cross, overlap, or touch, except where consecutive edges of a loop meet.
- One loop contains all the others, and each of them lies directly inside it: loops side by
  side are two profiles, and an island inside a hole is a second profile.
- A zero-length or non-finite edge is `geometry.degenerate`.

2D and analytic, as the engine's geometry is; only the B-rep kernel needs OCCT.
"""

import math
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from caliper.contracts.document import (
    Arc,
    Circle,
    EntityId,
    Geometry,
    Line,
    Point,
    Point2,
    Rectangle,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.kernel import Loop
from caliper.contracts.queries import AreaProperties, BoundingBox
from caliper.engine.constraints import tolerance

TAU = 2 * math.pi
_RAY = (math.cos(0.3217505544), math.sin(0.3217505544))
"""The direction inside/outside rays are cast in: not along an axis, so a ray through a
corner or along an edge of an axis-aligned sketch doesn't happen by construction."""


@dataclass(frozen=True, slots=True)
class Profile:
    outer: Loop
    holes: tuple[Loop, ...] = ()


@dataclass(frozen=True, slots=True)
class Edge:
    """One edge as a loop traverses it: a line from `a` to `b`, or an arc about `center`
    from angle `t0` turning by `sweep` radians (positive counter-clockwise; 0 for a line)."""

    a: Point2
    b: Point2
    center: Point2 | None = None
    radius: float = 0.0
    t0: float = 0.0
    sweep: float = 0.0
    source: EntityId | None = None


# --- Finding the loops ------------------------------------------------------------------


def find(selected: Sequence[tuple[EntityId, Geometry]]) -> Profile | Error:
    """The profile `selected` bounds, or why it isn't one."""
    chosen = sorted(selected, key=lambda pair: pair[0])  # repeated, an id is two boundaries
    if not chosen:
        return Error(
            code=ErrorCode.SELECTION_EMPTY, message="select one closed profile", field="ids"
        )
    loops: list[tuple[Loop, list[Edge]]] = []
    open_edges: list[tuple[EntityId, Line | Arc]] = []
    for id, entity in chosen:
        match entity:
            case Point():
                return _not_closed(
                    f"{id} is a point; a boundary is lines, arcs, circles, and rectangles", id
                )
            case Rectangle() | Circle():
                loop = Loop(edges=(entity,))
                if (problem := loop_problem(loop)) is not None:
                    return _with_ids(problem, id)
                loops.append((loop, traversed(loop, id)))
            case Line() | Arc():
                open_edges.append((id, entity))
    join = joining([g for _, g in chosen])
    for id, entity in open_edges:
        if (problem := _edge_problem(entity, join)) is not None:
            return _with_ids(problem, id)
    found = _chains(open_edges, join)
    if isinstance(found, Error):
        return found
    loops += found
    crossing = _crossing(loops, join)
    if crossing is not None:
        return crossing
    return _nested(loops)


def joining(geometry: Sequence[Geometry]) -> float:
    """How close two ends must be to join: `tolerance.BROKEN` of the geometry's size."""
    return tolerance.broken(v for g in geometry for v in _numbers(g))


def loop_problem(loop: Loop) -> Error | None:
    """Why `loop` can't bound a face, or None: what a kernel checks before building one."""
    edges = loop.edges
    if not edges:
        return _not_closed("a loop needs at least one edge")
    if loop.reversed and len(loop.reversed) != len(edges):
        return _not_closed("a loop needs one reversed flag per edge, or none")
    primitives = [e for e in edges if isinstance(e, Rectangle | Circle)]
    if primitives and len(edges) > 1:
        return _not_closed("a rectangle or circle is a loop on its own")
    if any(not isinstance(e, Line | Arc | Rectangle | Circle) for e in edges):
        return _not_closed("a loop is lines and arcs, or one rectangle or circle")
    join = joining(edges)
    for edge in edges:
        if (problem := _edge_problem(edge, join)) is not None:
            return problem
    if primitives:
        return None
    walked = traversed(loop)
    for here, after in zip(walked, [*walked[1:], walked[0]], strict=True):
        if _apart(here.b, after.a) > join:
            return _not_closed(
                f"the loop is open: one edge ends at ({_xy(here.b)}), the next starts at "
                f"({_xy(after.a)})"
            )
    return None


def traversed(loop: Loop, source: EntityId | None = None) -> list[Edge]:
    """`loop`'s edges as it traverses them. A rectangle is its four sides, counter-clockwise
    from its bottom-left corner; a circle is one full turn from angle 0."""
    walked: list[Edge] = []
    flags = loop.reversed or (False,) * len(loop.edges)
    for edge, backwards in zip(loop.edges, flags, strict=True):
        match edge:
            case Rectangle(corner=c, width=w, height=h):
                corners = [
                    c,
                    Point2(x=c.x + w, y=c.y),
                    Point2(x=c.x + w, y=c.y + h),
                    Point2(x=c.x, y=c.y + h),
                ]
                walked += [
                    Edge(a=p, b=q, source=source)
                    for p, q in zip(corners, [*corners[1:], corners[0]], strict=True)
                ]
            case Circle(center=c, radius=r):
                start = Point2(x=c.x + r, y=c.y)
                walked.append(
                    Edge(a=start, b=start, center=c, radius=r, t0=0.0, sweep=TAU, source=source)
                )
            case Line(start=a, end=b):
                walked.append(
                    Edge(a=b, b=a, source=source) if backwards else Edge(a=a, b=b, source=source)
                )
            case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
                t0, turn = math.radians(start), math.radians(sweep)
                first, last = _at(c, r, t0), _at(c, r, t0 + turn)
                if backwards:
                    walked.append(
                        Edge(
                            a=last,
                            b=first,
                            center=c,
                            radius=r,
                            t0=t0 + turn,
                            sweep=-turn,
                            source=source,
                        )
                    )
                else:
                    walked.append(
                        Edge(a=first, b=last, center=c, radius=r, t0=t0, sweep=turn, source=source)
                    )
    return walked


def _chains(
    open_edges: list[tuple[EntityId, Line | Arc]], join: float
) -> list[tuple[Loop, list[Edge]]] | Error:
    """Lines and arcs joined end to end into loops."""
    if not open_edges:
        return []
    ends: list[tuple[int, bool, Point2]] = []  # (edge, at its end?, where)
    for n, (_, entity) in enumerate(open_edges):
        start, end = _ends(entity)
        ends += [(n, False, start), (n, True, end)]
    joint = _cluster([p for _, _, p in ends], join)
    members: dict[int, list[int]] = defaultdict(list)
    for k, j in enumerate(joint):
        members[j].append(k)
    for _, found in sorted(members.items()):
        at = ends[found[0]][2]
        ids = tuple(dict.fromkeys(open_edges[ends[k][0]][0] for k in found))
        if len(found) == 1:
            n, is_end, _ = ends[found[0]]
            return _not_closed(
                f"the profile isn't closed: the {'end' if is_end else 'start'} of "
                f"{open_edges[n][0]}, at ({_xy(at)}), joins nothing",
                *ids,
            )
        if len(found) > 2:
            return _not_closed(
                f"{len(found)} edge ends meet at ({_xy(at)}) ({', '.join(ids)}): a profile's "
                "boundary can't branch",
                *ids,
            )
    other = {}
    for found in members.values():
        first, second = found
        other[first], other[second] = second, first
    loops: list[tuple[Loop, list[Edge]]] = []
    done: set[int] = set()
    for begin in range(len(open_edges)):
        if begin in done:
            continue
        edges: list[Geometry] = []
        flags: list[bool] = []
        walked: list[Edge] = []
        n, backwards = begin, False
        while n not in done:
            done.add(n)
            id, entity = open_edges[n]
            edges.append(entity)
            flags.append(backwards)
            walked += traversed(Loop(edges=(entity,), reversed=(backwards,)), id)
            leaving = 2 * n + (0 if backwards else 1)  # the end we leave by
            arriving = other[leaving]
            n, backwards = arriving // 2, arriving % 2 == 1  # entered at its end: backwards
        loops.append((Loop(edges=tuple(edges), reversed=tuple(flags)), walked))
    return loops


def _cluster(points: list[Point2], join: float) -> list[int]:
    """For each point, the index of the group of points within `join` of each other."""
    cell = max(join, 1e-300)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    group = list(range(len(points)))

    def root(k: int) -> int:
        while group[k] != k:
            group[k] = group[group[k]]
            k = group[k]
        return k

    for k, p in enumerate(points):
        gx, gy = math.floor(p.x / cell), math.floor(p.y / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in grid.get((gx + dx, gy + dy), ()):
                    if _apart(p, points[other]) <= join:
                        group[root(k)] = root(other)
        grid[(gx, gy)].append(k)
    roots = [root(k) for k in range(len(points))]
    numbered: dict[int, int] = {}
    return [numbered.setdefault(r, len(numbered)) for r in roots]


# --- Crossing and nesting ---------------------------------------------------------------


def _crossing(loops: list[tuple[Loop, list[Edge]]], join: float) -> Error | None:
    """The first place two boundaries cross or touch, other than where a loop's consecutive
    edges meet, or None."""
    edges = [(n, k, e) for n, (_, walked) in enumerate(loops) for k, e in enumerate(walked)]
    boxes = [_edge_box(e, join) for _, _, e in edges]
    order = sorted(range(len(edges)), key=lambda i: boxes[i][0])
    for position, i in enumerate(order):
        for j in order[position + 1 :]:
            if boxes[j][0] > boxes[i][2]:
                break
            if boxes[j][1] > boxes[i][3] or boxes[j][3] < boxes[i][1]:
                continue
            (loop_i, _, e), (loop_j, _, f) = edges[i], edges[j]
            shared = _shared_joints(e, f, join) if loop_i == loop_j else []
            met = _meet(e, f, join)
            if isinstance(met, str):
                return _not_closed(f"{_name(e)} and {_name(f)} overlap", *_ids(e, f))
            for p in met:
                if all(_apart(p, s) > 10 * join for s in shared):
                    return _not_closed(
                        f"{_name(e)} and {_name(f)} cross or touch at ({_xy(p)}): boundaries "
                        "may meet only end to end",
                        *_ids(e, f),
                    )
    return None


def _nested(loops: list[tuple[Loop, list[Edge]]]) -> Profile | Error:
    """One outer loop and the holes directly inside it."""
    points = [_sample(walked) for _, walked in loops]
    inside = [
        [j != i and _inside(points[j], walked) for j in range(len(loops))]
        for i, (_, walked) in enumerate(loops)
    ]
    count = len(loops)
    outers = [i for i in range(count) if all(inside[i][j] for j in range(count) if j != i)]
    if not outers:
        return _not_closed(
            "the selection is more than one profile: pick one outline and the holes inside it",
            *(e.source for _, walked in loops for e in walked[:1] if e.source is not None),
        )
    outer = outers[0]
    for hole in range(count):
        for other in range(count):
            if other not in (hole, outer) and inside[other][hole]:
                return _not_closed(
                    f"{_loop_name(loops[hole][1])} is inside a hole "
                    f"({_loop_name(loops[other][1])}): an island in a hole is a second profile",
                )
    return Profile(
        outer=loops[outer][0], holes=tuple(loop for n, (loop, _) in enumerate(loops) if n != outer)
    )


def _inside(p: Point2, walked: list[Edge]) -> bool:
    """Whether `p` is inside the loop, by counting where a ray from it crosses the edges."""
    vx, vy = _RAY
    crossings = 0
    for e in walked:
        if e.center is None:
            dx, dy = e.b.x - e.a.x, e.b.y - e.a.y
            denom = vx * dy - vy * dx
            if denom == 0:
                continue
            wx, wy = e.a.x - p.x, e.a.y - p.y
            s = (wx * dy - wy * dx) / denom  # along the ray
            t = (wx * vy - wy * vx) / denom  # along the edge
            if s > 0 and 0 <= t < 1:
                crossings += 1
        else:
            fx, fy = p.x - e.center.x, p.y - e.center.y
            half_b = vx * fx + vy * fy
            c = fx * fx + fy * fy - e.radius * e.radius
            disc = half_b * half_b - c
            if disc <= 0:
                continue
            root = math.sqrt(disc)
            for s in (-half_b - root, -half_b + root):
                if s > 0 and _on_arc(e, Point2(x=p.x + s * vx, y=p.y + s * vy), 0.0):
                    crossings += 1
    return crossings % 2 == 1


def _meet(e: Edge, f: Edge, join: float) -> list[Point2] | str:
    """Where two edges meet, or "overlap" if they share a stretch."""
    if e.center is None and f.center is None:
        return _line_line(e, f, join)
    if e.center is None:
        return _line_arc(e, f, join)
    if f.center is None:
        return _line_arc(f, e, join)
    return _arc_arc(e, f, join)


def _line_line(e: Edge, f: Edge, join: float) -> list[Point2] | str:
    ex, ey = e.b.x - e.a.x, e.b.y - e.a.y
    fx, fy = f.b.x - f.a.x, f.b.y - f.a.y
    length_e, length_f = math.hypot(ex, ey), math.hypot(fx, fy)
    denom = ex * fy - ey * fx
    wx, wy = f.a.x - e.a.x, f.a.y - e.a.y
    if abs(denom) <= join * max(length_e, length_f):  # parallel
        if abs(wx * ey - wy * ex) / length_e > join:
            return []
        # Collinear: where f's ends fall along e.
        t0 = (wx * ex + wy * ey) / length_e**2
        t1 = ((f.b.x - e.a.x) * ex + (f.b.y - e.a.y) * ey) / length_e**2
        low, high = max(0.0, min(t0, t1)), min(1.0, max(t0, t1))
        if (high - low) * length_e > join:
            return "overlap"
        if high - low >= -join / length_e:
            t = (low + high) / 2
            return [Point2(x=e.a.x + t * ex, y=e.a.y + t * ey)]
        return []
    t = (wx * fy - wy * fx) / denom
    u = (wx * ey - wy * ex) / denom
    slack_e, slack_f = join / length_e, join / length_f
    if -slack_e <= t <= 1 + slack_e and -slack_f <= u <= 1 + slack_f:
        return [Point2(x=e.a.x + t * ex, y=e.a.y + t * ey)]
    return []


def _line_arc(line: Edge, arc: Edge, join: float) -> list[Point2]:
    assert arc.center is not None
    dx, dy = line.b.x - line.a.x, line.b.y - line.a.y
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    fx, fy = line.a.x - arc.center.x, line.a.y - arc.center.y
    along = -(ux * fx + uy * fy)  # nearest point to the centre, along the line
    gap = abs(ux * fy - uy * fx)  # the centre's distance from the line
    r = arc.radius
    if gap > r + join:
        return []
    half = math.sqrt(max(0.0, r * r - gap * gap))
    found: list[Point2] = []
    for s in {along - half, along + half}:
        if -join <= s <= length + join:
            p = Point2(x=line.a.x + s * ux, y=line.a.y + s * uy)
            if _on_arc(arc, p, join):
                found.append(p)
    return found


def _arc_arc(e: Edge, f: Edge, join: float) -> list[Point2] | str:
    assert e.center is not None
    assert f.center is not None
    dx, dy = f.center.x - e.center.x, f.center.y - e.center.y
    d = math.hypot(dx, dy)
    r, s = e.radius, f.radius
    if d <= join and abs(r - s) <= join:  # one circle
        ends = [p for p in (f.a, f.b) if _on_arc(e, p, join)]
        ends += [p for p in (e.a, e.b) if _on_arc(f, p, join)]
        middles = [_at(f.center, s, f.t0 + f.sweep / 2), _at(e.center, r, e.t0 + e.sweep / 2)]
        if _on_arc(e, middles[0], join) or _on_arc(f, middles[1], join):
            return "overlap"
        return ends
    if d > r + s + join or d < abs(r - s) - join or d == 0:
        return []
    a = (d * d + r * r - s * s) / (2 * d)
    h = math.sqrt(max(0.0, r * r - a * a))
    mx, my = e.center.x + a * dx / d, e.center.y + a * dy / d
    candidates = {(mx + h * dy / d, my - h * dx / d), (mx - h * dy / d, my + h * dx / d)}
    return [
        p for x, y in candidates if _on_arc(e, p := Point2(x=x, y=y), join) and _on_arc(f, p, join)
    ]


def _on_arc(e: Edge, p: Point2, join: float) -> bool:
    """Whether `p`, on `e`'s circle, is within its sweep (to `join` along the circle)."""
    assert e.center is not None
    if abs(e.sweep) >= TAU:
        return True
    angle = math.atan2(p.y - e.center.y, p.x - e.center.x)
    turned = ((angle - e.t0) * (1 if e.sweep > 0 else -1)) % TAU
    slack = join / e.radius
    return turned <= abs(e.sweep) + slack or turned >= TAU - slack


def _shared_joints(e: Edge, f: Edge, join: float) -> list[Point2]:
    return [p for p in (e.a, e.b) if min(_apart(p, f.a), _apart(p, f.b)) <= join]


def _sample(walked: list[Edge]) -> Point2:
    """A point on the loop: the middle of its first edge."""
    e = walked[0]
    if e.center is None:
        return Point2(x=(e.a.x + e.b.x) / 2, y=(e.a.y + e.b.y) / 2)
    return _at(e.center, e.radius, e.t0 + e.sweep / 2)


# --- Regions against each other (the analytic kernel's solids) -----------------------------


def within(loop: Loop, region: Profile) -> bool:
    """Whether the region inside `loop` lies wholly in `region`: inside its outer loop, clear of
    every hole, with no boundaries crossing or touching. Exact, as `find` is."""
    walked = traversed(loop)
    loops = [
        (region.outer, traversed(region.outer)),
        *((hole, traversed(hole)) for hole in region.holes),
        (loop, walked),
    ]
    join = joining([e for each, _ in loops for e in each.edges])
    if _crossing(loops, join) is not None:
        return False
    point = _sample(walked)
    if not _inside(point, loops[0][1]):
        return False
    return not any(
        _inside(point, hole) or _inside(_sample(hole), walked) for _, hole in loops[1:-1]
    )


def polygon(loop: Loop, tolerance: float) -> list[Point2]:
    """The loop's corners in the order it runs, each arc replaced by chords no further than
    `tolerance` from it. For drawing (meshes), never for measuring."""
    corners: list[Point2] = []
    for e in traversed(loop):
        if e.center is None:
            corners.append(e.a)
            continue
        ratio = max(-1.0, min(1.0, 1.0 - tolerance / e.radius))
        step = max(2 * math.acos(ratio), 1e-3)
        count = max(3 if abs(e.sweep) >= TAU else 1, math.ceil(abs(e.sweep) / step))
        corners += [_at(e.center, e.radius, e.t0 + e.sweep * k / count) for k in range(count)]
    return corners


# --- Area properties and bounds ----------------------------------------------------------


def properties(profile: Profile) -> AreaProperties:
    """The region's area, centroid, and second moments about the centroid, exactly."""
    if not profile.holes and len(profile.outer.edges) == 1:
        match profile.outer.edges[0]:  # the textbook forms, to the last bit
            case Rectangle(corner=corner, width=w, height=h):
                return AreaProperties(
                    area=w * h,
                    centroid=Point2(x=corner.x + w / 2, y=corner.y + h / 2),
                    ixx=w * h**3 / 12,
                    iyy=h * w**3 / 12,
                    ixy=0.0,
                )
            case Circle(center=center, radius=r):
                polar_half = math.pi * r**4 / 4
                return AreaProperties(
                    area=math.pi * r**2, centroid=center, ixx=polar_half, iyy=polar_half, ixy=0.0
                )
    walked = traversed(profile.outer)
    origin = walked[0].a  # moments are summed about a point of the profile, for precision
    total = _moments(walked, origin)
    for hole in profile.holes:
        total = [t - h for t, h in zip(total, _moments(traversed(hole), origin), strict=True)]
    area, sx, sy, ixx0, iyy0, ixy0 = total
    x, y = sx / area, sy / area
    return AreaProperties(
        area=area,
        centroid=Point2(x=origin.x + x, y=origin.y + y),
        ixx=ixx0 - area * y * y,
        iyy=iyy0 - area * x * x,
        ixy=ixy0 - area * x * y,
    )


def extent(loop: Loop, a: float, b: float) -> tuple[float, float]:
    """The least and greatest of a·x + b·y over `loop`: along any direction, where `bounds`
    is along the axes. Exact: at the edges' ends, and on an arc where it turns back."""
    found: list[float] = []
    for e in traversed(loop):
        found += [a * e.a.x + b * e.a.y, a * e.b.x + b * e.b.y]
        if e.center is not None and (a or b):
            turn = math.atan2(b, a)  # where the arc runs square to the direction
            for angle in (turn, turn + math.pi):
                p = _at(e.center, e.radius, angle)
                if _on_arc(e, p, 0.0):
                    found.append(a * p.x + b * p.y)
    return min(found), max(found)


def bounds(loop: Loop) -> BoundingBox:
    boxes = [_edge_box(e, 0.0) for e in traversed(loop)]
    return BoundingBox(
        x_min=min(b[0] for b in boxes),
        y_min=min(b[1] for b in boxes),
        x_max=max(b[2] for b in boxes),
        y_max=max(b[3] for b in boxes),
    )


def _moments(walked: list[Edge], origin: Point2) -> list[float]:
    """∫∫ of 1, x, y, y², x², xy over the loop's region, about `origin`, counter-clockwise
    whichever way the loop runs: Green's theorem, edge by edge."""
    total = [0.0] * 6
    for e in walked:
        parts = _line_moments(e, origin) if e.center is None else _arc_moments(e, origin)
        total = [t + p for t, p in zip(total, parts, strict=True)]
    if total[0] < 0:
        total = [-t for t in total]
    return total


def _line_moments(e: Edge, origin: Point2) -> list[float]:
    x0, y0 = e.a.x - origin.x, e.a.y - origin.y
    a, b = e.b.x - e.a.x, e.b.y - e.a.y
    return [
        b * (x0 + a / 2),  # ∮ x dy
        b / 2 * (x0 * x0 + x0 * a + a * a / 3),  # ∮ x²/2 dy
        -a / 2 * (y0 * y0 + y0 * b + b * b / 3),  # ∮ -y²/2 dx
        -a / 3 * (y0**3 + 1.5 * y0 * y0 * b + y0 * b * b + b**3 / 4),  # ∮ -y³/3 dx
        b / 3 * (x0**3 + 1.5 * x0 * x0 * a + x0 * a * a + a**3 / 4),  # ∮ x³/3 dy
        b
        / 2
        * (
            x0 * x0 * y0
            + (x0 * x0 * b + 2 * x0 * a * y0) / 2
            + (2 * x0 * a * b + a * a * y0) / 3
            + a * a * b / 4
        ),  # ∮ x²y/2 dy
    ]


def _arc_moments(e: Edge, origin: Point2) -> list[float]:
    assert e.center is not None
    cx, cy, r = e.center.x - origin.x, e.center.y - origin.y, e.radius

    def over(antiderivative: Callable[[float], float]) -> float:
        return antiderivative(e.t0 + e.sweep) - antiderivative(e.t0)

    sin, cos = math.sin, math.cos
    c1 = over(sin)  # ∫cos
    s1 = over(lambda t: -cos(t))  # ∫sin
    c2 = over(lambda t: t / 2 + sin(2 * t) / 4)  # ∫cos²
    s2 = over(lambda t: t / 2 - sin(2 * t) / 4)  # ∫sin²
    c3 = over(lambda t: sin(t) - sin(t) ** 3 / 3)  # ∫cos³
    s3 = over(lambda t: -cos(t) + cos(t) ** 3 / 3)  # ∫sin³
    c4 = over(lambda t: 3 * t / 8 + sin(2 * t) / 4 + sin(4 * t) / 32)  # ∫cos⁴
    s4 = over(lambda t: 3 * t / 8 - sin(2 * t) / 4 + sin(4 * t) / 32)  # ∫sin⁴
    sc = over(lambda t: sin(t) ** 2 / 2)  # ∫sin cos
    sc2 = over(lambda t: -(cos(t) ** 3) / 3)  # ∫sin cos²
    sc3 = over(lambda t: -(cos(t) ** 4) / 4)  # ∫sin cos³
    return [
        r * cx * c1 + r * r * c2,
        r / 2 * (cx * cx * c1 + 2 * cx * r * c2 + r * r * c3),
        r / 2 * (cy * cy * s1 + 2 * cy * r * s2 + r * r * s3),
        r / 3 * (cy**3 * s1 + 3 * cy * cy * r * s2 + 3 * cy * r * r * s3 + r**3 * s4),
        r / 3 * (cx**3 * c1 + 3 * cx * cx * r * c2 + 3 * cx * r * r * c3 + r**3 * c4),
        r
        / 2
        * (
            cx * cx * cy * c1
            + cx * cx * r * sc
            + 2 * cx * cy * r * c2
            + 2 * cx * r * r * sc2
            + cy * r * r * c3
            + r**3 * sc3
        ),
    ]


def _edge_box(e: Edge, margin: float) -> tuple[float, float, float, float]:
    xs, ys = [e.a.x, e.b.x], [e.a.y, e.b.y]
    if e.center is not None:
        for quarter in range(4):
            angle = quarter * math.pi / 2
            p = _at(e.center, e.radius, angle)
            if _on_arc(e, p, 0.0):
                xs.append(p.x)
                ys.append(p.y)
    return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin


# --- Helpers ----------------------------------------------------------------------------


def _edge_problem(entity: Geometry, join: float) -> Error | None:
    numbers = list(_numbers(entity))
    if not all(math.isfinite(n) for n in numbers):
        return _degenerate(f"a {entity.kind} with a value that isn't finite")
    match entity:
        case Line(start=a, end=b) if _apart(a, b) <= join:
            return _degenerate("a line of zero length")
        case Arc(radius=r, sweep_angle=sweep) if r * math.radians(sweep) <= join or r <= 0:
            return _degenerate("an arc of zero length")
        case Circle(radius=r) if r <= 0:
            return _degenerate("a circle of zero radius")
        case Rectangle(width=w, height=h) if w <= 0 or h <= 0:
            return _degenerate("a rectangle of zero size")
    return None


def _numbers(entity: Geometry) -> Iterator[float]:
    match entity:
        case Point(position=p):
            yield from (p.x, p.y)
        case Line(start=a, end=b):
            yield from (a.x, a.y, b.x, b.y)
        case Circle(center=c, radius=r):
            yield from (c.x, c.y, r)
        case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
            yield from (c.x, c.y, r, start, sweep)
        case Rectangle(corner=c, width=w, height=h):
            yield from (c.x, c.y, w, h)


def _ends(entity: Line | Arc) -> tuple[Point2, Point2]:
    match entity:
        case Line(start=a, end=b):
            return a, b
        case Arc(center=c, radius=r, start_angle=start, sweep_angle=sweep):
            t0 = math.radians(start)
            return _at(c, r, t0), _at(c, r, t0 + math.radians(sweep))


def _at(center: Point2, radius: float, angle: float) -> Point2:
    return Point2(x=center.x + radius * math.cos(angle), y=center.y + radius * math.sin(angle))


def _apart(p: Point2, q: Point2) -> float:
    return math.hypot(p.x - q.x, p.y - q.y)


def _name(e: Edge) -> str:
    return str(e.source) if e.source is not None else "an edge"


def _ids(*edges: Edge) -> tuple[EntityId, ...]:
    return tuple(dict.fromkeys(e.source for e in edges if e.source is not None))


def _loop_name(walked: list[Edge]) -> str:
    return ", ".join(dict.fromkeys(str(e.source) for e in walked if e.source is not None))


def _xy(p: Point2) -> str:
    return f"{p.x:g}, {p.y:g}"


def _not_closed(message: str, *ids: EntityId) -> Error:
    return Error(code=ErrorCode.PROFILE_NOT_CLOSED, message=message, field="ids", ids=ids)


def _degenerate(message: str) -> Error:
    return Error(code=ErrorCode.GEOMETRY_DEGENERATE, message=message, field="ids")


def _with_ids(error: Error, *ids: EntityId) -> Error:
    return Error(
        code=error.code, message=f"{', '.join(ids)}: {error.message}", field="ids", ids=ids
    )
