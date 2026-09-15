import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import CreateCircle, CreateRectangle
from caliper.contracts.document import Document, EntityId, Point2, Rectangle
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot
from caliper.engine.io.canonical import LoadError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def milestone_document() -> Document:
    return snapshot.load(FIXTURES / "milestone.caliper")


def test_canonical_encoding_matches_adr_0005_exactly() -> None:
    bus = Bus()
    bus.execute(CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0))
    assert snapshot.dumps(bus.document) == (FIXTURES / "milestone.caliper").read_text()


def test_saved_file_reopens_with_the_same_width(tmp_path: Path) -> None:
    path = tmp_path / "part.caliper"
    snapshot.save(milestone_document(), path)
    reopened = snapshot.load(path)
    assert reopened.entities[EntityId("e1")] == Rectangle(
        corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0
    )
    assert b"\r" not in path.read_bytes()
    assert [p.name for p in tmp_path.iterdir()] == ["part.caliper"]


coordinates = st.floats(min_value=-1e6, max_value=1e6)
sizes = st.floats(min_value=1e-6, max_value=1e6)
points = st.builds(Point2, x=coordinates, y=coordinates)
creates = st.one_of(
    st.builds(CreateRectangle, corner=points, width=sizes, height=sizes),
    st.builds(CreateCircle, center=points, radius=sizes),
)


@given(commands=st.lists(creates, max_size=10))
def test_round_trip_is_exact(commands: list[CreateRectangle | CreateCircle]) -> None:
    bus = Bus()
    for command in commands:
        bus.execute(command)
    text = snapshot.dumps(bus.document)
    assert snapshot.loads(text) == bus.document
    assert snapshot.dumps(snapshot.loads(text)) == text


def test_hand_written_ints_load_as_floats() -> None:
    data = json.loads(snapshot.dumps(milestone_document()))
    data["document"]["entities"]["e1"]["width"] = 120
    document = snapshot.loads(json.dumps(data))
    assert document == milestone_document()


def mutated(change: str) -> str:
    """The milestone file with one deliberate problem."""
    data = json.loads((FIXTURES / "milestone.caliper").read_text())
    entity = data["document"]["entities"]["e1"]
    match change:
        case "newer-schema":
            data["schema_version"] = 2
        case "wrong-format":
            data["format"] = "something.else"
        case "unknown-top-level":
            data["extra"] = True
        case "wrong-units":
            data["units"]["length"] = "in"
        case "unknown-kind":
            entity["kind"] = "spline"
        case "unknown-field":
            entity["color"] = "red"
        case "missing-field":
            del entity["height"]
        case "negative-width":
            entity["width"] = -1.0
        case "dangling-reference":
            data["document"]["entities"]["e2"] = {
                "kind": "radial_dimension",
                "target": "e7",
                "measure": "radius",
                "label_angle": 0.0,
            }
        case "bad-next-id":
            data["document"]["next_id"] = 0
    return json.dumps(data)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{not json", "invalid JSON"),
        ('{"a": NaN}', "NaN is not allowed"),
        ('{"a": 1, "a": 2}', "duplicate key"),
        (mutated("newer-schema"), "update Caliper"),
        (mutated("wrong-format"), "not a Caliper document"),
        (mutated("unknown-top-level"), "unknown top-level field(s): extra"),
        (mutated("wrong-units"), "units must be"),
        (mutated("unknown-kind"), "unknown kind 'spline'"),
        (mutated("unknown-field"), "unknown field(s): color"),
        (mutated("missing-field"), "missing field(s): height"),
        (mutated("negative-width"), "document.entities.e1.width: width must be greater than 0"),
        (mutated("dangling-reference"), "document.entities.e2.target: no entity 'e7'"),
        (mutated("bad-next-id"), "next_id: must be a positive integer"),
    ],
)
def test_invalid_files_are_refused_with_a_reason(text: str, message: str) -> None:
    with pytest.raises(LoadError, match=message.replace("(", r"\(").replace(")", r"\)")):
        snapshot.loads(text)


def test_migrations_run_in_order_on_load(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def to_v2(data: dict[str, object]) -> dict[str, object]:
        calls.append(1)
        return data

    expected = milestone_document()
    monkeypatch.setattr(snapshot, "SCHEMA_VERSION", 2)
    monkeypatch.setitem(snapshot.MIGRATIONS, 1, to_v2)
    assert snapshot.loads((FIXTURES / "milestone.caliper").read_text()) == expected
    assert calls == [1]
