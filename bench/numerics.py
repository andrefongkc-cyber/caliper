"""The optimized solver against the reference (the solver as it was before Performance V2) on
every recorded Claude Desktop session: are they the same solutions?

    uv run python bench/numerics.py                  # every session in bench/sessions
    uv run python bench/numerics.py stress-plate-build --show 30

For each command, from the same document, both solvers run it, and the results are compared:
accepted or rejected, with the same errors; the same status (state, degrees of freedom, which
relations conflict or repeat); each check the session ran, passing the same way with values
within precision; and every stored value (`caliper.engine.constraints.equivalence`). Then the
whole session is replayed each way, and every relation must hold in both final drawings.
A significant difference, anything else that differs, or a relation that doesn't hold fails
the run (exit status 1). See `caliper/engine/constraints/tolerance.py` for what counts as
significant, and why.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from caliper.contracts.commands import Applied, Command, Rejected
from caliper.contracts.document import Document, EntityId, Feature, Ref
from caliper.contracts.queries import CheckResult, Expectation, Metric
from caliper.engine.commands.bus import Bus
from caliper.engine.constraints import equivalence, sketch, tolerance
from caliper.engine.io.codec import COMMAND_KINDS, DecodeError, decode_command

SESSIONS = Path(__file__).resolve().parent / "sessions"


@dataclass
class Tally:
    steps: int = 0
    rejected: int = 0
    behaviour: list[str] = field(default_factory=list)
    """Anything other than numbers that differs: outcomes, errors, status, checks."""
    values: int = 0
    same: int = 0
    precision: int = 0
    significant: list[equivalence.Difference] = field(default_factory=list)
    largest: equivalence.Difference | None = None
    unsatisfied: list[str] = field(default_factory=list)


def calls(name: str) -> list[tuple[str, dict[str, object]]]:
    data = json.loads((SESSIONS / f"{name}.json").read_text())
    return [(call["tool"], call["arguments"]) for call in data["calls"]]


def outcome(bus: Bus, command: Command) -> tuple[object, ...]:
    result = bus.execute(command)
    status = bus.queries.solve_status()
    if isinstance(result, Rejected):
        done: object = ("rejected", [(e.code, e.message, e.ids) for e in result.errors])
    else:
        assert isinstance(result, Applied)
        done = ("applied", result.created_ids)
    return done, (
        status.state,
        status.dof,
        dict(status.entity_dof),
        status.conflicting,
        status.redundant,
    )


def check(arguments: dict[str, object]) -> Expectation:
    """A `run_check` call's expectation, as the tool builds it."""
    refs = arguments.get("refs", [])
    ids = arguments.get("ids", [])
    if not isinstance(refs, list) or not isinstance(ids, list):
        raise TypeError("refs and ids must be lists")
    return Expectation(
        metric=Metric(arguments["metric"]),
        expected=float(arguments["expected"]),  # type: ignore[arg-type]
        tolerance=float(arguments["tolerance"]),  # type: ignore[arg-type]
        refs=tuple(Ref(entity=EntityId(r["entity"]), feature=Feature(r["feature"])) for r in refs),
        ids=tuple(EntityId(i) for i in ids),
    )


def same_check(a: CheckResult, b: CheckResult, document: Document) -> bool:
    if a.passed != b.passed or (a.error is None) != (b.error is None):
        return False
    if a.actual is None or b.actual is None:
        return a.actual == b.actual
    return abs(a.actual - b.actual) <= tolerance.PRECISION * equivalence.scale(document)


def compare(name: str) -> Tally:
    """Each command of the session from the same document, optimized and reference."""
    tally = Tally()
    bus = Bus(kernel=None)
    for tool, arguments in calls(name):
        if tool == "undo":
            bus.undo()
            continue
        if tool == "run_check":
            try:
                expectation = check(arguments)
            except (KeyError, TypeError, ValueError):
                continue
            ours = bus.queries.check(expectation)
            with sketch.reference():
                theirs = Bus(bus.document, kernel=None).queries.check(expectation)
            if not same_check(ours, theirs, bus.document):
                tally.behaviour.append(f"check {arguments}: {ours} vs {theirs}")
            continue
        if tool not in COMMAND_KINDS:
            continue
        try:
            command = decode_command({**arguments, "kind": tool}, tool)
        except (DecodeError, TypeError, ValueError):
            continue
        tally.steps += 1
        before = bus.document
        with sketch.reference():
            reference = Bus(before, kernel=None)
            expected = outcome(reference, command)
        actual = outcome(bus, command)
        if expected[0][0] == "rejected":  # type: ignore[index]
            tally.rejected += 1
        for what, a, b in (("outcome", actual[0], expected[0]), ("status", actual[1], expected[1])):
            if a != b:
                tally.behaviour.append(f"step {tally.steps} {tool} {what}: {a} vs {b}")
        tally_values(tally, reference.document, bus.document)
    return tally


def tally_values(tally: Tally, reference: Document, candidate: Document) -> None:
    for d in equivalence.differences(reference, candidate):
        tally.values += 1
        if d.significant:
            tally.significant.append(d)
        elif d.same:
            tally.same += 1
        else:
            tally.precision += 1
        if tally.largest is None or d.absolute > tally.largest.absolute:
            tally.largest = d


def replay(name: str, *, reference: bool) -> Document:
    from caliper.ai.draft import Draft

    draft, base = Draft(), Bus(kernel=None).document
    if reference:
        with sketch.reference():
            for tool, arguments in calls(name):
                draft.call(base, (), tool, arguments)
    else:
        for tool, arguments in calls(name):
            draft.call(base, (), tool, arguments)
    return base if draft.workspace is None else draft.workspace.document


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sessions", nargs="*", help="names in bench/sessions (default: all)")
    parser.add_argument("--show", type=int, default=10, help="differences to list per session")
    args = parser.parse_args(argv[1:])
    names = args.sessions or sorted(p.stem for p in SESSIONS.glob("*.json"))
    failed = False
    for name in names:
        started = time.perf_counter()
        tally = compare(name)
        ours, theirs = replay(name, reference=False), replay(name, reference=True)
        for label, document in (("optimized", ours), ("reference", theirs)):
            for id, (residual, allowed) in sorted(sketch.residuals(document).items()):
                if not residual <= allowed:
                    tally.unsatisfied.append(f"{label}: {id} off by {residual:.3g} > {allowed:.3g}")
        final = equivalence.differences(theirs, ours)
        took = time.perf_counter() - started
        print(f"{name}: {tally.steps} commands compared ({tally.rejected} rejected by both)")
        print(f"  took {took:.1f} s")
        differing = f"{len(tally.behaviour)} DIFFERENCES" if tally.behaviour else "identical"
        print(f"  outcomes, messages, status, checks: {differing}")
        for line in tally.behaviour[: args.show]:
            print(f"    {line}")
        counts = (
            f"{len(tally.significant)} significant, {tally.precision} within precision, "
            f"{tally.same} the same geometry"
        )
        largest = tally.largest
        if largest is not None:
            counts += f"; largest {largest.absolute:.3g} ({largest.entity} {largest.field})"
        print(f"  values after each command: {tally.values} differ: {counts}")
        holding = "NO" if tally.unsatisfied else "yes"
        print(f"  every relation holds in both final drawings: {holding}")
        for line in tally.unsatisfied[: args.show]:
            print(f"    {line}")
        print("  final drawings, replayed each way:")
        print("    " + equivalence.report(final, args.show).replace("\n", "\n    "))
        failed |= bool(tally.behaviour or tally.significant or tally.unsatisfied)
        failed |= any(d.significant for d in final)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
