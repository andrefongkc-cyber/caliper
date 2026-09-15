"""The bench harness runs its cases and reports failures honestly."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "bench"


def run_bench(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BENCH / "run.py"), *map(str, args)], capture_output=True, text=True
    )


def test_all_cases_pass() -> None:
    result = run_bench()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "rectangle-100x50  pass" in result.stdout
    assert "resize-width-120  pass" in result.stdout


def test_a_wrong_result_fails(tmp_path: Path) -> None:
    case = tmp_path / "resize-width-120"
    shutil.copytree(BENCH / "cases" / "resize-width-120", case)
    expected = case / "expected.caliper"
    expected.write_text(expected.read_text().replace("120.0", "125.0"))
    result = run_bench("--cases", tmp_path)
    assert result.returncode == 1
    assert "FAIL    DIFFERS" in result.stdout


def test_a_rejected_solution_fails_with_its_error(tmp_path: Path) -> None:
    case = tmp_path / "rectangle-100x50"
    shutil.copytree(BENCH / "cases" / "rectangle-100x50", case)
    reference = case / "reference.script.json"
    data = json.loads(reference.read_text())
    data["commands"][0]["width"] = -100
    reference.write_text(json.dumps(data))
    result = run_bench("--cases", tmp_path)
    assert result.returncode == 1
    assert "[value.not_positive]" in result.stdout
