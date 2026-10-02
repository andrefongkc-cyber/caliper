"""Invariant 4: every command runs headlessly, and replay output is byte-identical.

The expected file is committed, and CI compares against it on Linux and macOS, so this also
checks that output is identical across platforms.
"""

import json
import subprocess
import sys
from pathlib import Path

from caliper.contracts.commands import Applied, CreateExtrude, CreateRectangle, CreateSketch
from caliper.contracts.document import Plane, Point2
from caliper.engine import part
from caliper.engine.commands.bus import Bus
from caliper.engine.io import script, snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRIPT = FIXTURES / "milestone.script.json"
EXPECTED = FIXTURES / "milestone.caliper"


def replay(*args: str | Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "caliper.engine", "replay", *map(str, args)], capture_output=True
    )


def test_milestone_replays_to_identical_bytes() -> None:
    first, second = replay(SCRIPT), replay(SCRIPT)
    assert first.returncode == 0, first.stderr
    assert first.stdout == EXPECTED.read_bytes()
    assert second.stdout == first.stdout


def test_moves_and_deletes_replay_to_identical_bytes() -> None:
    """Move a rectangle and a dimension, delete a circle and the two dimensions on it."""
    script, expected = FIXTURES / "edits.script.json", FIXTURES / "edits.caliper"
    first, second = replay(script), replay(script)
    assert first.returncode == 0, first.stderr
    assert first.stdout == expected.read_bytes()
    assert second.stdout == first.stdout


def test_a_fillet_replays_to_identical_bytes() -> None:
    """Round a corner headlessly: two lines trimmed back, one arc added."""
    script, expected = FIXTURES / "fillet.script.json", FIXTURES / "fillet.caliper"
    first, second = replay(script), replay(script)
    assert first.returncode == 0, first.stderr
    assert first.stdout == expected.read_bytes()
    assert second.stdout == first.stdout


def test_arcs_replay_to_identical_bytes_with_start_angles_in_range() -> None:
    """Arcs started at -90, 360, 450 and -720.25 degrees, and one edited to -30, are stored at
    270, 0, 90, 359.75 and 330: one form per direction, whatever the script said."""
    script, expected = FIXTURES / "arcs.script.json", FIXTURES / "arcs.caliper"
    first, second = replay(script), replay(script)
    assert first.returncode == 0, first.stderr
    assert first.stdout == expected.read_bytes()
    assert second.stdout == first.stdout


def test_a_constrained_sketch_replays_to_identical_bytes() -> None:
    """Solve a hand-drawn plate into a fixed 100 x 50 rectangle with a tangent hole, then widen
    it to 120 through its dimension. Only lines and circles: no trigonometry in the solve, so
    the solved positions are the same bits on every platform."""
    script, expected = FIXTURES / "constraints.script.json", FIXTURES / "constraints.caliper"
    first, second = replay(script), replay(script)
    assert first.returncode == 0, first.stderr
    assert first.stdout == expected.read_bytes()
    assert second.stdout == first.stdout


def test_a_part_with_two_sketches_replays_to_identical_bytes() -> None:
    """V2's document (ADR 0011, schema 4): a rectangle in the part's first sketch; a second
    sketch on XZ with a circle driven to 30 mm across and a line levelled by a constraint, both
    drawn in it by name; that sketch moved to YZ; and a check of the circle, which belongs to
    the part. Lines and circles only, as above."""
    script = FIXTURES / "two-sketches.script.json"
    expected = FIXTURES / "two-sketches.caliper"
    first, second = replay(script), replay(script)
    assert first.returncode == 0, first.stderr
    assert first.stdout == expected.read_bytes()
    assert second.stdout == first.stdout


def test_replay_can_write_a_file(tmp_path: Path) -> None:
    output = tmp_path / "milestone.caliper"
    result = replay(SCRIPT, "-o", output)
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == EXPECTED.read_bytes()


def test_a_rejected_command_stops_replay_with_its_error_code(tmp_path: Path) -> None:
    script = tmp_path / "bad.script.json"
    data = json.loads(SCRIPT.read_text())
    data["commands"][1]["changes"]["width"] = -5
    script.write_text(json.dumps(data))
    result = replay(script)
    assert result.returncode == 1
    assert b"commands[1] modify_entity: width [value.not_positive]" in result.stderr
    assert result.stdout == b""


def test_an_invalid_script_is_refused(tmp_path: Path) -> None:
    script = tmp_path / "bad.script.json"
    script.write_text(
        '{"format": "caliper.script", "schema_version": 1, "commands": [{"kind": "extrude"}]}'
    )
    result = replay(script)
    assert result.returncode == 1
    assert b"unknown kind 'extrude'" in result.stderr


def test_a_part_started_with_no_sketch_replays_to_identical_bytes(tmp_path: Path) -> None:
    """Schema 2's `"part": "empty"` (ADR 0015): the app's 3D tab starts with no sketch, so what
    it records begins with the sketch the user made on a plane, and replays from there."""
    bus = Bus(part.no_sketch())
    commands = [
        CreateSketch(plane=Plane.XZ),
        CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0),
        CreateExtrude(depth=10.0),
    ]
    resolved = []
    for command in commands:
        result = bus.execute(command)
        assert isinstance(result, Applied), result
        resolved.append(result.command)
    written = tmp_path / "part.script.json"
    written.write_text(script.dumps(tuple(resolved), empty=True))
    first, second = replay(written), replay(written)
    assert first.returncode == 0, first.stderr
    assert first.stdout == snapshot.dumps(bus.document).encode()
    assert second.stdout == first.stdout
    # The same script read as a new part's would make a second sketch, so it says which.
    assert json.loads(written.read_text())["part"] == "empty"
    assert script.read(written.read_text()).start() == part.no_sketch()


def test_a_script_says_how_it_starts_only_from_schema_2(tmp_path: Path) -> None:
    old = {"format": "caliper.script", "schema_version": 1, "commands": [], "part": "empty"}
    for data, message in (
        (old, "unknown top-level field"),
        ({**old, "schema_version": 2, "part": "mostly"}, "part must be"),
        ({**old, "schema_version": 3}, "unsupported script schema_version"),
    ):
        bad = tmp_path / "bad.script.json"
        bad.write_text(json.dumps(data))
        result = replay(bad)
        assert result.returncode != 0
        assert message in result.stderr.decode()
    assert script.dumps(()) == script.dumps((), empty=False)
    assert json.loads(script.dumps(()))["schema_version"] == 1  # older readers still read it
