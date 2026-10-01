import json
import re
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
    CreateSketch,
    DeleteEntities,
    ModifyEntity,
    MoveEntities,
)
from caliper.contracts.document import (
    FIRST_SKETCH,
    DistanceOrientation,
    Document,
    EntityId,
    Feature,
    Plane,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import ErrorCode, LoadError
from caliper.engine.commands.bus import Bus
from caliper.engine.io import canonical, script, snapshot

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
V1 = FIXTURES / "v1"
"""Files written by schema 1, before sketch constraints, kept to test the migration."""
V3 = FIXTURES / "v3"
"""Every golden file as schema 3 wrote it, before the part (ADR 0011), kept to test the
migration: the fixtures by name, and the bench's expected files under `bench/`."""
ROOT = FIXTURES.parents[2]


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
        case "no-features":
            del data["document"]["features"]
        case "features-not-a-list":
            data["document"]["features"] = {"e0": "xy"}
        case "unknown-feature-kind":
            data["document"]["features"][0]["kind"] = "extrude"
        case "bad-plane":
            data["document"]["features"][0]["plane"] = "xw"
        case "feature-id-twice":
            data["document"]["features"].append({"id": "e0", "kind": "sketch", "plane": "xz"})
        case "feature-id-of-an-entity":
            data["document"]["features"].append({"id": "e1", "kind": "sketch", "plane": "xz"})
        case "no-such-sketch":
            entity["sketch"] = "e9"
        case "sketch-not-a-sketch":
            entity["sketch"] = "e1"
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
        (mutated("no-features"), "document: missing field(s): features"),
        (mutated("features-not-a-list"), "document.features: expected a list"),
        (mutated("unknown-feature-kind"), "document.features[0]: unknown kind 'extrude'"),
        (mutated("bad-plane"), "document.features[0].plane: plane must be one of: xy, xz, yz"),
        (mutated("feature-id-twice"), "document.features[1].id: id 'e0' is used twice"),
        (mutated("feature-id-of-an-entity"), "document.features[1].id: id 'e1' is used twice"),
        (mutated("no-such-sketch"), "document.entities.e1.sketch: no sketch 'e9'"),
        (
            mutated("sketch-not-a-sketch"),
            "document.entities.e1.sketch: 'e1' is a rectangle; geometry goes in a sketch",
        ),
    ],
)
def test_invalid_files_are_refused_with_a_reason(text: str, message: str) -> None:
    with pytest.raises(LoadError, match=re.escape(message)):
        snapshot.loads(text)


def test_files_and_scripts_raise_the_contract_load_error() -> None:
    # The old engine name is the same class, so a caller catching either catches both.
    assert canonical.LoadError is LoadError
    with pytest.raises(LoadError) as raised:
        snapshot.loads(mutated("negative-width"))
    assert type(raised.value) is LoadError
    assert [(e.code, e.field) for e in raised.value.errors] == [
        (ErrorCode.VALUE_NOT_POSITIVE, "document.entities.e1.width")
    ]
    for bad in ("{not json", '{"format": "caliper.script", "schema_version": 9}'):
        with pytest.raises(LoadError) as raised:
            script.loads(bad)
        assert type(raised.value) is LoadError


