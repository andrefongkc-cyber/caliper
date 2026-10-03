"""Compute and apply Deltas between immutable Documents (ADR 0002)."""

from dataclasses import replace
from types import MappingProxyType

from caliper.contracts.commands import Delta
from caliper.contracts.document import Document, Entity, EntityId


class StaleDeltaError(RuntimeError):
    """A delta's before-state doesn't match the document it's applied to. Always a bug."""


def diff(before: Document, after: Document) -> Delta:
    old, new = before.entities, after.entities
    # Documents share the entity objects a change left alone, and an entity is always equal
    # to itself, so only entities that aren't the same object are compared by value.
    changed = [id for id, e in new.items() if (was := old.get(id)) is not e and was != e]
    removed = old.keys() - new.keys()
    # The feature list is small and rarely changes: kept whole, and only when it changed.
    features = before.features is not after.features and before.features != after.features
    return Delta(
        before=MappingProxyType({id: old[id] for id in (*changed, *removed) if id in old}),
        after=MappingProxyType({id: new[id] for id in changed}),
        next_id_before=before.next_id,
        next_id_after=after.next_id,
        features_before=before.features if features else None,
        features_after=after.features if features else None,
    )


def is_empty(delta: Delta) -> bool:
    return (
        not delta.before
        and not delta.after
        and delta.next_id_before == delta.next_id_after
        and delta.features_before == delta.features_after
    )


def apply(document: Document, delta: Delta) -> Document:
    """Apply `delta`, first checking that `document` is in the delta's before-state.

    The check is what makes a stale undo stack fail loudly instead of corrupting a document.
    """
    if document.next_id != delta.next_id_before:
        raise StaleDeltaError(
            f"next_id is {document.next_id}, delta expects {delta.next_id_before}"
        )
    if delta.features_before is not None and document.features != delta.features_before:
        raise StaleDeltaError("the part's features don't match the delta's before-state")
    for entity_id in sorted(delta.before.keys() | delta.after.keys()):
        if document.entities.get(entity_id) != delta.before.get(entity_id):
            raise StaleDeltaError(f"entity {entity_id!r} doesn't match the delta's before-state")
    entities: dict[EntityId, Entity] = dict(document.entities)
    for entity_id in delta.removed:
        del entities[entity_id]
    entities.update(delta.after)
    return replace(
        document,
        entities=MappingProxyType(entities),
        next_id=delta.next_id_after,
        features=document.features if delta.features_after is None else delta.features_after,
    )
