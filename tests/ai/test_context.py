"""What the model is told about a sketch: small, relevant first, deterministic."""

import json

from caliper.ai.context import describe
from caliper.contracts.commands import (
    CreateCircle,
    CreateConstraint,
    CreateDimension,
    CreateRectangle,
)
from caliper.contracts.document import ConstraintType, Document, EntityId, Feature, Point2, Ref
from caliper.engine.commands.bus import Bus


def sketch() -> Bus:
    """e1 a 100 x 50 plate at the origin, e2 a hole in it, e3 a far circle, e4 its fix,
    e5 a dimension across the plate's bottom."""
    bus = Bus()
    bus.execute(CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50))
    bus.execute(CreateCircle(center=Point2(x=20, y=20), radius=3))
    bus.execute(CreateCircle(center=Point2(x=900, y=900), radius=5))
    bus.execute(
        CreateConstraint(
            type=ConstraintType.FIX, refs=(Ref(entity=EntityId("e3"), feature=Feature.CENTER),)
        )
    )
    bus.execute(
        CreateDimension(
            refs=(Ref(entity=EntityId("e1"), feature=Feature.BOTTOM),),
            placement=Point2(x=50, y=-10),
        )
    )
    return bus


def test_an_empty_sketch_is_described_without_errors() -> None:
    summary = describe(Document.empty())
    assert summary["entity_count"] == 0
    assert summary["bounds"] is None
    assert summary["entities"] == []
    assert summary["solve_status"]["state"] == "fully"  # type: ignore[index]


def test_entities_come_in_file_form_with_what_dimensions_measure() -> None:
    summary = describe(sketch().document)
    entities = {e["id"]: e for e in summary["entities"]}  # type: ignore[union-attr, index]
    assert entities["e1"]["kind"] == "rectangle"
    assert entities["e1"]["width"] == 100.0
    assert entities["e4"]["type"] == "fix"
    assert entities["e5"]["measured"] == 100.0
    assert summary["kinds"] == {
        "circle": 2,
        "constraint": 1,
        "distance_dimension": 1,
        "rectangle": 1,
    }
    assert summary["bounds"] == {"x_min": 0.0, "y_min": 0.0, "x_max": 905.0, "y_max": 905.0}


def test_the_selection_comes_first_then_what_is_near_it_then_the_rest() -> None:
    summary = describe(sketch().document, selection={EntityId("e2")}, limit=2)
    ids = [e["id"] for e in summary["entities"]]  # type: ignore[union-attr, index]
    assert ids == ["e2", "e1"]  # the hole, then the plate around it; not the far circle
    assert summary["selection"] == ["e2"]
    assert summary["not_shown"] == 3


def test_a_focus_on_something_other_than_geometry_still_works() -> None:
    summary = describe(sketch().document, focus=[EntityId("e5"), EntityId("e9")], limit=1)
    assert [e["id"] for e in summary["entities"]] == ["e5"]  # type: ignore[union-attr, index]


def test_the_order_is_reading_order_without_a_focus() -> None:
    bus = Bus()
    for x in range(12):
        bus.execute(CreateCircle(center=Point2(x=10.0 * x, y=0), radius=1))
    summary = describe(bus.document, limit=12)
    ids = [e["id"] for e in summary["entities"]]  # type: ignore[union-attr, index]
    assert ids == [f"e{n}" for n in range(1, 13)]  # e2 before e10


def test_changes_since_the_last_turn_are_listed() -> None:
    bus = sketch()
    before = bus.document
    bus.execute(CreateCircle(center=Point2(x=50, y=25), radius=2))
    summary = describe(bus.document, changes_since=before)
    assert summary["changes_since_your_last_turn"] == {
        "added": ["e6"],
        "modified": [],
        "removed": [],
    }


def test_the_same_document_gives_the_same_bytes() -> None:
    first = json.dumps(describe(sketch().document, selection={EntityId("e1")}), sort_keys=True)
    second = json.dumps(describe(sketch().document, selection={EntityId("e1")}), sort_keys=True)
    assert first == second
