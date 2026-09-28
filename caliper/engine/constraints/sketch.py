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
"""

import math
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
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
RESHAPE_COST = 100.0
"""How much more a unit change of a size (radius, width, height) costs than a unit move.

Steps minimize the weighted change, so a free circle or rectangle translates to meet a
constraint rather than shrinking or growing to reach it, as in any CAD sketcher."""
_SIZES = frozenset({"radius", "width", "height"})
"""Extra Newton steps once within tolerance, while they still reduce the error. They take
results to the last bit, so a width driven to 120 is stored as exactly 120.0."""


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


def clusters(document: Document) -> list[Cluster]:
    """Geometry joined by relations, each group with its relations. Lone geometry is omitted.

    Anchored geometry joins only relations that refer to nothing else, such as its own Fix;
    elsewhere it's one of the cluster's `fixed` constants."""
    return _clusters_of(document, _relation_ids(document), anchored(document))


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


type Joints = Mapping[tuple[EntityId, EntityId], Ref]


def _joints(document: Document, relations: Iterable[EntityId]) -> Joints:
    """Where a coincident constraint puts an arc's end on another curve (a line or another
    arc or circle, at its end or anywhere along it): that end, by (arc, other curve).
    Tangency between the two is written at the joint (`tangent_at_joint`)."""
    joints: dict[tuple[EntityId, EntityId], Ref] = {}
    for id in relations:
        entity = document.entities[id]
        if not (isinstance(entity, Constraint) and entity.type is ConstraintType.COINCIDENT):
            continue
        for end, other in permutations(entity.refs):
            if (
                isinstance(document.entities[end.entity], Arc)
                and end.feature in (Feature.START, Feature.END)
                and isinstance(document.entities[other.entity], Line | Arc | Circle)
                and other.feature in (Feature.START, Feature.END, Feature.CURVE)
                and other.entity != end.entity
            ):
                joints.setdefault((end.entity, other.entity), end)
    return joints


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
                joint = joints.get((ordered[0].entity, ordered[1].entity)) or joints.get(
                    (ordered[1].entity, ordered[0].entity)
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


def _tolerance(values: Sequence[float]) -> float:
    return 1e-10 * max(1.0, *(abs(v) for v in values)) if values else 1e-10


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
) -> tuple[list[float], bool]:
    """Minimum-norm Newton with backtracking on the free unknowns. True when solved.

    `nudge` starts from slightly off the stored values. A line exactly perpendicular to where
    a constraint wants it sits on a saddle, where no small change helps; a deterministic
    nudge of a thousandth of the sketch's size gives Newton a slope to follow.

    `hold_lengths` adds, for this solve only, equations keeping those lines as long as they
    were: they come last, so they only decide what the real constraints leave open.
    """
    held_lines = [id for id in hold_lengths if system.kinds.get(id) is Line]
    equations = system.equations(relations, list(system.values)) + [
        (id, _length_keeper(system, id)) for id in held_lines
    ]
    x = list(system.values)
    if nudge:
        size = 1e-3 * max(1.0, *(abs(v) for v in x))
        for n, i in enumerate(free):
            x[i] += size * ((n * 7919) % 13 - 6) / 6  # a fixed spread in [-1, 1]
        if not _in_domain(system, x):
            x = list(system.values)
    columns = {p: c for c, p in enumerate(free)}
    tolerance = _tolerance(x)
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
        if max((abs(v) for v in r), default=0.0) <= tolerance:
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
            return x, max((abs(v) for v in r), default=0.0) <= tolerance
        x, r, g, norm = trial, r2, g2, norm2
    return x, max((abs(v) for v in r), default=0.0) <= tolerance


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
    sketch would: when the command changed no Fix and no anchored geometry. Anything they
    can't settle, a conflict or a repeated constraint, is worked out on the whole sketch
    instead, so rejections and their messages are exactly what they always were."""
    where, fixed = _grouping(after)
    if fixed and _split_safe(before, after, fixed):
        split = _settle(before, after, request, where, final=False)
        if split is not None:
            return split
    whole = _whole(after) if fixed else where
    outcome = _settle(before, after, request, whole, final=True)
    assert outcome is not None
    return outcome


