"""V2's first milestone, headless, end to end on both kernels (ADR 0013).

A 120 x 50 plate sketched on XY, held by a driving width dimension, extruded 10 mm: 60,000 mm³,
and a stored volume check says so. The width changed to 140: 70,000. Undo: 60,000 again. Saved
and reopened: the same part, the same volume. Replayed from its script: the same bytes, every
time, on every platform, since the file holds only what was asked for.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from caliper.contracts.commands import Applied, ModifyEntity
from caliper.contracts.document import EntityId, Extrude, Metric
from caliper.contracts.errors import Error
from caliper.contracts.kernel import Kernel
from caliper.contracts.queries import Expectation
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry.fake_kernel import FakeKernel
from caliper.engine.io import script, snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRIPT = FIXTURES / "extruded-plate.script.json"
GOLDEN = FIXTURES / "extruded-plate.caliper"
PLATE, WIDTH, EXTRUDE, CHECK = (EntityId(f"e{n}") for n in range(1, 5))


@pytest.fixture(params=["analytic", "occt"])
def kernel(request: pytest.FixtureRequest) -> Kernel:
    if request.param == "analytic":
        return FakeKernel()
    occt = pytest.importorskip(
        "caliper.engine.geometry.occt_kernel", reason="the occt extra isn't installed"
    )
    made: Kernel = occt.OCCTKernel()
    return made


def volume(bus: Bus) -> float:
    found = bus.queries.solid_properties()
    assert not isinstance(found, Error), found
    return found.volume


def check(bus: Bus) -> tuple[bool, float | None]:
    stored = bus.document.entities[CHECK]
    assert isinstance(stored, Expectation)
    assert stored.metric is Metric.VOLUME
    result = bus.queries.check(stored)
    return result.passed, result.actual


def test_sketch_extrude_check_widen_undo_save_reopen(kernel: Kernel, tmp_path: Path) -> None:
    bus = Bus(kernel=kernel)
    for command in script.load(SCRIPT):
        assert isinstance(bus.execute(command), Applied), command
    assert isinstance(bus.document.features[1], Extrude)
    built = bus.document
    assert volume(bus) == pytest.approx(60_000.0, rel=1e-9)
    assert check(bus) == (True, pytest.approx(60_000.0, rel=1e-9))

    assert isinstance(bus.execute(ModifyEntity(id=WIDTH, changes={"value": 140.0})), Applied)
    assert volume(bus) == pytest.approx(70_000.0, rel=1e-9)
    passed, actual = check(bus)
    assert not passed  # the check still asks for 60,000
    assert actual == pytest.approx(70_000.0, rel=1e-9)

    bus.undo()
    assert bus.document == built
    assert volume(bus) == pytest.approx(60_000.0, rel=1e-9)
    assert check(bus)[0]

    path = tmp_path / "plate.caliper"
    snapshot.save(bus.document, path)
    reopened = snapshot.load(path)
    assert reopened == bus.document
    assert snapshot.dumps(reopened) == path.read_text()
    again = Bus(reopened, kernel=kernel)
    assert volume(again) == pytest.approx(60_000.0, rel=1e-9)
    assert check(again)[0]
    assert path.read_text() == GOLDEN.read_text()  # what the script replays to, below


def replay(*args: str | Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "caliper.engine", "replay", *map(str, args)], capture_output=True
    )


def test_the_milestone_replays_to_identical_bytes() -> None:
    """The extrude, its sketch, and the check, replayed headlessly: the same file every run.
    No solid is in it, so no kernel's last bits can change it (ADR 0005)."""
    first, second = replay(SCRIPT), replay(SCRIPT)
    assert first.returncode == 0, first.stderr
    assert first.stdout == GOLDEN.read_bytes()
    assert second.stdout == first.stdout
    assert b'"kind": "extrude"' in first.stdout
    assert b"volume" in first.stdout  # the check, not a measured volume
    assert b"60000.0," not in first.stdout.replace(b'"expected": 60000.0,', b"")
