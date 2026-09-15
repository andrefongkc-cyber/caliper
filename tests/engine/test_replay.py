"""Invariant 4: every command runs headlessly, and replay output is byte-identical.

The expected file is committed, and CI compares against it on Linux and macOS, so this also
checks that output is identical across platforms.
"""

import json
import subprocess
import sys
from pathlib import Path

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