def _settle(
    before: Document,
    after: Document,
    request: Request,
    where: Mapping[EntityId, Cluster],
    *,
    final: bool,
) -> Document | list[Error] | None:
    """Solve the touched clusters of `where`. When not `final`, None instead of an error, for
    the whole sketch to decide."""
    solved: dict[EntityId, Entity] = {}
    touched = {where[id] for id in request.touched if id in where}
    for cluster in sorted(touched, key=lambda c: c.geometry[0]):  # `clusters` order
        system = System.build(after, cluster, before)
        # The nudge only rescues a degenerate start, and where it pushes depends on which
        # unknowns the system holds: split clusters leave it to the whole sketch.
        outcome = _attempt(system, request, nudge=final)
        if outcome is None:
            return [_conflict(system, request)] if final else None
        values, entities = outcome
        redundant = _redundancy(system, values, request.new)
        if redundant is not None:
            return [redundant] if final else None
        solved.update(entities)
    if not solved:
        return after
    return Document(entities=MappingProxyType({**after.entities, **solved}), next_id=after.next_id)


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
    system: System, request: Request, *, nudge: bool = True
) -> tuple[list[float], dict[EntityId, Entity]] | None:
    """The solved values and the geometry they describe, or None if no stage works. Last,
    unless `nudge` is False, everything is tried again from slightly off the stored values."""
    stages = _stages(system, request)
    holds = [request.turning, frozenset()] if request.turning else [frozenset()]
    attempts = [(free, False, hold) for free in stages for hold in holds]
    if nudge:
        attempts += [(stages[-1], True, hold) for hold in holds]
    for free, nudged, hold in attempts:
        values, ok = _newton(system, system.relations, free, nudge=nudged, hold_lengths=hold)
        if ok and (entities := _written(system, values)) is not None:
            return values, entities
    return None


def _written(system: System, values: Sequence[float]) -> dict[EntityId, Entity] | None:
    """The geometry `values` describe, or None if any of it became degenerate.

    Degenerate includes nearly so: a line shrunk to a billionth of the sketch's size has
    satisfied its equations by collapsing, which no one asked for.
    """
    from caliper.engine.commands.validation import (  # the command layer is above
        canonical_angle,
        domain_errors,
    )

    tiny = 1e-9 * max(1.0, *(abs(v) for v in values))
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
    if not ids:
        return Error(
            code=ErrorCode.CONSTRAINT_CONFLICT,
            message=f"{subject} can't be satisfied by the geometry it refers to (a "
            "rectangle can't turn, and nothing may shrink to nothing)",
        )
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


def _redundancy(system: System, values: Sequence[float], new: frozenset[EntityId]) -> Error | None:
    """An error when a new relation repeats what the others already say."""
    added = [id for id in system.relations if id in new]
    if not added:
        return None
    order = [id for id in system.relations if id not in new] + added
    everything = system.unknowns
    equations = system.equations(order, values)
    _, gradients, owners = _evaluate(equations, system.frame(values, everything))
    basis = RowBasis(len(values))
    rows = _dense(gradients, {i: i for i in everything})
    for i, row in enumerate(rows):
        basis.add(i, row)
    for i, owner in enumerate(owners):
        if owner in new and i in basis.dependent:
            weights = basis.dependent[i]
            largest = max((abs(w) for w in weights), default=0.0)
            implying = sorted(
                {
                    owners[basis.kept[j]]
                    for j, w in enumerate(weights)
                    if largest > 0 and abs(w) > 1e-8 * largest
                }
                - new
            )
            subject = _subject(system.document, Request(touched=new, new=frozenset({owner})))
            if not implying:
                message = f"{subject} always holds here; it adds nothing"
            else:
                message = f"{subject} is already implied by {_names(system.document, implying)}"
                if not isinstance(system.document.entities[owner], Constraint):
                    message += "; add it as a driven dimension (no value) instead"
            return Error(code=ErrorCode.CONSTRAINT_REDUNDANT, message=message, ids=tuple(implying))
    return None


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
    with_it = Document(
        entities=_Adding(document.entities, id, constraint), next_id=document.next_id
    )
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


_LAST: list[_Solved] = []
"""The last document `status` saw. Documents share the entity objects an edit didn't change,
so the next one is compared by identity, and only the clusters an edit touched are redone."""


def status(document: Document) -> SolveStatus:
    """Degrees of freedom and constraint health of the stored geometry."""
    last = _LAST[0] if _LAST else None
    if last is None:
        solved = _solved(document, None)
    else:
        changed = _changed(last.document, document)
        if _any_relation(changed, last.document, document):
            solved = _solved(document, last)  # clusters may have joined or split
        else:
            solved = _updated(last, document, changed)
    _LAST[:] = [solved]
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
    tolerance = 1e3 * _tolerance(values)
    broken = {owners[i] for i, r in enumerate(residuals) if abs(r) > tolerance}
    basis = RowBasis(len(values))
    for i, row in enumerate(_dense(gradients, {i: i for i in everything})):
        basis.add(i, row)
    return _Health(
        rank=basis.rank,
        free=tuple(basis.free_dimensions(system.slots[id]) for id in cluster.geometry),
        conflicting=frozenset(broken),
        redundant=frozenset({owners[i] for i in basis.dependent} - broken),
    )
