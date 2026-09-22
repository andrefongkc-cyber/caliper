import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateCircle,
    CreateDistanceDimension,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    MoveEntities,
)
from caliper.contracts.document import (
    DistanceOrientation,
    Document,
    EntityId,
    Feature,
    Point2,
    Rectangle,
    Ref,
)
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot
from caliper.engine.io.canonical import LoadError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
V1 = FIXTURES / "v1"
"""Files written by schema 1, before sketch constraints, kept to test the migration."""


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
            data["schema_version"] = snapshot.SCHEMA_VERSION + 1
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
    real = snapshot.MIGRATIONS[1]

    def to_v2(data: dict[str, object]) -> dict[str, object]:
        calls.append(1)
        return real(data)

    def to_v3(data: dict[str, object]) -> dict[str, object]:
        calls.append(2)
        return data

    expected = milestone_document()
    monkeypatch.setattr(snapshot, "SCHEMA_VERSION", 3)
    monkeypatch.setitem(snapshot.MIGRATIONS, 1, to_v2)
    monkeypatch.setitem(snapshot.MIGRATIONS, 2, to_v3)
    assert snapshot.loads((V1 / "milestone.caliper").read_text()) == expected
    assert calls == [1, 2]


def test_schema_1_files_gain_the_constraint_fields() -> None:
    """Migration 1 → 2: nothing was construction geometry, and every dimension was driven."""
    data = json.loads((V1 / "dimensioned.caliper").read_text())
    upgraded = snapshot.migrate(data, 1)
    entities = upgraded["document"]["entities"]  # type: ignore[index]
    assert upgraded["schema_version"] == 2
    assert entities["e1"] == {
        "construction": False,
        "corner": {"x": 0.0, "y": 0.0},
        "height": 50.0,
        "kind": "rectangle",
        "width": 120.0,
    }
    assert entities["e2"] == {
        "a": {"entity": "e1", "feature": "bottom_left"},
        "b": {"entity": "e1", "feature": "bottom_right"},
        "kind": "distance_dimension",
        "offset": -10.0,
        "orientation": "horizontal",
        "value": None,
    }
    # Loading the old file gives the same document as the migrated bench expectation.
    current = FIXTURES.parents[2] / "bench" / "cases" / "dimension-bottom-edge"
    assert snapshot.load(V1 / "dimensioned.caliper") == snapshot.load(current / "expected.caliper")
    assert (
        snapshot.dumps(snapshot.load(V1 / "dimensioned.caliper"))
        == (current / "expected.caliper").read_text()
    )


def test_every_schema_version_below_the_current_one_has_a_migration() -> None:
    assert set(snapshot.MIGRATIONS) == set(range(1, snapshot.SCHEMA_VERSION))


def test_read_reports_the_version_a_file_was_written_with() -> None:
    read = snapshot.read((V1 / "milestone.caliper").read_text())
    assert read.schema_version == 1
    assert read.document == milestone_document()


# --- History ----------------------------------------------------------------------------


def resolved_history() -> tuple[Document, list[Command]]:
    bus = Bus()
    commands: list[Command] = [
        CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50),  # type: ignore[arg-type]
        CreateCircle(center=Point2(x=150.0, y=25.0), radius=10.0),
        CreateDistanceDimension(
            a=Ref(entity=EntityId("e1"), feature=Feature.CENTER),
            b=Ref(entity=EntityId("e2"), feature=Feature.CENTER),
            orientation=DistanceOrientation.HORIZONTAL,
            offset=4.0,
        ),
        ModifyEntity(id=EntityId("e1"), changes={"width": 120, "corner": Point2(x=1.0, y=2.0)}),
        MoveEntities(ids=(EntityId("e1"), EntityId("e1")), dx=1, dy=2),  # type: ignore[arg-type]
        DeleteEntities(ids=(EntityId("e2"),)),
    ]
    resolved = []
    for command in commands:
        result = bus.execute(command)
        assert isinstance(result, Applied), result
        resolved.append(result.command)
    return bus.document, resolved


def test_history_is_off_by_default() -> None:
    document, _ = resolved_history()
    text = snapshot.dumps(document)
    assert "history" not in json.loads(text)
    assert snapshot.read(text).history is None


def test_history_round_trips_the_resolved_commands(tmp_path: Path) -> None:
    document, history = resolved_history()
    path = tmp_path / "with-history.caliper"
    snapshot.save(document, path, history=history)
    read = snapshot.read_file(path)
    assert read.document == document
    assert read.history == tuple(history)
    assert snapshot.load(path) == document
    assert snapshot.dumps(read.document, history=read.history) == path.read_text()
    assert json.loads(path.read_text())["history"][4] == {
        "dx": 1.0,
        "dy": 2.0,
        "ids": ["e1"],
        "kind": "move_entities",
    }


@pytest.mark.parametrize(
    ("history", "message"),
    [
        ({"kind": "create_circle"}, "history: must be a list of commands"),
        (
            [
                {"kind": "create_circle", "center": {"x": 0.0, "y": 0.0}, "radius": 1.0},
                {"kind": "paint"},
            ],
            "history\\[1\\]: unknown kind 'paint'",
        ),
    ],
    ids=["not a list", "unknown command"],
)
def test_malformed_history_is_refused(history: object, message: str) -> None:
    data = json.loads((FIXTURES / "milestone.caliper").read_text())
    data["history"] = history
    with pytest.raises(LoadError, match=message):
        snapshot.loads(json.dumps(data))