def test_migrations_run_in_order_on_load(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def recorded(version: int) -> snapshot.Migration:
        real = snapshot.MIGRATIONS[version]

        def migration(data: dict[str, object]) -> dict[str, object]:
            calls.append(version)
            return real(data)

        return migration

    expected = milestone_document()
    for version in (1, 2, 3):
        monkeypatch.setitem(snapshot.MIGRATIONS, version, recorded(version))
    assert snapshot.loads((V1 / "milestone.caliper").read_text()) == expected
    assert calls == [1, 2, 3]


def test_schema_1_files_gain_the_constraint_fields() -> None:
    """Migration 1 → 2: nothing was construction geometry, and every dimension was driven."""
    data = json.loads((V1 / "dimensioned.caliper").read_text())
    upgraded = snapshot.migrate(data, 1)
    entities = upgraded["document"]["entities"]  # type: ignore[index]
    assert upgraded["schema_version"] == snapshot.SCHEMA_VERSION
    assert entities["e1"] == {
        "construction": False,
        "corner": {"x": 0.0, "y": 0.0},
        "height": 50.0,
        "kind": "rectangle",
        "sketch": "e0",  # 3 -> 4: in the part's one sketch
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


def test_old_files_load_with_arc_start_angles_in_range() -> None:
    """Files written before the engine normalized `start_angle` hold it as it was given.
    Loading one, migrations included, stores each angle in [0, 360), as replay does now."""
    expected = FIXTURES / "arcs.caliper"
    # Schema 1, as the V1 engine wrote it: -90, 360, 450, -720.25 and -30.
    assert snapshot.load(V1 / "arcs.caliper") == snapshot.load(expected)
    assert snapshot.dumps(snapshot.load(V1 / "arcs.caliper")) == expected.read_text()
    # Schema 2, as the V1.5 engine wrote it before this change.
    data = json.loads(expected.read_text())
    data["schema_version"] = 2
    data["document"]["entities"]["e1"]["start_angle"] = -90.0
    data["document"]["entities"]["e2"]["start_angle"] = 360.0
    assert snapshot.loads(json.dumps(data)) == snapshot.load(expected)


def test_schema_2_files_load_unchanged_as_schema_3() -> None:
    """Migration 2 → 3 adds the `check` kind (C-1): a schema-2 file has none, so its data is
    the same, and it reads as the document the current engine writes."""
    start = ROOT / "bench" / "cases" / "constrained-plate-width-120"
    data = json.loads((start / "start.caliper").read_text())
    assert data["schema_version"] == 2
    upgraded = snapshot.MIGRATIONS[2](json.loads((start / "start.caliper").read_text()))
    assert upgraded == data
    read = snapshot.read((start / "start.caliper").read_text())
    assert read.schema_version == 2
    every = snapshot.migrate(json.loads((start / "start.caliper").read_text()), 2)
    assert snapshot.dumps(read.document) == canonical.dumps(every)  # type: ignore[arg-type]


# --- Migration 3 -> 4: the part (ADR 0011) ----------------------------------------------


def golden(old: Path) -> Path:
    """Where the schema-4 version of a kept schema-3 file lives."""
    if old.parent.name == "bench":
        return ROOT / "bench" / "cases" / old.stem / "expected.caliper"
    return FIXTURES / old.name


SCHEMA_3 = sorted(V3.glob("*.caliper")) + sorted((V3 / "bench").glob("*.caliper"))


BORN_AT_SCHEMA_4 = {FIXTURES / "two-sketches.caliper"}
"""Goldens first written at schema 4, which have no older version."""


def test_every_schema_3_golden_is_kept_for_the_migration() -> None:
    goldens = {*FIXTURES.glob("*.caliper"), *(ROOT / "bench" / "cases").glob("*/expected.caliper")}
    assert {golden(old) for old in SCHEMA_3} == goldens - BORN_AT_SCHEMA_4


@pytest.mark.parametrize("old", SCHEMA_3, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_schema_3_files_become_one_sketch_on_xy_byte_for_byte(old: Path) -> None:
    """Every golden file schema 3 wrote loads as the file the engine writes now: the same
    entities, ids, and next_id, in the part's one sketch. Replaying each script gives these
    same bytes (test_replay.py and the bench), so V1 replay is unchanged by the migration."""
    read = snapshot.read(old.read_text())
    assert read.schema_version == 3
    assert snapshot.dumps(read.document) == golden(old).read_text()


def test_migration_3_puts_geometry_in_the_first_sketch_and_nothing_else() -> None:
    """Geometry gains `sketch`; dimensions, constraints, and checks don't, since theirs is
    worked out or they belong to the part. Ids and next_id stay."""
    data = json.loads((V3 / "constraints.caliper").read_text())
    upgraded = snapshot.MIGRATIONS[3](json.loads((V3 / "constraints.caliper").read_text()))
    document = upgraded["document"]
    assert document["features"] == [{"id": "e0", "kind": "sketch", "plane": "xy"}]  # type: ignore[index]
    assert document["next_id"] == data["document"]["next_id"]  # type: ignore[index]
    entities = document["entities"]  # type: ignore[index]
    assert entities.keys() == data["document"]["entities"].keys()
    for id, entity in entities.items():
        old = data["document"]["entities"][id]
        if entity["kind"] in {"point", "line", "circle", "arc", "rectangle"}:
            assert entity == old | {"sketch": "e0"}
        else:
            assert entity == old


def test_a_schema_3_file_that_used_e0_gets_the_next_free_id_for_its_sketch() -> None:
    """`e0` was never allocated, but a caller could choose it: the sketch takes `e{next_id}`
    instead, and next_id moves past it, as allocating it would have."""
    data = json.loads((V3 / "milestone.caliper").read_text())
    data["document"]["entities"]["e0"] = data["document"]["entities"].pop("e1")
    document = snapshot.loads(json.dumps(data))
    assert [(f.id, f.plane) for f in document.features] == [(EntityId("e2"), Plane.XY)]
    assert document.next_id == 3
    assert document.entities[EntityId("e0")] == Rectangle(
        corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0, sketch=EntityId("e2")
    )


def test_migration_3_leaves_a_malformed_document_to_the_decoder() -> None:
    assert snapshot.MIGRATIONS[3]({"document": []}) == {"document": []}
    with pytest.raises(LoadError, match="document: expected an object"):
        snapshot.loads(
            json.dumps({**json.loads((V3 / "milestone.caliper").read_text()), "document": []})
        )


def test_a_part_with_two_sketches_round_trips_exactly(tmp_path: Path) -> None:
    bus = Bus()
    sketch = bus.execute(CreateSketch(plane=Plane.XZ))
    assert isinstance(sketch, Applied)
    (second,) = sketch.created_ids
    bus.execute(
        CreateRectangle(corner=Point2(x=0.0, y=0.0), width=10.0, height=5.0, sketch=FIRST_SKETCH)
    )
    bus.execute(CreateCircle(center=Point2(x=1.0, y=2.0), radius=3.0, sketch=second))
    path = tmp_path / "two.caliper"
    snapshot.save(bus.document, path)
    assert snapshot.load(path) == bus.document
    assert snapshot.dumps(snapshot.load(path)) == path.read_text()


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
