from types import MappingProxyType

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import Delta
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


def all_ids_diff(before: Document, after: Document) -> Delta:
    """The diff before it compared by identity first, kept as the reference."""
    ids = before.entities.keys() | after.entities.keys()
    changed = {i for i in ids if before.entities.get(i) != after.entities.get(i)}
    return Delta(
        before=MappingProxyType({i: before.entities[i] for i in changed if i in before.entities}),
        after=MappingProxyType({i: after.entities[i] for i in changed if i in after.entities}),
        next_id_before=before.next_id,
        next_id_after=after.next_id,
    )


circles = st.builds(
    lambda x, r: Circle(center=Point2(x=x, y=0.0), radius=r),
    st.sampled_from([0.0, 1.0]),
    st.sampled_from([1.0, 2.0]),
)


@given(
    before=st.dictionaries(st.sampled_from(["e1", "e2", "e3", "e4"]), circles),
    data=st.data(),
)
def test_diff_matches_comparing_every_id_by_value(
    before: dict[str, Circle], data: st.DataObject
) -> None:
    # The next document keeps some entities as the same objects, replaces some with equal
    # copies (not a change), changes some, drops some, and adds some.
    after: dict[str, Circle] = {}
    for id, entity in before.items():
        match data.draw(st.sampled_from(["same", "copy", "other", "drop"])):
            case "same":
                after[id] = entity
            case "copy":
                after[id] = Circle(center=entity.center, radius=entity.radius)
            case "other":
                after[id] = data.draw(circles)
    after |= data.draw(st.dictionaries(st.sampled_from(["e5", "e6"]), circles))
    old = Document(
        entities=MappingProxyType({EntityId(k): v for k, v in before.items()}), next_id=7
    )
    new = Document(entities=MappingProxyType({EntityId(k): v for k, v in after.items()}), next_id=8)
    assert diff(old, new) == all_ids_diff(old, new)
