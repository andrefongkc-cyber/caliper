"""Compute and apply Deltas between immutable Documents (ADR 0002)."""

from types import MappingProxyType

from caliper.contracts.commands import Delta
from caliper.contracts.document import Document, Entity, EntityId


class StaleDeltaError(RuntimeError):
    """A delta's before-state doesn't match the document it's applied to. Always a bug."""


def diff(before: Document, after: Document) -> Delta:
    ids = before.entities.keys() | after.entities.keys()
    changed = {i for i in ids if before.entities.get(i) != after.entities.get(i)}
    return Delta(
        before=MappingProxyType({i: before.entities[i] for i in changed if i in before.entities}),
        after=MappingProxyType({i: after.entities[i] for i in changed if i in after.entities}),
        next_id_before=before.next_id,
        next_id_after=after.next_id,
    )


def is_empty(delta: Delta) -> bool:
    return not delta.before and not delta.after and delta.next_id_before == delta.next_id_after


def apply(document: Document, delta: Delta) -> Document:
    """Apply `delta`, first checking that `document` is in the delta's before-state.

    The check is what makes a stale undo stack fail loudly instead of corrupting a document.
    """
    if document.next_id != delta.next_id_before:
        raise StaleDeltaError(
            f"next_id is {document.next_id}, delta expects {delta.next_id_before}"
        )
    for entity_id in sorted(delta.before.keys() | delta.after.keys()):
        if document.entities.get(entity_id) != delta.before.get(entity_id):
            raise StaleDeltaError(f"entity {entity_id!r} doesn't match the delta's before-state")
    entities: dict[EntityId, Entity] = dict(document.entities)
    for entity_id in delta.removed:
        del entities[entity_id]
    entities.update(delta.after)
    return Document(entities=MappingProxyType(entities), next_id=delta.next_id_after)
