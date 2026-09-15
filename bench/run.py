"""Evaluation harness: does a solver turn a prompt into the right geometry?

Each case lives in bench/cases/<name>/:

    case.json              prompt and numeric expectations
    start.caliper          starting document (optional; empty if absent)
    reference.script.json  a known-good solution
    expected.caliper       the document the reference solution produces

V1 has one solver: the reference script, which proves the harness and the engine work end to
end. The AI layer (V3) adds a solver that reads the prompt instead, and this harness is how
it gets measured.

Expectations are evaluated through `queries.check`, the same call the AI layer will use.

Usage:
    uv run python bench/run.py [--cases DIR] [CASE ...]
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from caliper.contracts.commands import Command, Rejected
from caliper.contracts.document import Document, EntityId, Feature, Ref
from caliper.contracts.queries import CheckResult, Expectation, Metric
from caliper.engine.commands.bus import Bus
from caliper.engine.io import canonical, script, snapshot

CASES = Path(__file__).resolve().parent / "cases"
CASE_FORMAT = "caliper.bench_case"


@dataclass(frozen=True)
class Case:
    name: str
    directory: Path
    prompt: str
    expectations: tuple[Expectation, ...]
    start: Document


@dataclass
class Outcome:
    case: str
    rejected: str | None = None
    snapshot_matches: bool | None = None
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.rejected is None
            and self.snapshot_matches is not False
            and all(check.passed for check in self.checks)
        )


def load_case(directory: Path) -> Case:
    data = canonical.parse((directory / "case.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != CASE_FORMAT:
        raise ValueError(f"{directory / 'case.json'} is not a bench case")
    start = directory / "start.caliper"
    return Case(
        name=directory.name,
        directory=directory,
        prompt=str(data["prompt"]),
        expectations=tuple(_expectation(item) for item in data.get("expectations", [])),
        start=snapshot.load(start) if start.exists() else Document.empty(),
    )


def _expectation(data: object) -> Expectation:
    if not isinstance(data, dict):
        raise ValueError(f"expectation must be an object, got {data!r}")
    expected, tolerance = data.get("expected"), data.get("tolerance")
    if not isinstance(expected, int | float) or not isinstance(tolerance, int | float):
        raise ValueError(f"expectation needs numeric expected and tolerance: {data!r}")
    return Expectation(
        metric=Metric(data["metric"]),
        expected=float(expected),
        tolerance=float(tolerance),
        refs=tuple(
            Ref(entity=EntityId(r["entity"]), feature=Feature(r["feature"]))
            for r in data.get("refs", [])
        ),
        ids=tuple(EntityId(i) for i in data.get("ids", [])),
    )


def reference_solver(case: Case) -> tuple[Command, ...]:
    return script.load(case.directory / "reference.script.json")


def evaluate(case: Case, commands: tuple[Command, ...]) -> Outcome:
    outcome = Outcome(case=case.name)
    bus = Bus(case.start)
    for index, command in enumerate(commands):
        result = bus.execute(command)
        if isinstance(result, Rejected):
            first = result.errors[0]
            outcome.rejected = f"commands[{index}] {command.kind}: [{first.code}] {first.message}"
            return outcome

    expected = case.directory / "expected.caliper"
    if expected.exists():
        outcome.snapshot_matches = snapshot.dumps(bus.document) == expected.read_text("utf-8")

    outcome.checks = [bus.queries.check(expectation) for expectation in case.expectations]
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("names", nargs="*", help="cases to run (default: all)")
    args = parser.parse_args(argv)

    directories = sorted(p for p in args.cases.iterdir() if (p / "case.json").exists())
    if args.names:
        directories = [d for d in directories if d.name in args.names]

    outcomes = [evaluate(case, reference_solver(case)) for case in map(load_case, directories)]

    width = max([len(o.case) for o in outcomes] + [4])
    print(f"{'case':<{width}}  result  snapshot  expectations")
    for o in outcomes:
        snapshot_cell = {True: "match", False: "DIFFERS", None: "-"}[o.snapshot_matches]
        failed = sum(not c.passed for c in o.checks)
        expectations = f"{len(o.checks) - failed} passed, {failed} failed"
        result = "pass" if o.passed else "FAIL"
        print(f"{o.case:<{width}}  {result:<6}  {snapshot_cell:<8}  {expectations}")
        if o.rejected:
            print(f"{'':<{width}}  {o.rejected}")

    failures = sum(not o.passed for o in outcomes)
    print(f"\n{len(outcomes)} case(s): {len(outcomes) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
