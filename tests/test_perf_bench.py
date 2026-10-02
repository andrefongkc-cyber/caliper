"""`bench/perf_v22.py`, Performance V2.2's benchmark cases, kept importable and honest.

The cases themselves take minutes and run by hand (`uv run python bench/perf.py`); this only
checks what they're built on, so a broken import or helper shows up in the suite, as
`bench/perf.py` once broke unnoticed (core.md, closing out the N phase).
"""

import sys
from pathlib import Path

from caliper.contracts.document import DistanceDimension

BENCH = Path(__file__).resolve().parent.parent / "bench"
sys.path.insert(0, str(BENCH))

import perf_v22  # noqa: E402


class Counted:
    def work(self, x: int) -> int:
        return x + 1


def test_counting_counts_and_puts_the_function_back() -> None:
    real = Counted.work
    with perf_v22.counting(Counted, "work") as count:
        assert Counted().work(1) == 2
        assert Counted().work(2) == 3
    assert count == [2]
    assert Counted.work is real


def test_the_large_sketch_is_the_size_asked_for_with_a_dimension_in_twenty() -> None:
    document = perf_v22.mixed(200)
    assert len(document.entities) == 200
    dimensions = [e for e in document.entities.values() if isinstance(e, DistanceDimension)]
    assert len(dimensions) == 10
    assert document.next_id == 201


def test_every_case_group_is_named_once() -> None:
    names = [name for name, _ in perf_v22.cases()]
    assert len(names) == len(set(names))
