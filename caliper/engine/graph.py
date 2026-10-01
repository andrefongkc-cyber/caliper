"""What depends on what, over a document's ids (core.md's recomputation graph, items 1 to 3;
ADR 0013).

The 2D layer is the solver's own, under these names: a dimension's or constraint's inputs are
the geometry it references (`sketch.references`), and the reverse index is
`sketch.referrers`, kept incrementally by identity as before. Features add the 3D layer: a
sketch is one node whose inputs are the geometry drawn in it, and an extrude reads its sketch
(and the geometry in `ids`), and builds on the solid of the extrude before it.

Features may read only features before them. That keeps the graph free of cycles by
construction, and `order` refuses a document that breaks the rule rather than recompute it.
"""

from collections.abc import Iterable, Mapping

from caliper.contracts.document import (
    Document,
    EntityId,
    Expectation,
    Extrude,
    Geometry,
    PartFeature,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.engine import part
from caliper.engine.constraints import sketch


def inputs(document: Document, id: EntityId) -> frozenset[EntityId]:
    """What `id` reads: an entity's references, a check's measured ids, a sketch's geometry,
    an extrude's sketch and profile and the solid it builds on. Nothing for an unknown id."""
    entity = document.entities.get(id)
    if isinstance(entity, Expectation):
        return frozenset((*entity.ids, *(ref.entity for ref in entity.refs)))
    if entity is not None:
        return frozenset(ref.entity for ref in sketch.references(entity))
    feature = part.feature(document, id)
    match feature:
        case Sketch():
            return frozenset(
                i
                for i, e in document.entities.items()
                if isinstance(e, Geometry) and e.sketch == feature.id
            )
        case Extrude():
            earlier = _solid_before(document, feature)
            return frozenset(
                (feature.sketch, *feature.ids, *(() if earlier is None else (earlier,)))
            )
    return frozenset()


def reads(feature: PartFeature) -> frozenset[EntityId]:
    """The features a feature names, which must come before it. An extrude's sketch: the solid
    it builds on is whatever comes before it, which no edit can make come after."""
    match feature:
        case Extrude(sketch=read):
            return frozenset({read})
    return frozenset()


def dependents(document: Document) -> Mapping[EntityId, frozenset[EntityId]]:
    """`inputs`, turned around: what reads each id."""
    found: dict[EntityId, set[EntityId]] = {
        target: set(ids) for target, ids in sketch.referrers(document).items()
    }
    for id, entity in document.entities.items():
        if isinstance(entity, Expectation):
            for target in inputs(document, id):
                found.setdefault(target, set()).add(id)
    for feature in document.features:
        for target in inputs(document, feature.id):
            found.setdefault(target, set()).add(feature.id)
    return {target: frozenset(ids) for target, ids in found.items()}


def affected(document: Document, changed: Iterable[EntityId]) -> frozenset[EntityId]:
    """Everything a change to `changed` reaches: the ids and all that depends on them, in
    turn. What a recompute has to look at; anything outside it keeps its result."""
    index = dependents(document)
    reached: set[EntityId] = set()
    waiting = list(changed)
    while waiting:
        id = waiting.pop()
        if id in reached:
            continue
        reached.add(id)
        waiting.extend(index.get(id, ()))
    return frozenset(reached)


def order(document: Document) -> tuple[EntityId, ...] | Error:
    """The features in the order to recompute them: the part's own order, once every feature
    is shown to read only features before it. `dependency.cycle` names any that don't."""
    seen: set[EntityId] = set()
    for feature in document.features:
        later = sorted(
            read
            for read in reads(feature)
            if read not in seen and part.feature(document, read) is not None
        )
        if later:
            return Error(
                code=ErrorCode.DEPENDENCY_CYCLE,
                message=(
                    f"{feature.id} reads {', '.join(later)}, which "
                    f"{'is' if len(later) == 1 else 'are'} not before it: features read only "
                    "what comes before them"
                ),
                ids=tuple(sorted({feature.id, *later})),
            )
        seen.add(feature.id)
    return tuple(feature.id for feature in document.features)


def _solid_before(document: Document, feature: PartFeature) -> EntityId | None:
    """The last extrude before `feature`: the solid it builds on."""
    last: EntityId | None = None
    for each in document.features:
        if each is feature:
            return last
        if isinstance(each, Extrude):
            last = each.id
    return last
