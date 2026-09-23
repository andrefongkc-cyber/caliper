"""The bench harness runs its cases and reports failures honestly."""

import json
import re
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
    for case in (
        "rectangle-100x50",
        "resize-width-120",
        "dimension-bottom-edge",
        "move-right-30",
        "delete-circle-with-dimension",
        "fillet-corner-10mm",
    ):
        assert re.search(rf"^{case}\s+pass\s+match\s+\d+ passed, 0 failed$", result.stdout, re.M), (
            case
        )
    assert "7 case(s): 7 passed, 0 failed" in result.stdout


def test_a_failed_expectation_fails(tmp_path: Path) -> None:
    case = tmp_path / "resize-width-120"
    shutil.copytree(BENCH / "cases" / "resize-width-120", case)
    spec = case / "case.json"
    data = json.loads(spec.read_text())
    data["expectations"][0]["expected"] = 125.0
    spec.write_text(json.dumps(data, indent=2) + "\n")
    result = run_bench("--cases", tmp_path)
    assert result.returncode == 1
    assert "FAIL    match     1 passed, 1 failed" in result.stdout


def test_a_wrong_move_fails_its_expectations_without_the_snapshot(tmp_path: Path) -> None:
    # Contract gap 10: width and height alone passed a rectangle moved the wrong distance.
    case = tmp_path / "move-right-30"
    shutil.copytree(BENCH / "cases" / "move-right-30", case)
    (case / "expected.caliper").unlink()
    reference = case / "reference.script.json"
    data = json.loads(reference.read_text())
    data["commands"][0]["dx"] = 25
    reference.write_text(json.dumps(data))
    result = run_bench("--cases", tmp_path)
    assert result.returncode == 1
    assert re.search(r"^move-right-30\s+FAIL\s+-\s+3 passed, 1 failed$", result.stdout, re.M)


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
