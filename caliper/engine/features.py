"""The part's solid, worked out from its features in order (ADR 0013).

Nothing here is stored: a solid is kernel output, which differs across platforms in the last
bits (ADR 0005), so it is recomputed from the features whenever it's asked for, and only
where something it reads has changed. Each step is cached by the identity of what it read,
as the 2D solver caches clusters and checks (Performance V2):

- an extrude's own prism by the kernel, the extrude, its sketch, and each geometry entity
  in its profile, the very objects;
- the solid after it by the solid it builds on and that prism.

Documents share every entity a change left alone, and undo puts the old objects back, so a
width change recomputes one prism and what's built on it; a dimension's label moving
recomputes nothing; and undoing the width finds the old prism waiting.

A feature that fails gives its reason, and the features after it `feature.failed` without
being recomputed: what they would build on is missing.
"""

import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from caliper.contracts.document import (
    Document,
    EntityId,
    Extrude,
    ExtrudeOperation,
    Geometry,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.kernel import Kernel, KernelError, Shape
from caliper.contracts.queries import BoundingBox3, Mesh
from caliper.engine import graph, part, profiles
from caliper.engine.document.recent import Recent


@dataclass(frozen=True, slots=True)
class Result:
    """The part as it stands after one feature: its solid, or why there isn't one."""

    solid: Shape | None
    """None before any extrude, or when this feature or one before it failed."""
    error: Error | None = None


def profile(document: Document, extrude: Extrude) -> profiles.Profile | Error:
    """The closed profile an extrude sweeps, or why its geometry isn't one."""
    chosen = _chosen(document, extrude)
    return chosen if isinstance(chosen, Error) else profiles.find(chosen)


def _chosen(document: Document, extrude: Extrude) -> list[tuple[EntityId, Geometry]] | Error:
    """The geometry an extrude sweeps, in id order: `ids`, or the sketch's own."""
    sketch = part.feature(document, extrude.sketch)
    if not isinstance(sketch, Sketch):
        return Error(
            code=ErrorCode.ENTITY_NOT_FOUND,
            message=f"{extrude.id} reads sketch {extrude.sketch}, which the part doesn't have",
            field="sketch",
        )
    chosen: list[tuple[EntityId, Geometry]] = []
    if extrude.ids:
        for id in extrude.ids:
            entity = document.entities.get(id)
            if entity is None:
                return _error(ErrorCode.ENTITY_NOT_FOUND, f"no entity {id!r}", id)
            if not isinstance(entity, Geometry):
                return _error(
                    ErrorCode.ENTITY_WRONG_KIND,
                    f"{id!r} is a {entity.kind}; a profile is geometry",
                    id,
                )
            if entity.sketch != sketch.id:
                message = f"{id!r} is in sketch {entity.sketch}, not {sketch.id}"
                return _error(ErrorCode.SKETCH_MIXED, message, id)
            if entity.construction:
                message = f"{id!r} is construction geometry, which isn't part of a profile"
                return _error(ErrorCode.PROFILE_CONSTRUCTION, message, id)
            chosen.append((id, entity))
    else:
        chosen = sorted(
            (id, e)
            for id, e in document.entities.items()
            if isinstance(e, Geometry) and e.sketch == sketch.id and not e.construction
        )
        if not chosen:
            return Error(
                code=ErrorCode.SELECTION_EMPTY,
                message=f"sketch {sketch.id} has nothing to extrude: draw a closed profile in it",
                field="sketch",
            )
    return chosen


def solids(document: Document, kernel: Kernel) -> Mapping[EntityId, Result]:
    """Each feature's result, in order. The same mapping for the same document and kernel."""
    remembered = _RESULTS.get(document)
    if remembered is not None and remembered[0] is kernel:
        return remembered[1]
    found: dict[EntityId, Result] = {}
    ordered = graph.order(document)
    if isinstance(ordered, Error):
        for feature in document.features:
            found[feature.id] = Result(solid=None, error=ordered)
        _RESULTS.put(document, (kernel, found))
        return found
    solid: Shape | None = None
    failed: EntityId | None = None
    for feature in document.features:
        if not isinstance(feature, Extrude):
            found[feature.id] = Result(solid=None if failed else solid)
            continue
        if failed is not None:
            found[feature.id] = Result(
                solid=None,
                error=Error(
                    code=ErrorCode.FEATURE_FAILED,
                    message=f"{failed} failed, so {feature.id} wasn't worked out",
                    ids=(failed,),
                ),
            )
            continue
        made = _built(document, feature, solid, kernel)
        if isinstance(made, Error):
            failed, solid = feature.id, None
            found[feature.id] = Result(solid=None, error=made)
        else:
            solid = made
            found[feature.id] = Result(solid=made)
    _RESULTS.put(document, (kernel, found))
    return found


def solid(document: Document, kernel: Kernel, ids: Sequence[EntityId] = ()) -> Shape | Error:
    """The part's solid after its last feature, or after the one feature in `ids`."""
    results = solids(document, kernel)
    if len(ids) > 1:
        return Error(
            code=ErrorCode.VALUE_OUT_OF_RANGE,
            message="name one feature, or none for the whole part",
            field="ids",
        )
    if ids:
        id = ids[0]
        if id not in results:
            if id in document.entities:
                kind = document.entities[id].kind
                return _error(ErrorCode.ENTITY_WRONG_KIND, f"{id!r} is a {kind}, not a feature", id)
            return _error(ErrorCode.ENTITY_NOT_FOUND, f"no feature {id!r}", id)
        result = results[id]
    elif document.features:
        result = results[document.features[-1].id]
    else:
        result = Result(solid=None)
    if result.error is not None:
        return result.error
    if result.solid is None:
        return Error(
            code=ErrorCode.SELECTION_EMPTY,
            message="the part has no solid yet: extrude a sketch",
            field="ids",
        )
    return result.solid


def _built(
    document: Document, extrude: Extrude, before: Shape | None, kernel: Kernel
) -> Shape | Error:
    """The solid after `extrude`: its prism joined to or cut from `before`. A prism found
    for the same objects was made from a closed profile, so the profile isn't looked for
    again."""
    chosen = _chosen(document, extrude)
    if isinstance(chosen, Error):
        return chosen
    sketch = part.feature(document, extrude.sketch)
    assert isinstance(sketch, Sketch)
    geometry = tuple(entity for _, entity in chosen)
    prism = _PRISMS.get((kernel, extrude, sketch, *geometry))
    try:
        if prism is None:
            found = profiles.find(chosen)
            if isinstance(found, Error):
                return found
            face = kernel.make_face(found.outer, found.holes)
            on = part.frame(sketch.plane)
            if extrude.reversed:
                on = part.moved(on, -extrude.depth)
            prism = kernel.extrude(face, on, extrude.depth)
            _PRISMS.put((kernel, extrude, sketch, *geometry), prism)
        if before is None:
            if extrude.operation is ExtrudeOperation.REMOVE:
                return Error(
                    code=ErrorCode.VALUE_OUT_OF_RANGE,
                    message=f"{extrude.id} removes, but there's no solid before it to remove from",
                    field="operation",
                )
            return prism
        key = (kernel, before, prism, extrude.operation)
        combined = _SOLIDS.get(key)
        if combined is None:
            if extrude.operation is ExtrudeOperation.ADD:
                combined = kernel.union(before, prism)
            else:
                combined = kernel.cut(before, prism)
            _SOLIDS.put(key, combined)
        return combined
    except KernelError as e:
        return Error(code=e.code, message=f"{extrude.id}: {e}", ids=(extrude.id,))


def mesh(kernel: Kernel, solid: Shape, tolerance: float) -> Mesh:
    """`kernel.mesh`, kept for the solid while it's the same object: drawing asks again on
    every change, and most changes leave the solid alone."""
    meshes = _MESHES.get((kernel, solid))
    if meshes is None:
        meshes = {}
        _MESHES.put((kernel, solid), meshes)
    if tolerance not in meshes:
        meshes[tolerance] = kernel.mesh(solid, tolerance)
    return meshes[tolerance]


def properties(kernel: Kernel, solid: Shape) -> tuple[float, BoundingBox3 | None]:
    """`solid`'s volume and bounding box (None when it's empty), kept while it's the same
    object. The Part panel, the status bar, and the 3D view each ask after a change, and most
    changes leave the solid alone (Performance V2.2, Perf-3). A `KernelError` isn't kept."""
    found = _PROPERTIES.get((kernel, solid))
    if found is None:
        volume = kernel.volume(solid)
        try:
            box: BoundingBox3 | None = kernel.bounding_box_3d(solid)
        except KernelError as e:
            if e.code is not ErrorCode.SELECTION_EMPTY:
                raise
            box = None
        found = (volume, box)
        _PROPERTIES.put((kernel, solid), found)
    return found


def _error(code: ErrorCode, message: str, id: EntityId) -> Error:
    return Error(code=code, message=message, field="ids", ids=(id,))


class _ByIdentity[V]:
    """The last few values worked out, each kept with the very objects it was worked out from,
    so a key matches only those objects, not equal ones made since, and an object's id can't
    be reused while its entry is held."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._entries: OrderedDict[tuple[int, ...], tuple[tuple[object, ...], V]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[object, ...]) -> V | None:
        ids = tuple(map(id, key))
        with self._lock:
            entry = self._entries.get(ids)
            if entry is None or any(a is not b for a, b in zip(entry[0], key, strict=True)):
                return None
            self._entries.move_to_end(ids)
            return entry[1]

    def put(self, key: tuple[object, ...], value: V) -> None:
        ids = tuple(map(id, key))
        with self._lock:
            self._entries[ids] = (key, value)
            self._entries.move_to_end(ids)
            while len(self._entries) > self._size:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_PRISMS: _ByIdentity[Shape] = _ByIdentity(64)
"""Each extrude's own prism, by the kernel, the extrude, its sketch, and its geometry."""
_SOLIDS: _ByIdentity[Shape] = _ByIdentity(64)
"""The solid after each extrude, by the kernel, the solid before it, its prism, and how."""
_MESHES: _ByIdentity[dict[float, Mesh]] = _ByIdentity(16)
"""Each solid's meshes, by tolerance."""
_PROPERTIES: _ByIdentity[tuple[float, BoundingBox3 | None]] = _ByIdentity(64)
"""Each solid's volume and bounding box."""
_RESULTS: Recent[tuple[Kernel, Mapping[EntityId, Result]]] = Recent(8)
"""Every feature's result for the last few documents asked about."""


def forget() -> None:
    """Drop everything cached: for tests that count a kernel's work from nothing."""
    _PRISMS.clear()
    _SOLIDS.clear()
    _MESHES.clear()
    _PROPERTIES.clear()
    _RESULTS.clear()
