"""What the model is told about the document: a summary, not the document itself.

A request carries a small, deterministic picture of the sketch: counts, bounds, solve status,
the selection, and up to `limit` entities in their file form, chosen so the relevant ones come
first: the selection and any focus, then geometry near them, then the rest by id. The model
asks for more with the inspection tools. `changes_since` adds what changed between turns, so
the model hears about edits the user made, and proposals they accepted, without re-reading
everything.
"""

import re
from collections.abc import Collection, Iterable

from caliper.contracts.document import (
    AngleDimension,
    DistanceDimension,
    Document,
    EntityId,
    Geometry,
    RadialDimension,
)
from caliper.contracts.errors import Error
from caliper.contracts.queries import BoundingBox
from caliper.engine.document.delta import diff
from caliper.engine.io.canonical import JSON
from caliper.engine.io.codec import encode
from caliper.engine.queries import DocumentQueries

NEARBY_MARGIN = 0.25
"""How far past the focus's bounds counts as nearby, as a fraction of their larger side."""


def describe(
    document: Document,
    *,
    selection: Collection[EntityId] = (),
    focus: Iterable[EntityId] = (),
    limit: int = 40,
    changes_since: Document | None = None,
) -> dict[str, JSON]:
    queries = DocumentQueries(document, kernel=None)
    entities = document.entities
    counts: dict[str, int] = {}
    for entity in entities.values():
        counts[entity.kind] = counts.get(entity.kind, 0) + 1
    status = queries.solve_status()
    order = _order(document, queries, [*sorted(selection), *focus])
    shown = order[:limit]
    summary: dict[str, JSON] = {
        "units": "millimetres and degrees; y points up",
        "entity_count": len(entities),
        "kinds": dict(sorted(counts.items())),
        "bounds": _bounds(queries.bounding_box()),
        "solve_status": {
            "state": status.state.value,
            "dof": status.dof,
            "conflicting": list(status.conflicting),
            "redundant": list(status.redundant),
        },
        "selection": [str(id) for id in sorted(selection) if id in entities],
        "entities": [_entity(document, queries, id) for id in shown],
        "not_shown": len(order) - len(shown),
    }
    if changes_since is not None:
        delta = diff(changes_since, document)
        summary["changes_since_your_last_turn"] = {
            "added": [str(id) for id in _natural(delta.added)],
            "modified": [str(id) for id in _natural(delta.modified)],
            "removed": [str(id) for id in _natural(delta.removed)],
        }
    return summary


def _order(document: Document, queries: DocumentQueries, first: list[EntityId]) -> list[EntityId]:
    """The focus in the order given, then geometry near it, then everything else by id."""
    entities = document.entities
    ordered = list(dict.fromkeys(id for id in first if id in entities))
    shapes = [id for id in ordered if isinstance(entities[id], Geometry)]
    focus_box = queries.bounding_box(shapes) if shapes else None
    if isinstance(focus_box, BoundingBox):
        reach = NEARBY_MARGIN * max(focus_box.width, focus_box.height, 1.0)
        around = BoundingBox(
            x_min=focus_box.x_min - reach,
            y_min=focus_box.y_min - reach,
            x_max=focus_box.x_max + reach,
            y_max=focus_box.y_max + reach,
        )
        ordered += _natural(set(queries.entities_in_box(around, crossing=True)) - set(ordered))
    return ordered + _natural(entities.keys() - set(ordered))


def _entity(document: Document, queries: DocumentQueries, id: EntityId) -> JSON:
    entity = document.entities[id]
    data = encode(entity)
    assert isinstance(data, dict)
    summary: dict[str, JSON] = {"id": id, **data}
    if isinstance(entity, DistanceDimension | RadialDimension | AngleDimension):
        measured = queries.dimension_value(id)
        summary["measured"] = None if isinstance(measured, Error) else measured
    return summary


def _bounds(box: BoundingBox | Error) -> JSON:
    if isinstance(box, Error):
        return None
    return {"x_min": box.x_min, "y_min": box.y_min, "x_max": box.x_max, "y_max": box.y_max}


def _natural(ids: Iterable[EntityId]) -> list[EntityId]:
    """Ids in reading order: e2 before e10."""
    return sorted(
        ids, key=lambda id: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", id)]
    )
