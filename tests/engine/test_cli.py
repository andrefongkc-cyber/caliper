"""`python -m caliper.engine inspect | export`, and `replay --history`."""

import json
import subprocess
import sys
from pathlib import Path

from caliper.contracts.document import Document
from caliper.engine.io import snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"

SCRIPT = {
    "format": "caliper.script",
    "schema_version": 1,
    "commands": [
        {"kind": "create_rectangle", "corner": {"x": 0, "y": 0}, "width": 100, "height": 50},
        {"kind": "create_circle", "center": {"x": 150, "y": 25}, "radius": 10},
        {
            "kind": "create_distance_dimension",
            "a": {"entity": "e1", "feature": "bottom_left"},
            "b": {"entity": "e1", "feature": "bottom_right"},
            "orientation": "horizontal",
            "offset": -10,
        },
        {
            "kind": "create_radial_dimension",
            "target": "e2",
            "measure": "diameter",
            "label_angle": 45,
        },
        {"kind": "modify_entity", "id": "e1", "changes": {"width": 120}},
    ],
}


def run(*args: str | Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "caliper.engine", *map(str, args)], capture_output=True
    )


def replayed(tmp_path: Path, *flags: str) -> Path:
    script = tmp_path / "part.script.json"
    script.write_text(json.dumps(SCRIPT))
    output = tmp_path / ("with-history.caliper" if flags else "part.caliper")
    result = run("replay", script, "-o", output, *flags)
    assert result.returncode == 0, result.stderr
    return output


# --- replay --history -------------------------------------------------------------------


def test_replay_can_record_the_resolved_commands(tmp_path: Path) -> None:
    read = snapshot.read_file(replayed(tmp_path, "--history"))
    assert read.history is not None
    assert [command.kind for command in read.history] == [c["kind"] for c in SCRIPT["commands"]]  # type: ignore[index]
    assert read.document == snapshot.load(replayed(tmp_path))
    first = json.loads(replayed(tmp_path, "--history").read_text())["history"][0]
    assert first["id"] == "e1"  # resolved: the allocated id is filled in
    assert first["width"] == 100.0


# --- inspect ----------------------------------------------------------------------------


def test_inspect_summarizes_a_file(tmp_path: Path) -> None:
    result = run("inspect", replayed(tmp_path, "--history"))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.decode().splitlines()
    assert lines[1:8] == [
        "  schema version  2",
        "  units           mm, deg",
        "  entities        4: 1 circle, 1 distance_dimension, 1 radial_dimension, 1 rectangle",
        "  next id         5",
        "  bounds          x 0.0 to 160.0, y 0.0 to 50.0 (160.0 x 50.0 mm)",
        "  sketch          under-constrained, 7 DOF remaining",
        "  history         5 commands",
    ]
    assert lines[9:] == [
        "  e1  rectangle           corner (0.0, 0.0), width 120.0, height 50.0  [dof 4]",
        "  e2  circle              center (150.0, 25.0), radius 10.0  [dof 3]",
        "  e3  distance_dimension  a e1.bottom_left, b e1.bottom_right, orientation horizontal"
        " = 120.0",
        "  e4  radial_dimension    target e2, measure diameter = 20.0",
    ]


def test_inspect_an_empty_document(tmp_path: Path) -> None:
    path = tmp_path / "empty.caliper"
    path.write_text(snapshot.dumps(Document.empty()))
    result = run("inspect", path)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.decode().splitlines()
    assert "  entities        0" in lines
    assert "  bounds          none" in lines


def test_inspect_refuses_an_invalid_file_with_the_reason(tmp_path: Path) -> None:
    data = json.loads((FIXTURES / "milestone.caliper").read_text())
    data["document"]["entities"]["e1"]["width"] = -1.0
    path = tmp_path / "bad.caliper"
    path.write_text(json.dumps(data))
    result = run("inspect", path)
    assert result.returncode == 1
    assert b"document.entities.e1.width: width must be greater than 0" in result.stderr
    assert run("inspect", tmp_path / "missing.caliper").returncode == 1


# --- export -----------------------------------------------------------------------------


def test_export_strips_history_and_says_so(tmp_path: Path) -> None:
    exported = tmp_path / "for-supplier.caliper"
    result = run("export", replayed(tmp_path, "--history"), "-o", exported)
    assert result.returncode == 0, result.stderr
    assert result.stderr == b"removed the history section (5 commands)\n"
    assert "history" not in json.loads(exported.read_text())
    assert exported.read_bytes() == replayed(tmp_path).read_bytes()


def test_export_writes_canonical_bytes_to_stdout(tmp_path: Path) -> None:
    data = json.loads((FIXTURES / "milestone.caliper").read_text())
    data["document"]["entities"]["e1"]["width"] = 120  # hand-written int
    messy = tmp_path / "messy.caliper"
    messy.write_text(json.dumps(data, indent=None))
    result = run("export", messy)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (FIXTURES / "milestone.caliper").read_bytes()
    assert result.stderr == b""
