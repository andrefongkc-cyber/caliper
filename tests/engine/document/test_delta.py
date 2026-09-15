from types import MappingProxyType

import pytest

from caliper.contracts.document import Circle, Document, EntityId, Point2
from caliper.engine.document.delta import StaleDeltaError, apply, diff, is_empty

E1 = EntityId("e1")
SMALL = Circle(center=Point2(x=0.0, y=0.0), radius=1.0)
LARGE = Circle(center=Point2(x=0.0, y=0.0), radius=2.0)


def document(**entities: Circle) -> Document:
    items = {EntityId(k): v for k, v in entities.items()}
    return Document(entities=MappingProxyType(items), next_id=len(items) + 1)


def test_diff_then_apply_reaches_the_target_and_inverse_returns() -> None:
    before, after = document(e1=SMALL), document(e1=LARGE, e2=SMALL)
    delta = diff(before, after)
    assert (delta.modified, delta.added, delta.removed) == ({E1}, {EntityId("e2")}, set())
    assert apply(before, delta) == after
    assert apply(after, delta.inverted()) == before


def test_identical_documents_give_an_empty_delta() -> None:
    assert is_empty(diff(document(e1=SMALL), document(e1=SMALL)))


def test_applying_to_a_document_in_the_wrong_state_fails_loudly() -> None:
    delta = diff(document(e1=SMALL), document(e1=LARGE))
    with pytest.raises(StaleDeltaError, match="e1"):
        apply(document(e1=LARGE), delta)
