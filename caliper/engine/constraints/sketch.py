"""Solving a document: clusters, staged solves, conflicts, redundancy, and status.

Constraints and driving dimensions ("relations") connect geometry into clusters that solve
independently. A command asks `settle` to re-solve the clusters it touched. The solve runs in
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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from caliper.contracts.document import (
    POINT_FEATURES,
    AngleDimension,
    Arc,
    Circle,
    Constraint,
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
from caliper.engine.constraints.ad import Dual
from caliper.engine.constraints.linalg import RowBasis
from caliper.engine.constraints.model import PARAMS, Frame, read, write
from caliper.engine.constraints.relations import (
    Match,
    Setting,
    match,
    measure,
    measure_angle,
)

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


def clusters(document: Document) -> list[Cluster]:
    """Geometry joined by relations, each group with its relations. Lone geometry is omitted."""
    parent: dict[EntityId, EntityId] = {}

    def root(e: EntityId) -> EntityId:
        while parent.setdefault(e, e) != e:
            parent[e] = parent[parent[e]]
            e = parent[e]
        return e

    owners: dict[EntityId, EntityId] = {}
    for id in sorted(document.entities):
        entity = document.entities[id]
        if not is_relation(entity):
            continue
        targets = sorted({r.entity for r in references(entity)})
        first = root(targets[0])  # registers it, even when the relation has one entity
        for other in targets[1:]:
            parent[root(other)] = first
        owners[id] = targets[0]
    groups: dict[EntityId, tuple[list[EntityId], list[EntityId]]] = {}
    for e in sorted(parent):
        groups.setdefault(root(e), ([], []))[0].append(e)
    for id, owner in owners.items():
        groups[root(owner)][1].append(id)
    return [Cluster(tuple(g), tuple(r)) for g, r in groups.values()]


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

    @classmethod
    def build(
        cls, document: Document, cluster: Cluster, before: Document | None = None
    ) -> "System":
        kinds: dict[EntityId, type[Geometry]] = {}
        slots: dict[EntityId, list[int]] = {}
        params: list[Param] = []
        values: list[float] = []
        anchors: list[float] = []
        for id in cluster.geometry:
            entity = document.entities[id]
            assert isinstance(entity, GEOMETRY)
            kinds[id] = type(entity)
            old = before.entities.get(id) if before is not None else None
            if type(old) is not type(entity):
                old = entity
            slots[id] = []
            for path in PARAMS[type(entity)]:
                slots[id].append(len(params))
                params.append((id, path))
                values.append(read(entity, path))
                anchors.append(read(old, path))
        return cls(document, kinds, slots, params, values, anchors, list(cluster.relations))

    def frame(self, values: Sequence[float], variables: Iterable[int] = ()) -> Frame:
        return Frame(self.kinds, self.slots, values, frozenset(variables))

    def equations(
        self, relations: Sequence[EntityId], first: Sequence[float]
    ) -> list[tuple[EntityId, Callable[[Frame], list[Dual]]]]:
        setting = Setting(first=self.frame(first), anchor=self.frame(self.anchors))
        return [(id, _compile(self.document, id, setting)) for id in relations]

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


def _compile(document: Document, id: EntityId, at: Setting) -> Callable[[Frame], list[Dual]]:
    entity = document.entities[id]
    match entity:
        case Constraint(type=type_, refs=refs):
            found = match(document, type_, refs)
            assert isinstance(found, Match), found
            equations, ordered = found.rule.equations, found.refs
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
    system: System, relations: Sequence[EntityId], free: Sequence[int]
) -> tuple[list[float], bool]:
    """Minimum-norm Newton with backtracking on the free unknowns. True when solved."""
    x = list(system.values)
    equations = system.equations(relations, x)
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


def settle(before: Document, after: Document, request: Request) -> Document | list[Error]:
    """Solve every cluster the command touched. The document to keep, or why not."""
    solved: dict[EntityId, Entity] = {}
    for cluster in clusters(after):
        members = set(cluster.geometry) | set(cluster.relations)
        if not cluster.relations or not members & request.touched:
            continue
        system = System.build(after, cluster, before)
        outcome = _solve_cluster(system, request)
        if isinstance(outcome, Error):
            return [outcome]
        values, entities = outcome
        redundant = _redundancy(system, values, request.new)
        if redundant is not None:
            return [redundant]
        solved.update(entities)
    if not solved:
        return after
    return Document(entities=MappingProxyType({**after.entities, **solved}), next_id=after.next_id)


def _stages(system: System, request: Request) -> list[list[int]]:
    held = system.indices(request.held)
    everything = set(range(len(system.params))) - held
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


def _solve_cluster(
    system: System, request: Request
) -> tuple[list[float], dict[EntityId, Entity]] | Error:
    for free in _stages(system, request):
        values, ok = _newton(system, system.relations, free)
        if ok and (entities := _written(system, values)) is not None:
            return values, entities
    return _conflict(system, request)


def _written(system: System, values: Sequence[float]) -> dict[EntityId, Entity] | None:
    """The geometry `values` describe, or None if any of it became degenerate.

    Degenerate includes nearly so: a line shrunk to a billionth of the sketch's size has
    satisfied its equations by collapsing, which no one asked for.
    """
    from caliper.engine.commands.validation import domain_errors  # the command layer is above

    tiny = 1e-9 * max(1.0, *(abs(v) for v in values))
    changes: dict[EntityId, dict[str, float]] = {}
    for (id, path), old, new in zip(system.params, system.values, values, strict=True):
        if new != old:
            if path == "start_angle":
                new %= 360.0
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
    """Which relations (and whether the command's own edit) stand in the way.

    Each relation is dropped in turn; the ones whose removal lets the rest solve are the
    conflict. The solver is local, so a conflict it could only escape by a large jump (a
    horizontal line made vertical at a fixed length) may have no single culprit it can
    find; then every relation on the geometry involved is named.
    """
    held = system.indices(request.held)
    everything = [i for i in range(len(system.params)) if i not in held]
    blamed = [
        id
        for id in system.relations
        if id not in request.new
        and _solves(system, [r for r in system.relations if r != id], everything)
    ]
    edit_blamed = bool(held) and _solves(system, system.relations, range(len(system.params)))
    ids = sorted({*blamed, *(request.edited if edit_blamed else ())})
    subject = _subject(system.document, request)
    if not ids:
        ids = _neighbours(system, request)
        if not ids:
            return Error(
                code=ErrorCode.CONSTRAINT_CONFLICT,
                message=f"{subject} can't be satisfied by the geometry it refers to (a "
                "rectangle can't turn, and nothing may shrink to nothing)",
            )
        return Error(
            code=ErrorCode.CONSTRAINT_CONFLICT,
            message=f"{subject} can't hold together with {_names(system.document, ids)}; "
            "no single one of them is the cause on its own",
            ids=tuple(ids),
        )
    culprits = [id for id in ids if id not in request.edited]
    if edit_blamed and not culprits:
        message = f"{subject} can't move that way; the constraints hold it"
    else:
        message = f"{subject} conflicts with {_names(system.document, culprits)}"
    return Error(code=ErrorCode.CONSTRAINT_CONFLICT, message=message, ids=tuple(ids))


def _solves(system: System, relations: Sequence[EntityId], free: Iterable[int]) -> bool:
    values, ok = _newton(system, relations, list(free))
    return ok and _written(system, values) is not None


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
    everything = list(range(len(values)))
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


def implied(document: Document, constraint: Constraint, id: EntityId) -> bool:
    """Whether `constraint`, added to `document` under `id`, would be redundant there."""
    with_it = Document(
        entities=MappingProxyType({**document.entities, id: constraint}), next_id=document.next_id
    )
    for cluster in clusters(with_it):
        if id in cluster.relations:
            system = System.build(with_it, cluster)
            return _redundancy(system, system.values, frozenset({id})) is not None
    return False


# --- Status -------------------------------------------------------------------------------


def status(document: Document) -> SolveStatus:
    """Degrees of freedom and constraint health of the stored geometry."""
    entity_dof: dict[EntityId, int] = {
        id: len(PARAMS[type(e)]) for id, e in document.entities.items() if isinstance(e, GEOMETRY)
    }
    dof = sum(entity_dof.values())
    conflicting: list[EntityId] = []
    redundant: list[EntityId] = []
    for cluster in clusters(document):
        if not cluster.relations:
            continue
        system = System.build(document, cluster)
        values = system.values
        everything = list(range(len(values)))
        equations = system.equations(system.relations, values)
        residuals, gradients, owners = _evaluate(equations, system.frame(values, everything))
        tolerance = 1e3 * _tolerance(values)
        broken = {owners[i] for i, r in enumerate(residuals) if abs(r) > tolerance}
        basis = RowBasis(len(values))
        for i, row in enumerate(_dense(gradients, {i: i for i in everything})):
            basis.add(i, row)
        conflicting += broken
        redundant += {owners[i] for i in basis.dependent} - broken
        # Entities in a cluster can each still move without moving independently (two lines
        # joined at a corner), so the cluster counts as unknowns minus rank, not a sum.
        dof -= basis.rank
        for id in cluster.geometry:
            entity_dof[id] = basis.free_dimensions(system.slots[id])
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
        entity_dof=MappingProxyType(dict(sorted(entity_dof.items()))),
        conflicting=tuple(sorted(conflicting)),
        redundant=tuple(sorted(redundant)),
    )
