"""The CI boundary check (.github/scripts/check_boundaries.py) enforces stream ownership."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "check_boundaries.py"


def run(branch: str, *files: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--branch", branch, *files],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("branch", "path"),
    [
        ("stream/core", "caliper/engine/document/model.py"),
        ("stream/core", "bench/cases/rectangle.json"),
        ("stream/core", "tests/engine/test_bus.py"),
        ("stream/core", "docs/workplan/core.md"),
        ("stream/core/undo-stack", "docs/adr/0007-something.md"),
        ("stream/shell", "caliper/app/viewport/canvas.py"),
        ("stream/shell", "tests/app/test_tools.py"),
        ("stream/shell/rect-tool", "docs/workplan/shell.md"),
        ("contracts/add-angle-dimension", "caliper/contracts/document.py"),
        ("shared/bump-pyside", "pyproject.toml"),
        ("phase-0/foundation", "CLAUDE.md"),
    ],
)
def test_allowed(branch: str, path: str) -> None:
    result = run(branch, path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("branch", "path"),
    [
        ("stream/core", "caliper/app/main.py"),
        ("stream/core", "tests/app/test_tools.py"),
        ("stream/core", "caliper/contracts/commands.py"),
        ("stream/core", "docs/workplan/shell.md"),
        ("stream/core", "pyproject.toml"),
        ("stream/shell", "caliper/engine/cli.py"),
        ("stream/shell", "caliper/contracts/document.py"),
        ("stream/shell", "tests/engine/test_bus.py"),
        ("stream/shell", "CLAUDE.md"),
        ("stream/shell", "uv.lock"),
    ],
)
def test_denied(branch: str, path: str) -> None:
    result = run(
        branch, "caliper/app/ok.py" if branch.startswith("stream/shell") else "bench/ok.py", path
    )
    assert result.returncode == 1
    assert path in result.stdout


@pytest.mark.parametrize("branch", ["main", "fix-thing", "stream/corex", "core/topic"])
def test_unknown_branch_names_fail(branch: str) -> None:
    result = run(branch, "README.md")
    assert result.returncode == 1
    assert "naming convention" in result.stderr
