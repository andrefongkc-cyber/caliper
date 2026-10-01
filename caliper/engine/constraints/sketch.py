"""Solving a document: clusters, staged solves, conflicts, redundancy, and status.

Constraints and driving dimensions ("relations") connect geometry into clusters that solve
independently. Geometry that Fix constraints pin completely ("anchored": a fixed origin point,
say) can't move in any solve, so it doesn't join what's related to it: a hole and a slot both
dimensioned from a fixed origin are separate clusters, each holding the origin as a constant.
Without that, a sketch dimensioned from its origin is one cluster, and every command solves
all of it. A command asks `settle` to re-solve the clusters it touched. The solve runs in
stages that each let a little more move, and the first stage that satisfies everything
wins:

1. Nothing moves (the relations may already hold).
2. For a new constraint or dimension value, the "mover": the reference a constraint
   moves, or a dimension's `b` side; then that reference's whole entity. For an edit or a
   move, first the movers of the constraints on the edited geometry (a mirror point
   follows its twin, the axis stays), then everything except the edited geometry.
3. Everything in the cluster, except fields the command set explicitly ("held").

Within a stage, Newton steps take the smallest change that satisfies the equations, so
unrelated geometry stays put. When no stage works, the constraints conflict: each relation
in the cluster (and the command's own edit) is dropped in turn, and the ones whose removal
lets the rest solve are reported.

Every solve starts from the stored geometry, the last solution with the command's own change
applied, and a stored value the solve changed by no more than `tolerance.UNCHANGED` stays as
stored when every relation holds without the change (`_kept`), so solving again changes
nothing and unchanged geometry never drifts. What "holds", "unchanged", and "the same
solution" mean is `tolerance`. `reference()` solves the way Caliper did before clusters split
and values were kept: the oracle `equivalence` compares this solver with.
"""

import math
import sys
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from itertools import permutations
from types import MappingProxyType

from caliper.contracts.document import (
    POINT_FEATURES,
    AngleDimension,
    Arc,
    Circle,
    Constraint,
    ConstraintType,
    DistanceDimension,
    Document,
    Entity,
    EntityId,
    Feature,
    Geometry,
    Line,
    Point,
    RadialDimension,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import ConstraintState, SolveStatus
from caliper.engine.constraints import tolerance
from caliper.engine.constraints.ad import Dual, hypot
from caliper.engine.constraints.linalg import RowBasis
from caliper.engine.constraints.model import PARAMS, Frame, read, write
from caliper.engine.constraints.relations import (
    Match,
    Setting,
    match,
    measure,
    measure_angle,
    tangent_at_joint,
)
from caliper.engine.document.recent import Recent

type Param = tuple[EntityId, str]
type Relation = Constraint | DistanceDimension | RadialDimension | AngleDimension

GEOMETRY = (Point, *[t for t in PARAMS if t is not Point])

ITERATIONS = 64
POLISH = 3
"""Extra Newton steps once within tolerance, while they still reduce the error. They take
results to the last bit, so a width driven to 120 is stored as exactly 120.0."""
RESHAPE_COST = 100.0
"""How much more a unit change of a size (radius, width, height) costs than a unit move.

Steps minimize the weighted change, so a free circle or rectangle translates to meet a
constraint rather than shrinking or growing to reach it, as in any CAD sketcher."""
_SIZES = frozenset({"radius", "width", "height"})


# --- What refers to what ------------------------------------------------------------------


def references(entity: Entity) -> tuple[Ref, ...]:
    """The features a constraint or dimension refers to. Geometry refers to nothing."""
    match entity:
        case Constraint(refs=refs):
            return refs
        case DistanceDimension(a=a, b=b) | AngleDimension(a=a, b=b):
            return (a, b)
        case RadialDimension(target=target):
            return (Ref(entity=target, feature=Feature.CURVE),)
    return ()


_REFERRERS: Recent[Mapping[EntityId, frozenset[EntityId]]] = Recent(4)
"""`referrers` for recent documents."""


def referrers(document: Document) -> Mapping[EntityId, frozenset[EntityId]]:
    """The constraints and dimensions that refer to each entity: what `references` says,
    turned around. Worked out from the last document asked about, by identity: only the
    entities a change added, removed, or replaced are looked at again."""
    found = _REFERRERS.get(document)
    if found is not None:
        return found
    latest = _REFERRERS.latest()
    if latest is None:
        found = _indexed(document)
    else:
        base, index = latest
        changed = _changed(base, document)
        if len(changed) <= len(document.entities) // 2:
            found = _reindexed(index, base, document, changed)
        else:
            found = _indexed(document)
    _REFERRERS.put(document, found)
    return found


def _indexed(document: Document) -> dict[EntityId, frozenset[EntityId]]:
    found: dict[EntityId, set[EntityId]] = {}
    for id, entity in document.entities.items():
        for ref in references(entity):
            found.setdefault(ref.entity, set()).add(id)
    return {target: frozenset(ids) for target, ids in found.items()}


def _reindexed(
    index: Mapping[EntityId, frozenset[EntityId]],
    before: Document,
    after: Document,
    changed: set[EntityId],
) -> dict[EntityId, frozenset[EntityId]]:
    updated = dict(index)
    for id in changed:
        old, new = before.entities.get(id), after.entities.get(id)
        was = {r.entity for r in references(old)} if old is not None else set()
        now = {r.entity for r in references(new)} if new is not None else set()
        for target in was - now:
            left = updated[target] - {id}
            if left:
                updated[target] = left
            else:
                del updated[target]
        for target in now - was:
            updated[target] = updated.get(target, frozenset()) | {id}
    return updated


def is_relation(entity: Entity) -> bool:
    """Constraints, and dimensions with a value: the things the solver must satisfy."""
    if isinstance(entity, Constraint):
        return True
    return isinstance(entity, DistanceDimension | RadialDimension | AngleDimension) and (
        entity.value is not None
    )


@dataclass(frozen=True, slots=True)
class Cluster:
    geometry: tuple[EntityId, ...]
    relations: tuple[EntityId, ...]
    fixed: tuple[EntityId, ...] = ()
    """Anchored geometry the relations refer to without it being a member: constants here,
    solved (and counted) in its own cluster, with its Fix constraints."""


_DIRECT: Mapping[tuple[type[Geometry], Feature], str] = {
    (Point, Feature.POINT): "position",
    (Line, Feature.START): "start",
    (Line, Feature.END): "end",
    (Circle, Feature.CENTER): "center",
    (Arc, Feature.CENTER): "center",
    (Rectangle, Feature.BOTTOM_LEFT): "corner",
}
"""Point features that are two of their entity's own parameters, so fixing one pins those."""


def anchored(document: Document) -> frozenset[EntityId]:
    """Geometry whose every parameter a Fix constraint pins: it can't move in any solve."""
    pinned: dict[EntityId, set[str]] = {}
    sizes: dict[EntityId, int] = {}
    for entity in document.entities.values():
        if not (isinstance(entity, Constraint) and entity.type is ConstraintType.FIX):
            continue
        for ref in entity.refs:
            target = document.entities.get(ref.entity)
            if not isinstance(target, GEOMETRY):
                continue
            paths = PARAMS[type(target)]
            sizes[ref.entity] = len(paths)
            if ref.feature is Feature.CURVE:
                pinned.setdefault(ref.entity, set()).update(paths)
            elif (prefix := _DIRECT.get((type(target), ref.feature))) is not None:
                pinned.setdefault(ref.entity, set()).update(
                    path for path in paths if path.split(".")[0] == prefix
                )
    return frozenset(id for id, paths in pinned.items() if len(paths) == sizes[id])


_REFERENCE: ContextVar[bool] = ContextVar("caliper_solver_reference", default=False)


@contextmanager
def reference() -> Iterator[None]:
    """Solve as Caliper did before Performance V2, for tests and diagnostics: every cluster
    whole (anchored geometry joins like any other geometry), every value written as solved,
    and nothing cached or reused. It writes exactly the files the old solver wrote, which
    makes it the oracle the optimized solver is compared with (`equivalence`). Only the
    current thread (or context) solves this way while it's open."""
    token = _REFERENCE.set(True)
    try:
        yield
    finally:
        _REFERENCE.reset(token)


def clusters(document: Document) -> list[Cluster]:
    """Geometry joined by relations, each group with its relations. Lone geometry is omitted.

    Anchored geometry joins only relations that refer to nothing else, such as its own Fix;
    elsewhere it's one of the cluster's `fixed` constants. (Under `reference`, it joins like
    any other geometry.)"""
    fixed = frozenset() if _REFERENCE.get() else anchored(document)
    return _clusters_of(document, _relation_ids(document), fixed)


def _relation_ids(document: Document) -> list[EntityId]:
    return [id for id in sorted(document.entities) if is_relation(document.entities[id])]


def _whole(document: Document) -> dict[EntityId, Cluster]:
    """Every clustered entity's cluster when anchored geometry joins like any other: the
    clusters as they were before anchoring split them. For the rare paths (a command that
    fails, or repeats a constraint) whose answer and message must stay exactly the same."""
    return _by_member(_clusters_of(document, _relation_ids(document), frozenset()))


def _clusters_of(
    document: Document, relations: Iterable[EntityId], fixed: frozenset[EntityId]
) -> list[Cluster]:
    """The clusters `relations` (sorted ids) join their geometry into, in `clusters` order:
    by each one's first geometry id, geometry and relations each sorted. A relation joins only
    its targets outside `fixed`, unless every target is in it."""
    parent: dict[EntityId, EntityId] = {}

    def root(e: EntityId) -> EntityId:
        while parent.setdefault(e, e) != e:
            parent[e] = parent[parent[e]]
            e = parent[e]
        return e

    owners: dict[EntityId, EntityId] = {}
    constants: dict[EntityId, list[EntityId]] = {}
    for id in relations:
        targets = sorted({r.entity for r in references(document.entities[id])})
        joined = [t for t in targets if t not in fixed] or targets
        first = root(joined[0])  # registers it, even when the relation has one entity
        for other in joined[1:]:
            parent[root(other)] = first
        owners[id] = joined[0]
        if len(joined) < len(targets):
            constants[id] = [t for t in targets if t in fixed]
    groups: dict[EntityId, tuple[list[EntityId], list[EntityId], set[EntityId]]] = {}
    for e in sorted(parent):
        groups.setdefault(root(e), ([], [], set()))[0].append(e)
    for id, owner in owners.items():
        group = groups[root(owner)]
        group[1].append(id)
        group[2].update(constants.get(id, ()))
    return [Cluster(tuple(g), tuple(r), tuple(sorted(f))) for g, r, f in groups.values()]


_GROUPS: Recent[tuple[Mapping[EntityId, Cluster], frozenset[EntityId]]] = Recent(4)
"""`grouped` for recent documents, with their anchored geometry."""


def grouped(document: Document) -> Mapping[EntityId, Cluster]:
    """The cluster of every clustered entity, geometry and relations alike, as `clusters`
    finds them.

    Worked out from the last document asked about. Documents share the entity objects a change
    left alone, so they are compared by identity: when no relation changed, every cluster keeps
    its members; otherwise only the clusters a changed relation left or reaches are grouped
    again. A changed Fix changes what is anchored, so everything is grouped again then.
    """
    return _grouping(document)[0]


def _grouping(document: Document) -> tuple[Mapping[EntityId, Cluster], frozenset[EntityId]]:
    if _REFERENCE.get():
        return _whole(document), frozenset()
    cached = _GROUPS.get(document)
    if cached is not None:
        return cached
    found: Mapping[EntityId, Cluster]
    latest = _GROUPS.latest()
    if latest is None:
        fixed = anchored(document)
        found = _by_member(_clusters_of(document, _relation_ids(document), fixed))
    else:
        base, (where, fixed) = latest
        changed = _changed(base, document)
        if not _any_relation(changed, base, document):
            found = where
        elif _any_fix(changed, base, document) or len(changed) > len(document.entities) // 2:
            fixed = anchored(document)
            found = _by_member(_clusters_of(document, _relation_ids(document), fixed))
        else:
            found = _regrouped(where, base, document, changed, fixed)
    _GROUPS.put(document, (found, fixed))
    return found, fixed


def _by_member(found: Iterable[Cluster]) -> dict[EntityId, Cluster]:
    return {id: cluster for cluster in found for id in (*cluster.geometry, *cluster.relations)}


def _regrouped(
    where: Mapping[EntityId, Cluster],
    before: Document,
    after: Document,
    changed: set[EntityId],
    fixed: frozenset[EntityId],
) -> dict[EntityId, Cluster]:
    """`where`, from `before`, for `after`: the clusters that held a changed entity, or that a
    changed relation refers into, are grouped again from their relations; the rest stay."""
    reached = {member for member in changed if member in where}
    for member in changed:
        for entity in (before.entities.get(member), after.entities.get(member)):
            if entity is not None and is_relation(entity):
                reached.update(r.entity for r in references(entity))
    stale = {id(where[e]): where[e] for e in reached if e in where}.values()
    relations = {r for cluster in stale for r in cluster.relations} | changed
    kept = sorted(
        r
        for r in relations
        if (entity := after.entities.get(r)) is not None and is_relation(entity)
    )
    regrouped = dict(where)
    for cluster in stale:
        for member in (*cluster.geometry, *cluster.relations):
            del regrouped[member]
    regrouped.update(_by_member(_clusters_of(after, kept, fixed)))
    return regrouped


# --- A cluster as a system of equations ---------------------------------------------------


@dataclass
class System:
    document: Document
    """The candidate document the cluster comes from."""
    kinds: dict[EntityId, type[Geometry]]
    slots: dict[EntityId, list[int]]
    params: list[Param]
    values: list[float]
    anchors: list[float]
    """Values before the command, where Fix holds things."""
    relations: list[EntityId] = field(default_factory=list)
    constant: frozenset[int] = frozenset()
    """Parameters of the cluster's anchored `fixed` geometry: read, never solved for."""
    compiled: list[tuple[EntityId, Callable[[Frame], list[Dual]]]] | None = field(
        default=None, repr=False, compare=False
    )
    """`stored_equations`, once worked out."""

    @classmethod
    def build(
        cls, document: Document, cluster: Cluster, before: Document | None = None
    ) -> "System":
        kinds: dict[EntityId, type[Geometry]] = {}
        slots: dict[EntityId, list[int]] = {}
        params: list[Param] = []
        values: list[float] = []
        anchors: list[float] = []
        constant: set[int] = set()
        for id in (*cluster.geometry, *cluster.fixed):
            entity = document.entities[id]
            assert isinstance(entity, GEOMETRY)
            kinds[id] = type(entity)
            old = before.entities.get(id) if before is not None else None
            if type(old) is not type(entity):
                old = entity
            slots[id] = []
            for path in PARAMS[type(entity)]:
                if id in cluster.fixed:
                    constant.add(len(params))
                slots[id].append(len(params))
                params.append((id, path))
                values.append(read(entity, path))
                anchors.append(read(old, path))
        return cls(
            document,
            kinds,
            slots,
            params,
            values,
            anchors,
            list(cluster.relations),
            frozenset(constant),
        )

    @property
    def unknowns(self) -> list[int]:
        """Every parameter a solve may change: all but the constants."""
        return [i for i in range(len(self.params)) if i not in self.constant]

    def frame(self, values: Sequence[float], variables: Iterable[int] = ()) -> Frame:
        return Frame(self.kinds, self.slots, values, frozenset(variables))

    def equations(
        self, relations: Sequence[EntityId], first: Sequence[float]
    ) -> list[tuple[EntityId, Callable[[Frame], list[Dual]]]]:
        setting = Setting(first=self.frame(first), anchor=self.frame(self.anchors))
        joints = _joints(self.document, self.relations)
        return [(id, _compile(self.document, id, setting, joints)) for id in relations]

    def stored_equations(self) -> list[tuple[EntityId, Callable[[Frame], list[Dual]]]]:
        """Every relation's equations, with signs and branches set by the stored values:
        compiled once per system, for each stage's Newton attempt and for `_kept`."""
        if self.compiled is None:
            self.compiled = self.equations(self.relations, self.values)
        return self.compiled

    def indices(self, params: Iterable[Param]) -> set[int]:
        where = {p: i for i, p in enumerate(self.params)}
        return {where[p] for p in params if p in where}

    def depends(self, ref: Ref) -> set[int]:
        """Unknowns a reference depends on."""
        frame = self.frame(self.values, range(len(self.values)))
        quantities = (
            frame.point(ref)
            if ref.feature in POINT_FEATURES[self.kinds[ref.entity]]
            else frame.defining(ref)
        )
        return {i for q in quantities for i in q.g}


type Joints = Mapping[tuple[EntityId, EntityId, Feature], Ref]


_CORNER_SIDES = {
    Feature.BOTTOM_LEFT: (Feature.BOTTOM, Feature.LEFT),
    Feature.BOTTOM_RIGHT: (Feature.BOTTOM, Feature.RIGHT),
    Feature.TOP_RIGHT: (Feature.TOP, Feature.RIGHT),
    Feature.TOP_LEFT: (Feature.TOP, Feature.LEFT),
}
"""The two sides of a rectangle that meet at each corner."""


def _joints(document: Document, relations: Iterable[EntityId]) -> Joints:
    """Where an arc's end lies on another curve: that end, by (arc, other curve's entity, its
    curve feature). The end may be joined to the curve directly (at the curve's end, a
    rectangle's corner, or anywhere along it) or through other points joined to it and each
    other. Tangency between the two is written at the joint (`tangent_at_joint`)."""
    coincident = [
        entity.refs
        for id in relations
        if isinstance(entity := document.entities[id], Constraint)
        and entity.type is ConstraintType.COINCIDENT
    ]
    # Points joined point to point, transitively: each point's group.
    group: dict[Ref, set[Ref]] = {}
    for refs in coincident:
        if all(r.feature not in _CURVES for r in refs):
            merged = group.get(refs[0], {refs[0]}) | group.get(refs[1], {refs[1]})
            for ref in merged:
                group[ref] = merged
    on_curve: dict[Ref, list[Ref]] = {}  # a point: the curves it's put on
    for refs in coincident:
        for curve, point in permutations(refs):
            if curve.feature in _CURVES and point.feature not in _CURVES:
                on_curve.setdefault(point, []).append(curve)
    joints: dict[tuple[EntityId, EntityId, Feature], Ref] = {}
    for end in {r for refs in coincident for r in refs}:
        if not (
            isinstance(document.entities[end.entity], Arc)
            and end.feature in (Feature.START, Feature.END)
        ):
            continue
        for point in group.get(end, {end}):
            others: list[tuple[EntityId, Feature]] = [
                (curve.entity, curve.feature) for curve in on_curve.get(point, [])
            ]
            target = document.entities[point.entity]
            if isinstance(target, Line | Arc) and point.feature in _ON_CURVE:
                others.append((point.entity, Feature.CURVE))
            elif isinstance(target, Rectangle) and point.feature in _CORNER_SIDES:
                others.extend((point.entity, side) for side in _CORNER_SIDES[point.feature])
            for other, feature in others:
                if other != end.entity:
                    joints.setdefault((end.entity, other, feature), end)
    return joints


_CURVES = frozenset({Feature.CURVE, Feature.BOTTOM, Feature.RIGHT, Feature.TOP, Feature.LEFT})
_ON_CURVE = frozenset({Feature.START, Feature.END, Feature.MID})
"""A line's or arc's point features that lie on the curve itself."""


def _compile(
    document: Document, id: EntityId, at: Setting, joints: Joints
) -> Callable[[Frame], list[Dual]]:
    entity = document.entities[id]
    match entity:
        case Constraint(type=type_, refs=refs):
            found = match(document, type_, refs)
            assert isinstance(found, Match), found
            equations, ordered = found.rule.equations, found.refs
            if type_ is ConstraintType.TANGENT:
                a, b = ordered[0], ordered[1]
                joint = joints.get((a.entity, b.entity, b.feature)) or joints.get(
                    (b.entity, a.entity, a.feature)
                )
                if joint is not None:
                    equations = tangent_at_joint(joint)
            assert equations is not None
            return lambda f: equations(f, ordered, at)
        case DistanceDimension(value=float(target)) | RadialDimension(value=float(target)):
            dimension = entity
            return lambda f: [measure(f, document, dimension, at) - target]
        case AngleDimension(a=a, b=b, supplementary=supplementary, value=float(target)):
            return lambda f: [measure_angle(f, a, b, supplementary, at) - target]
    raise ValueError(f"{id} is not a relation")


def _evaluate(
    equations: Sequence[tuple[EntityId, Callable[[Frame], list[Dual]]]], frame: Frame
) -> tuple[list[float], list[dict[int, float]], list[EntityId]]:
    residuals: list[float] = []
    gradients: list[dict[int, float]] = []
    owners: list[EntityId] = []
    for id, equation in equations:
        for d in equation(frame):
            residuals.append(d.v)
            gradients.append(d.g)
            owners.append(id)
    return residuals, gradients, owners


def _dense(gradients: Sequence[dict[int, float]], columns: Mapping[int, int]) -> list[list[float]]:
    rows = []
    for g in gradients:
        row = [0.0] * len(columns)
        for i, d in g.items():
            if i in columns:
                row[columns[i]] = d
        rows.append(row)
    return rows


def _newton(
    system: System,
    relations: Sequence[EntityId],
    free: Sequence[int],
    *,
    nudge: bool = False,
    hold_lengths: Iterable[EntityId] = (),
    patient: bool = True,
) -> tuple[list[float], bool]:
    """Minimum-norm Newton with backtracking on the free unknowns. True when solved.

    `nudge` starts from slightly off the stored values. A line exactly perpendicular to where
    a constraint wants it sits on a saddle, where no small change helps; a deterministic
    nudge of a thousandth of the sketch's size gives Newton a slope to follow.

    `hold_lengths` adds, for this solve only, equations keeping those lines as long as they
    were: they come last, so they only decide what the real constraints leave open.

    Not `patient`: a stage that isn't solved and whose best step lowers the squared residuals
    by less than the tolerance squared has stalled, and fails there. Such a step moves no
    residual by an amount the convergence test can tell apart, so no number of them solves
    the stage: typically the unknowns it may move can't satisfy the relations, and all that's
    left to lower is round-off, or residuals `_kept` left within tolerance elsewhere. (The
    reference is patient, as the solver always was: it tries every iteration.)
    """
    held_lines = [id for id in hold_lengths if system.kinds.get(id) is Line]
    compiled = (
        system.stored_equations()
        if list(relations) == system.relations
        else system.equations(relations, list(system.values))
    )
    equations = compiled + [(id, _length_keeper(system, id)) for id in held_lines]
    x = list(system.values)
    if nudge:
        size = tolerance.NUDGE * tolerance.scale(x)
        for n, i in enumerate(free):
            x[i] += size * ((n * 7919) % 13 - 6) / 6  # a fixed spread in [-1, 1]
        if not _in_domain(system, x):
            x = list(system.values)
    columns = {p: c for c, p in enumerate(free)}
    allowed = tolerance.solved(x)
    # Weighted minimum norm: solve for u = W Δx with J W⁻¹ u = -r, then Δx = W⁻¹ u.
    costs = [RESHAPE_COST if system.params[p][1] in _SIZES else 1.0 for p in free]

    def attempt(values: list[float]) -> tuple[list[float], list[dict[int, float]], float]:
        if not _in_domain(system, values):
            return [], [], math.inf  # never step through a negative radius or a full turn
        try:
            r, g, _ = _evaluate(equations, system.frame(values, free))
        except (ZeroDivisionError, ValueError, OverflowError):
            return [], [], math.inf
        total = sum(v * v for v in r)
        return r, g, total if math.isfinite(total) else math.inf

    r, g, norm = attempt(x)
    if not math.isfinite(norm):
        return x, False
    polished = 0
    for _ in range(ITERATIONS):
        if max((abs(v) for v in r), default=0.0) <= allowed:
            if norm == 0.0 or polished == POLISH or not free:
                return x, True
            polished += 1
        if not free:
            return x, False
        basis = RowBasis(len(free))
        for i, row in enumerate(_dense(g, columns)):
            basis.add(i, [d / cost for d, cost in zip(row, costs, strict=True)])
        step = [u / cost for u, cost in zip(basis.step(r), costs, strict=True)]
        alpha = 1.0
        for _ in range(16):
            trial = list(x)
            for c, p in enumerate(free):
                trial[p] = x[p] + alpha * step[c]
            r2, g2, norm2 = attempt(trial)
            if norm2 < norm:
                break
            alpha /= 2.0
        else:
            return x, max((abs(v) for v in r), default=0.0) <= allowed
        stalled = not patient and not polished and norm - norm2 < allowed * allowed
        if stalled and not max((abs(v) for v in r2), default=0.0) <= allowed:
            return trial, False
        x, r, g, norm = trial, r2, g2, norm2
    return x, max((abs(v) for v in r), default=0.0) <= allowed


def _length_keeper(system: System, id: EntityId) -> Callable[[Frame], list[Dual]]:
    start = system.values[system.slots[id][0] : system.slots[id][0] + 4]
    length = math.hypot(start[2] - start[0], start[3] - start[1])

    def equation(f: Frame) -> list[Dual]:
        line = f.straight(Ref(entity=id, feature=Feature.CURVE))
        return [hypot(line.b[0] - line.a[0], line.b[1] - line.a[1]) - length]

    return equation


def _in_domain(system: System, values: Sequence[float]) -> bool:
    """Sizes positive and arc sweeps inside (0, 360), for every unknown in the system."""
    for (_, path), value in zip(system.params, values, strict=True):
        if path in _SIZES and not value > 0:
            return False
        if path == "sweep_angle" and not 0 < value < 360:
            return False
    return True


# --- Settling a command -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Request:
    """What a command changed, so `settle` knows what to solve and what may move."""

    touched: frozenset[EntityId]
    """Entities (geometry or relations) whose clusters must be solved again."""
    held: frozenset[Param] = frozenset()
    """Fields the command set explicitly. They keep their new values."""
    movers: tuple[frozenset[Param], ...] = ()
    """What to try moving first, in stages; everything else in the cluster comes last."""
    keep: frozenset[EntityId] = frozenset()
    """Geometry to leave alone if anything else can move instead: an edited entity's other
    fields stay put while what's connected to it follows."""
    new: frozenset[EntityId] = frozenset()
    """Relations this command adds (or turns from driven to driving): checked for redundancy."""
    edited: frozenset[EntityId] = frozenset()
    """Geometry the command edited directly, named when the edit itself conflicts."""
    turning: frozenset[EntityId] = frozenset()
    """Lines a new direction constraint refers to. The first attempts hold their lengths,
    so they turn to the new direction instead of shrinking into it."""


def settle(before: Document, after: Document, request: Request) -> Document | list[Error]:
    """Solve every cluster the command touched. The document to keep, or why not.

    The clusters are the split ones (see `clusters`) when that gives what solving the whole
    sketch would: when the command changed no Fix and no anchored geometry. Whatever they
    can't decide exactly as the whole sketch would (see `_settle`: a failure, a repeated
    constraint, a decision close to its threshold) is worked out on the whole sketch instead,
    so rejections and their messages are exactly what they always were. Either way, values a
    solve didn't need to change stay as stored (`_kept`); under `reference`, nothing splits
    and every value is written as solved."""
    if _REFERENCE.get():
        outcome = _settle(before, after, request, _whole(after), local=False, keep=False)
        assert outcome is not None
        return outcome
    where, fixed = _grouping(after)
    if fixed and _split_safe(before, after, fixed):
        split = _settle(before, after, request, where, local=True, keep=True)
        if split is not None:
            return split
    whole = _whole(after) if fixed else where
    outcome = _settle(before, after, request, whole, local=False, keep=True)
    assert outcome is not None
    return outcome


def _settle(
    before: Document,
    after: Document,
    request: Request,
    where: Mapping[EntityId, Cluster],
    *,
    local: bool,
    keep: bool,
) -> Document | list[Error] | None:
    """Solve the touched clusters of `where`, keeping stored values the solve didn't need to
    change when `keep` (`_kept`).

    `local`: the clusters are split ones, which decide only what they decide exactly as the
    whole sketch would. Anything else is None, for the whole sketch to decide: no solution
    without the nudge (a degenerate start, where the nudge's direction depends on the
    system's unknowns), any sign of a repeated relation, a new relation close to repeating the
    others, or geometry close to collapsing. Otherwise a failure is an error."""
    solved: dict[EntityId, Entity] = {}
    touched = {where[id] for id in request.touched if id in where}
    for cluster in sorted(touched, key=lambda c: c.geometry[0]):  # `clusters` order
        system = System.build(after, cluster, before)
        outcome = _attempt(system, request, nudge=not local, keep=keep)
        if outcome is None:
            return None if local else [_conflict(system, request)]
        values, entities = outcome
        redundant = _redundancy(system, values, request.new, local=local)
        if redundant is _UNDECIDED:
            return None
        if isinstance(redundant, Error):
            return None if local else [redundant]
        if local and _near_collapse(entities.values(), after):
            return None
        solved.update(entities)
    if not solved:
        return after
    return replace(after, entities=MappingProxyType({**after.entities, **solved}))


def _split_safe(before: Document, after: Document, fixed: frozenset[EntityId]) -> bool:
    """Whether the split clusters solve the command as the whole sketch would: it moved no
    anchored geometry (a constant there) and changed no Fix (which changes what's anchored)."""
    changed = _changed(before, after)
    return not (changed & fixed) and not _any_fix(changed, before, after)


def _any_fix(ids: Iterable[EntityId], before: Document, after: Document) -> bool:
    return any(
        isinstance(entity, Constraint) and entity.type is ConstraintType.FIX
        for id in ids
        for entity in (before.entities.get(id), after.entities.get(id))
    )


def _stages(system: System, request: Request) -> list[list[int]]:
    held = system.indices(request.held) | system.constant
    everything = set(system.unknowns) - held
    stages: list[set[int]] = [set()]
    for movers in request.movers:
        stages.append((stages[-1] | system.indices(movers)) - held)
    if request.keep:
        kept = {i for id in request.keep if id in system.slots for i in system.slots[id]}
        # First the side each adjacent constraint moves (a mirror point, a parallel line),
        # then anything connected, then everything.
        followers = {
            mover(system.document, system.document.entities[id]).entity
            for id in system.relations
            if _geometry_of(system.document, id) & request.keep
        } - request.keep
        stages.append({i for id in followers for i in system.slots[id]} - held)
        stages.append(everything - kept)
    stages.append(everything)
    unique: list[list[int]] = []
    for stage in stages:
        ordered = sorted(stage)
        if not unique or ordered != unique[-1]:
            unique.append(ordered)
    return unique


def _attempt(
    system: System, request: Request, *, nudge: bool = True, keep: bool = True
) -> tuple[list[float], dict[EntityId, Entity]] | None:
    """The solved values and the geometry to write, or None if no stage works. Last, unless
    `nudge` is False, everything is tried again from slightly off the stored values. With
    `keep`, the geometry keeps stored values the solve didn't need to change (`_kept`); the
    values returned, which the caller's decisions use, are always the solved ones."""
    stages = _stages(system, request)
    holds = [request.turning, frozenset()] if request.turning else [frozenset()]
    attempts = [(free, False, hold) for free in stages for hold in holds]
    if nudge:
        attempts += [(stages[-1], True, hold) for hold in holds]
    for free, nudged, hold in attempts:
        values, ok = _newton(
            system, system.relations, free, nudge=nudged, hold_lengths=hold, patient=not keep
        )
        if not ok or (entities := _written(system, values)) is None:
            continue
        # Decided on the solved values, as the reference decides. Keeping stored values only
        # changes what's written, and only if what's kept is as valid as what was solved.
        if (
            keep
            and (kept := _kept(system, values)) is not values
            and (written := _written(system, kept)) is not None
        ):
            entities = written
        return values, entities
    return None


KEEPING_ROUNDS = 8
"""How many times `_kept` narrows what it puts back before writing every value as solved."""


def _kept(system: System, values: list[float]) -> list[float]:
    """`values`, with each unknown the solve changed by no more than `tolerance.UNCHANGED`
    put back as stored, as long as every relation still holds within `tolerance.SOLVED`, at
    the scale of both the stored values and the ones kept.

    A solve moves each unknown it may by the least that satisfies the relations, then polishes
    to the last bit, so geometry that already satisfied them picks up changes it never needed:
    round-off (a stored 0 becoming 5e-63), or a closer approach to a root it was already within
    tolerance of (a typed 22.619865 becoming 22.61986494804043). Keeping the stored value there
    keeps what the user or the model wrote, makes solving again change nothing, and stops
    round-off from building up over many edits. A change some relation needs always stays:
    each round checks every relation that reads a value put back (the others are exactly as
    solved), and gives up putting back the largest changes any relation still failing reads,
    until none fails. The caller decides everything on the solved values; this only chooses
    what's written.
    """
    stored = system.values
    changes = {
        i: abs(new - old)
        for i, (old, new) in enumerate(zip(stored, values, strict=True))
        if new != old
    }
    bound = tolerance.unchanged(stored)
    candidates = {i for i, change in changes.items() if change <= bound}
    if not candidates:
        return values
    equations = system.stored_equations()
    reading: dict[EntityId, list[int]] = {}  # a relation reads only what it refers to
    for k, (owner, _) in enumerate(equations):
        for entity in _geometry_of(system.document, owner):
            reading.setdefault(entity, []).append(k)
    for _ in range(KEEPING_ROUNDS):
        trial = [stored[i] if i in candidates else v for i, v in enumerate(values)]
        if not _in_domain(system, trial):
            return values
        affected = sorted({k for i in candidates for k in reading.get(system.params[i][0], ())})
        try:
            residuals, gradients, _ = _evaluate(
                [equations[k] for k in affected], system.frame(trial, candidates)
            )
        except (ZeroDivisionError, ValueError, OverflowError):
            return values
        # Within the tolerance the solve met, and the one at the scale of what's kept as it
        # will be stored: a solve that shrinks the geometry (a radius driven from 11 to 1), or
        # a start angle past 360 stored as a few degrees, tightens it.
        allowed = min(tolerance.solved(stored), tolerance.solved(_as_stored(system, trial)))
        failing = [k for k, r in enumerate(residuals) if not abs(r) <= allowed]
        if not failing:
            return trial
        read = {i for k in failing for i in gradients[k]} & candidates
        if not read:
            return values  # it fails for a reason putting values back can't explain
        cut = max(changes[i] for i in read)
        candidates = {i for i in candidates if changes[i] < cut}
        if not candidates:
            return values
    return values


def _as_stored(system: System, values: Sequence[float]) -> list[float]:
    """`values` as `_written` stores them: start angles in [0, 360)."""
    from caliper.engine.commands.validation import canonical_angle  # the command layer is above

    return [
        canonical_angle(v) if path == "start_angle" else v
        for (_, path), v in zip(system.params, values, strict=True)
    ]


def _near_collapse(entities: Iterable[Entity], document: Document) -> bool:
    """Whether any of `entities` is within `tolerance.DECIDED` of collapsing at the whole
    document's scale: the whole sketch, with a scale at least a split cluster's, could call it
    collapsed where the split cluster didn't."""
    geometry = [e for e in entities if isinstance(e, GEOMETRY)]
    if not geometry:
        return False
    threshold = tolerance.DECIDED * tolerance.UNCHANGED * _document_scale(document)
    return any(_collapsed(e, threshold) for e in geometry)


_SCALES: Recent[float] = Recent(4)
"""`_document_scale` for recent documents."""


def _document_scale(document: Document) -> float:
    """`tolerance.scale` of every value of the document's geometry. Worked out from the last
    document asked about: only the entities a change replaced are read again, unless one of
    them was the largest."""
    found = _SCALES.get(document)
    if found is not None:
        return found
    latest = _SCALES.latest()
    if latest is not None:
        base, size = latest
        changed = _changed(base, document)
        if all(_magnitude(base.entities.get(id)) < size for id in changed):
            found = max([size, *(_magnitude(document.entities.get(id)) for id in changed)])
    if found is None:
        found = tolerance.scale(_magnitude(e) for e in document.entities.values())
    _SCALES.put(document, found)
    return found


def _magnitude(entity: Entity | None) -> float:
    """The largest magnitude among a geometry entity's values; 0 for anything else."""
    if not isinstance(entity, GEOMETRY):
        return 0.0
    return max(abs(read(entity, path)) for path in PARAMS[type(entity)])


def _written(system: System, values: Sequence[float]) -> dict[EntityId, Entity] | None:
    """The geometry `values` describe, or None if any of it became degenerate.

    Degenerate includes nearly so: a line shrunk to a billionth of the sketch's size has
    satisfied its equations by collapsing, which no one asked for. The size is the larger of
    the solve's start and end: Newton converged at the start's, so a solve that squeezes the
    sketch (a 69 mm rectangle to 1e-9 mm wide) is judged at that size too, not at the tiny
    one it made (C-12).
    """
    from caliper.engine.commands.validation import (  # the command layer is above
        canonical_angle,
        domain_errors,
    )

    tiny = tolerance.unchanged([*system.values, *values])
    changes: dict[EntityId, dict[str, float]] = {}
    for (id, path), old, new in zip(system.params, system.values, values, strict=True):
        if new != old:
            if path == "start_angle":
                new = canonical_angle(new)  # stored in [0, 360), as `build_entity` stores it
            changes.setdefault(id, {})[path] = new + 0.0  # never store -0.0
    entities: dict[EntityId, Entity] = {}
    for id, fields_ in changes.items():
        current = system.document.entities[id]
        assert isinstance(current, GEOMETRY)
        entity = write(current, fields_)
        if domain_errors(entity) or _collapsed(entity, tiny):
            return None
        entities[id] = entity
    return entities


def _collapsed(entity: Geometry, tiny: float) -> bool:
    match entity:
        case Line(start=a, end=b):
            return math.hypot(b.x - a.x, b.y - a.y) <= tiny
        case Circle(radius=r):
            return r <= tiny
        case Arc(radius=r, sweep_angle=sweep):
            return r <= tiny or sweep <= 1e-9 or sweep >= 360.0 - 1e-9
        case Rectangle(width=w, height=h):
            return w <= tiny or h <= tiny
    return False


def _conflict(system: System, request: Request) -> Error:
    """The smallest set of existing relations that can't hold together with the command.

    Found with QuickXplain (Junker, 2004): split the candidates in half, keep whichever half
    already fails, recurse. That takes a few solves of small subsets instead of one solve of
    the whole cluster per relation, and small subsets are easy for a local solver: a line
    made vertical is found to conflict with its horizontal, even where swinging it 90° to
    test the alternative would be out of the solver's reach. The command's own edit is
    blamed too when releasing it lets everything hold.
    """
    held = system.indices(request.held)
    free = [i for i in system.unknowns if i not in held]
    added = [id for id in system.relations if id in request.new]
    near = set(_neighbours(system, request))
    # Relations on the same geometry first: QuickXplain prefers blaming earlier candidates.
    candidates = sorted(
        (id for id in system.relations if id not in request.new),
        key=lambda id: (id not in near, id),
    )

    def holds(relations: Sequence[EntityId]) -> bool:
        return _solves(system, relations, free)

    culprits = sorted(_quickxplain(added, candidates, holds)) if holds(added) else []
    edit_blamed = bool(held) and _solves(system, system.relations, system.unknowns)
    ids = sorted({*culprits, *(request.edited if edit_blamed else ())})
    subject = _subject(system.document, request)
    alone = (
        f"{subject} can't be satisfied by the geometry it refers to (a rectangle can't turn, "
        "and nothing may shrink to nothing)"
    )
    if not ids:
        return Error(code=ErrorCode.CONSTRAINT_CONFLICT, message=alone)
    if culprits and set(culprits) <= request.touched and not edit_blamed:
        # The changed relation alone (a dimension's new value): nothing else to blame.
        return Error(code=ErrorCode.CONSTRAINT_CONFLICT, message=alone, ids=tuple(ids))
    if edit_blamed and not culprits:
        message = f"{subject} can't move that way; the constraints hold it"
    else:
        message = f"{subject} conflicts with {_names(system.document, culprits)}"
    return Error(code=ErrorCode.CONSTRAINT_CONFLICT, message=message, ids=tuple(ids))


def _quickxplain(
    background: list[EntityId],
    candidates: list[EntityId],
    holds: Callable[[Sequence[EntityId]], bool],
) -> list[EntityId]:
    """A minimal subset X of `candidates` such that `background` + X doesn't hold.

    Assumes `background` holds and `background` + `candidates` doesn't.
    """

    def search(base: list[EntityId], grew: bool, pool: list[EntityId]) -> list[EntityId]:
        if grew and not holds(base):
            return []
        if len(pool) == 1:
            return pool
        half = len(pool) // 2
        first, second = pool[:half], pool[half:]
        from_second = search(base + first, bool(first), second)
        from_first = search(base + from_second, bool(from_second), first)
        return from_first + from_second

    return search(background, False, candidates) if candidates else []


def _solves(system: System, relations: Sequence[EntityId], free: Iterable[int]) -> bool:
    free = list(free)
    for nudge in (False, True):
        values, ok = _newton(system, relations, free, nudge=nudge)
        if ok and _written(system, values) is not None:
            return True
    return False


def _subject(document: Document, request: Request) -> str:
    """What the command did, for messages: "the new vertical constraint", "the edit to e3"."""
    if request.new:
        id = min(request.new)
        entity = document.entities[id]
        if isinstance(entity, Constraint):
            return f"the new {entity.type.value} constraint"
        return f"the new {entity.kind.replace('_', ' ')}"
    if request.edited:
        return f"the change to {', '.join(sorted(request.edited))}"
    return "the change to " + ", ".join(sorted(request.touched))


def _neighbours(system: System, request: Request) -> list[EntityId]:
    """Relations sharing geometry with what the command touched."""
    document = system.document
    near = {g for id in request.touched for g in _geometry_of(document, id)}
    return sorted(
        id for id in system.relations if id not in request.new and _geometry_of(document, id) & near
    )


def _geometry_of(document: Document, id: EntityId) -> set[EntityId]:
    entity = document.entities[id]
    return {r.entity for r in references(entity)} or {id}


class _Undecided:
    """A redundancy decision a split cluster leaves to the whole sketch (see `_redundancy`)."""


_UNDECIDED = _Undecided()


def _redundancy(
    system: System, values: Sequence[float], new: frozenset[EntityId], *, local: bool = False
) -> Error | _Undecided | None:
    """An error when a new relation repeats what the others already say.

    `local`: the system is a split cluster. Its rows lack the anchored constants' columns, so
    a row's length, and what `tolerance.INDEPENDENT` compares it with, differ from the whole
    sketch's. A new relation clearly independent is so either way; one within
    `tolerance.DECIDED` of the threshold is `_UNDECIDED`, for the whole sketch to decide."""
    added = sorted((id for id in system.relations if id in new), key=_number)
    if not added:
        return None
    # Rows and columns in the order entities were made, so a check that follows another on
    # the same cluster (the next command of a pattern) starts with the rows and columns the
    # last one had, and carries on from its factorization (`_extended`).
    order = sorted((id for id in system.relations if id not in new), key=_number) + added
    everything = system.unknowns
    canonical = sorted(everything, key=lambda i: (_number(system.params[i][0]), i))
    columns = {i: c for c, i in enumerate(canonical)}
    equations = system.equations(order, values)
    _, gradients, owners = _evaluate(equations, system.frame(values, everything))
    rows = _dense(gradients, columns)
    basis = _extended(tuple(system.params[i] for i in canonical), rows)
    if local:
        # A kept row's last entry in `lower` is what was left of it after the rows before it.
        close = tolerance.DECIDED * tolerance.INDEPENDENT
        position = {i: k for k, i in enumerate(basis.kept)}
        if any(
            owner in new
            and i in position
            and basis.lower[position[i]][-1] <= close * math.sqrt(sum(x * x for x in rows[i]))
            for i, owner in enumerate(owners)
        ):
            return _UNDECIDED
    for i, owner in enumerate(owners):
        if owner in new and i in basis.dependent:
            weights = basis.dependent[i]
            largest = max((abs(w) for w in weights), default=0.0)
            implying = sorted(
                {
                    owners[basis.kept[j]]
                    for j, w in enumerate(weights)
                    if largest > 0 and abs(w) > tolerance.IMPLYING * largest
                }
                - new
            )
            subject = _subject(system.document, Request(touched=new, new=frozenset({owner})))
            # A relation of several equations (a fix, a coincidence) can repeat only some of
            # what the others say; it's rejected all the same, but it isn't implied.
            partly = any(o == owner and j not in basis.dependent for j, o in enumerate(owners))
            if not implying:
                message = f"{subject} {'partly ' if partly else ''}always holds here"
                message += "" if partly else "; it adds nothing"
            elif partly:
                message = (
                    f"{subject} is partly implied by {_names(system.document, implying)}: the "
                    "rest of it is new, but a constraint can't repeat any of what the others "
                    "say. Delete what it overlaps, or add only what's missing"
                )
            else:
                message = f"{subject} is already implied by {_names(system.document, implying)}"
                if not isinstance(system.document.entities[owner], Constraint):
                    message += "; add it as a driven dimension (no value) instead"
            return Error(code=ErrorCode.CONSTRAINT_REDUNDANT, message=message, ids=tuple(implying))
    return None


def _number(id: EntityId) -> tuple[int, str]:
    """An id's place in the order entities were made: `e12` after `e9`."""
    digits = id[1:]
    return (int(digits), "") if id[:1] == "e" and digits.isdigit() else (sys.maxsize, id)


@dataclass(frozen=True, slots=True)
class _Factored:
    """A redundancy check's factorization after its first `len(rows)` rows."""

    columns: tuple[Param, ...]
    rows: tuple[tuple[tuple[int, float], ...], ...]
    """Each row's nonzero entries, as (column, value)."""
    basis: RowBasis


_FACTORED = threading.local()
"""Each thread's last redundancy check's factorization (`_extended`)."""


def _extended(columns: tuple[Param, ...], rows: list[list[float]]) -> RowBasis:
    """`rows` factorized, carrying on from the last check's factorization for as many rows as
    this check starts with, to the bit, in the same columns. A factorization only ever
    appends: each row's part depends only on the rows before it, so the last one's state after
    its first k rows is exactly what factorizing those k rows again would give, and it's kept
    whole inside it. A command adding to a cluster reuses every row; an edit that moves some
    geometry reuses the rows before the first it changed. New columns are new geometry, last
    in the order and zero in the old rows. The reference solver works everything out afresh."""
    width = len(columns)
    entries = tuple(tuple((c, x) for c, x in enumerate(row) if x != 0.0) for row in rows)
    fresh = _REFERENCE.get()
    last: _Factored | None = None if fresh else getattr(_FACTORED, "last", None)
    start = 0
    if last is not None and columns[: len(last.columns)] == last.columns:
        for kept, now in zip(last.rows, entries, strict=False):
            if kept != now:
                break
            start += 1
    basis = RowBasis(width) if last is None or not start else _truncated(last.basis, start, width)
    for i in range(start, len(rows)):
        basis.add(i, rows[i])
    if not fresh:
        _FACTORED.last = _Factored(columns, entries, basis)
    return basis


def _truncated(basis: RowBasis, rows: int, width: int) -> RowBasis:
    """A copy of `basis` as it was after its first `rows` rows, over `width` columns (the new
    ones zero in every row)."""
    grow = [0.0] * (width - basis.width)
    kept = [i for i in basis.kept if i < rows]
    n = len(kept)
    return RowBasis(
        width,
        q=[[*row, *grow] for row in basis.q[:n]],
        lower=[row[:] for row in basis.lower[:n]],
        kept=kept,
        dependent={i: row[:] for i, row in basis.dependent.items() if i < rows},
        supports=basis.supports[:n],
        masks=basis.masks[:n],
    )


def _names(document: Document, ids: Sequence[EntityId]) -> str:
    def name(id: EntityId) -> str:
        entity = document.entities.get(id)
        match entity:
            case Constraint(type=type_):
                return f"{id} ({type_.value})"
            case DistanceDimension(value=v) | RadialDimension(value=v) | AngleDimension(value=v):
                return f"{id} ({entity.kind.replace('_', ' ')} {v!r})"
            case None:
                return id
        return f"{id} ({entity.kind})"

    return ", ".join(name(id) for id in ids) or "nothing"


def mover(document: Document, relation: Entity) -> Ref:
    """The reference a constraint or dimension moves when it has to: the last one given for a
    constraint (as its rule says), `b` for a distance or angle, the curve for a radius."""
    match relation:
        case Constraint(type=type_, refs=refs):
            found = match(document, type_, refs)
            assert isinstance(found, Match)
            return found.refs[found.rule.mover]
        case DistanceDimension(b=b) | AngleDimension(b=b):
            return b
        case RadialDimension(target=target):
            return Ref(entity=target, feature=Feature.CURVE)
    raise ValueError(f"{relation!r} isn't a constraint or dimension")


def turning(document: Document, relation: Entity) -> frozenset[EntityId]:
    """Lines a relation sets the direction of: they should turn, not shrink, when it's added."""
    match relation:
        case Constraint(type=type_, refs=refs):
            found = match(document, type_, refs)
            assert isinstance(found, Match)
            if not found.rule.turns:
                return frozenset()
        case AngleDimension(a=a, b=b):
            refs = (a, b)
        case _:
            return frozenset()
    return frozenset(r.entity for r in refs if isinstance(document.entities[r.entity], Line))


def canonical(document: Document, constraint: Constraint) -> tuple[Ref, ...]:
    """A valid constraint's references in the order it is stored in."""
    found = match(document, constraint.type, constraint.refs)
    assert isinstance(found, Match), found
    return found.refs


def entity_params(document: Document, id: EntityId) -> frozenset[Param]:
    entity = document.entities[id]
    assert isinstance(entity, GEOMETRY)
    return frozenset((id, path) for path in PARAMS[type(entity)])


def ref_params(document: Document, ref: Ref) -> frozenset[Param]:
    """The unknowns a feature depends on: a line END is its end point, a rectangle's
    BOTTOM_RIGHT its corner and width."""
    entity = document.entities[ref.entity]
    assert isinstance(entity, GEOMETRY)
    system = System.build(document, Cluster((ref.entity,), ()))
    paths = PARAMS[type(entity)]
    return frozenset((ref.entity, paths[i]) for i in system.depends(ref))


def implied(
    document: Document, constraint: Constraint, id: EntityId, joins: Mapping[EntityId, Cluster]
) -> bool:
    """Whether `constraint`, added to `document` under `id`, would be redundant there.

    `joins` is each clustered geometry entity's cluster in `document`, as `clusters` finds
    them, worked out once for many questions. The constraint joins the clusters of what it
    refers to into one, members sorted as `clusters` sorts them, so the answer is the one a
    whole new document's clusters would give.
    """
    geometry: set[EntityId] = set()
    fixed: set[EntityId] = set()
    relations = {id}
    for entity in {r.entity for r in constraint.refs}:
        joined = joins.get(entity)
        if joined is None:
            geometry.add(entity)
        else:
            geometry.update(joined.geometry)
            fixed.update(joined.fixed)
            relations.update(joined.relations)
    with_it = replace(document, entities=_Adding(document.entities, id, constraint))
    joined = Cluster(
        tuple(sorted(geometry)), tuple(sorted(relations)), tuple(sorted(fixed - geometry))
    )
    system = System.build(with_it, joined)
    return _redundancy(system, system.values, frozenset({id})) is not None


class _Adding(Mapping[EntityId, Entity]):
    """A document's entities plus one more, without copying them."""

    def __init__(self, entities: Mapping[EntityId, Entity], id: EntityId, entity: Entity) -> None:
        self._entities, self._id, self._entity = entities, id, entity

    def __getitem__(self, key: EntityId) -> Entity:
        return self._entity if key == self._id else self._entities[key]

    def __iter__(self) -> Iterator[EntityId]:
        yield from self._entities
        if self._id not in self._entities:
            yield self._id

    def __len__(self) -> int:
        return len(self._entities) + (self._id not in self._entities)


# --- How well relations hold ------------------------------------------------------------


def residuals(document: Document) -> dict[EntityId, tuple[float, float]]:
    """How far each relation is from holding in the stored geometry, and how far it may be:
    its largest residual, and `tolerance.SOLVED` at the scale of its whole cluster (what a
    solve of the sketch as the old solver grouped it allows, so a split cluster's stricter
    answer always fits). For tests and diagnostics; a solved document has every residual
    within what's allowed."""
    found: dict[EntityId, tuple[float, float]] = {}
    for cluster in {id(c): c for c in _whole(document).values()}.values():
        system = System.build(document, cluster)
        allowed = tolerance.solved(system.values)
        try:
            equations = system.equations(system.relations, system.values)
            values, _, owners = _evaluate(equations, system.frame(system.values))
        except (ZeroDivisionError, ValueError, OverflowError):  # degenerate: nothing holds
            found.update(dict.fromkeys(system.relations, (math.inf, allowed)))
            continue
        for owner, value in zip(owners, values, strict=True):
            size = abs(value) if math.isfinite(value) else math.inf
            found[owner] = (max(found.get(owner, (0.0, allowed))[0], size), allowed)
    return found


# --- Status -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Health:
    """What `status` learns from one cluster. It depends on nothing outside the cluster and
    the anchored geometry it holds as constants."""

    rank: int
    free: tuple[int, ...]
    """Each geometry entity's remaining degrees of freedom, in `Cluster.geometry` order."""
    conflicting: frozenset[EntityId]
    redundant: frozenset[EntityId]


@dataclass(frozen=True, slots=True)
class _Solved:
    """One document's status, in the pieces the next document's status can reuse."""

    document: Document
    clusters: tuple[Cluster, ...]
    health: tuple[_Health, ...]
    """One per cluster, in the same order."""
    where: Mapping[EntityId, int]
    """The cluster of every clustered entity, geometry and relations alike."""
    unknowns: int
    """Parameters of all the geometry: the degrees of freedom before any relation."""
    entity_dof: Mapping[EntityId, int]
    """Sorted by id, and never changed once built: the next status copies it."""
    fixed: frozenset[EntityId] = frozenset()
    """Anchored geometry some cluster holds as a constant."""


_SOLVED: Recent[tuple[_Solved, SolveStatus]] = Recent(4)
"""Status of recent documents (the window asks about its own, a proposal's, and the one it's
based on), with the pieces the next document's status reuses: documents share the entity
objects an edit didn't change, so only the clusters an edit touched are redone."""


def status(document: Document) -> SolveStatus:
    """Degrees of freedom and constraint health of the stored geometry."""
    if _REFERENCE.get():
        return _status(_solved(document, None))
    cached = _SOLVED.get(document)
    if cached is not None:
        return cached[1]
    latest = _SOLVED.latest()
    if latest is None:
        solved = _solved(document, None)
    else:
        last = latest[1][0]
        changed = _changed(last.document, document)
        if _any_relation(changed, last.document, document):
            solved = _solved(document, last)  # clusters may have joined or split
        else:
            solved = _updated(last, document, changed)
    found = _status(solved)
    _SOLVED.put(document, (solved, found))
    return found


def _status(solved: _Solved) -> SolveStatus:
    dof = solved.unknowns - sum(h.rank for h in solved.health)
    conflicting = sorted(id for h in solved.health for id in h.conflicting)
    redundant = sorted(id for h in solved.health for id in h.redundant)
    if conflicting:
        state = ConstraintState.CONFLICTING
    elif redundant:
        state = ConstraintState.OVER
    elif dof == 0:
        state = ConstraintState.FULLY
    else:
        state = ConstraintState.UNDER
    return SolveStatus(
        state=state,
        dof=dof,
        entity_dof=MappingProxyType(solved.entity_dof),
        conflicting=tuple(conflicting),
        redundant=tuple(redundant),
    )


def _solved(document: Document, last: _Solved | None) -> _Solved:
    """Everything from the clusters up, reusing any cluster whose entities are unchanged."""
    unique = {id(c): c for c in grouped(document).values()}.values()
    found = tuple(sorted(unique, key=lambda c: c.geometry[0]))  # `clusters` order
    before = (
        {}
        if last is None
        else {c.geometry + c.relations + c.fixed: i for i, c in enumerate(last.clusters)}
    )
    health: list[_Health] = []
    where: dict[EntityId, int] = {}
    for index, cluster in enumerate(found):
        members = cluster.geometry + cluster.relations
        reused = before.get(members + cluster.fixed)
        if (
            last is not None
            and reused is not None
            and all(
                last.document.entities[id] is document.entities[id]
                for id in (*members, *cluster.fixed)
            )
        ):
            health.append(last.health[reused])
        else:
            health.append(_health(document, cluster))
        where.update(dict.fromkeys(members, index))
    entity_dof = {
        id: len(PARAMS[type(e)]) for id, e in document.entities.items() if isinstance(e, GEOMETRY)
    }
    unknowns = sum(entity_dof.values())
    for cluster, h in zip(found, health, strict=True):
        entity_dof.update(zip(cluster.geometry, h.free, strict=True))
    return _Solved(
        document,
        found,
        tuple(health),
        where,
        unknowns,
        dict(sorted(entity_dof.items())),
        frozenset(id for cluster in found for id in cluster.fixed),
    )


def _updated(last: _Solved, document: Document, changed: set[EntityId]) -> _Solved:
    """`last`, after an edit that changed geometry only, so every cluster keeps its members."""
    if changed & last.fixed:  # a constant of other clusters: find them all again
        return _solved(document, last)
    touched = {last.where[id] for id in changed if id in last.where}
    health = list(last.health)
    for index in touched:
        health[index] = _health(document, last.clusters[index])
    entity_dof = dict(last.entity_dof)
    unknowns = last.unknowns
    added = False
    for id in changed:
        old, new = last.document.entities.get(id), document.entities.get(id)
        if isinstance(old, GEOMETRY):
            unknowns -= len(PARAMS[type(old)])
            if not isinstance(new, GEOMETRY):
                del entity_dof[id]
        if isinstance(new, GEOMETRY):
            unknowns += len(PARAMS[type(new)])
            added = added or id not in entity_dof
            entity_dof[id] = len(PARAMS[type(new)])
    for index in touched:
        entity_dof.update(zip(last.clusters[index].geometry, health[index].free, strict=True))
    if added:
        entity_dof = dict(sorted(entity_dof.items()))
    return _Solved(
        document, last.clusters, tuple(health), last.where, unknowns, entity_dof, last.fixed
    )


def _changed(before: Document, after: Document) -> set[EntityId]:
    """Ids whose entity was added, removed, or replaced, compared by identity."""
    old = before.entities
    changed = {id for id, e in after.entities.items() if old.get(id) is not e}
    if len(old) != len(after.entities) - sum(1 for id in changed if id not in old):
        changed.update(id for id in old if id not in after.entities)
    return changed


def _any_relation(ids: Iterable[EntityId], before: Document, after: Document) -> bool:
    return any(
        is_relation(entity)
        for id in ids
        for entity in (before.entities.get(id), after.entities.get(id))
        if entity is not None
    )


def _health(document: Document, cluster: Cluster) -> _Health:
    system = System.build(document, cluster)
    values = system.values
    everything = system.unknowns
    equations = system.equations(system.relations, values)
    residuals, gradients, owners = _evaluate(equations, system.frame(values, everything))
    allowed = tolerance.broken(values)
    broken = {owners[i] for i, r in enumerate(residuals) if abs(r) > allowed}
    basis = RowBasis(len(values))
    for i, row in enumerate(_dense(gradients, {i: i for i in everything})):
        basis.add(i, row)
    return _Health(
        rank=basis.rank,
        free=tuple(basis.free_dimensions(system.slots[id]) for id in cluster.geometry),
        conflicting=frozenset(broken),
        redundant=frozenset({owners[i] for i in basis.dependent} - broken),
    )
