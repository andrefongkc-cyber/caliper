"""Time an MCP session the way the Caliper window runs it: every call in the draft, and every
call that changes the draft shows the whole draft again as a proposal.

Run `uv run python tests/ai/bench_mcp_session.py`. Not a test: pytest doesn't collect it.
The sketch is one connected profile (a comb, 8 changes a tooth), so every constraint
re-solves all of it, like a plate outline does. For each size it reports, in seconds:

- the whole session, and its slowest call
- "replay": showing the proposal by running every pending command again (before the fix,
  what every change did), against "reuse": showing the workspace's own document
- one Accept: the commands through a real bus in one transaction, which still happens once

The replay path is skipped above `--replay-max` changes (130): at 224 it took 277 s.
"""

import argparse
import time
from collections.abc import Callable

from caliper.ai.draft import Draft
from caliper.app.agent.proposal import Plan, prepare
from caliper.contracts.document import Document
from caliper.engine.commands.bus import Bus

Show = Callable[[Draft, Document], None]


def comb(teeth: int) -> list[tuple[str, dict[str, object]]]:
    calls: list[tuple[str, dict[str, object]]] = []
    count, x, previous = 0, 0.0, None

    def add(name: str, arguments: dict[str, object]) -> str:
        nonlocal count
        calls.append((name, arguments))
        count += 1
        return f"e{count}"

    def line(a: tuple[float, float], b: tuple[float, float]) -> str:
        return add("create_line", {"start": {"x": a[0], "y": a[1]}, "end": {"x": b[0], "y": b[1]}})

    def join(a: str, b: str) -> None:
        refs = [{"entity": a, "feature": "end"}, {"entity": b, "feature": "start"}]
        add("create_constraint", {"type": "coincident", "refs": refs})

    for _ in range(teeth):
        up, top = line((x, 0), (x, 20)), line((x, 20), (x + 10, 20))
        if previous is not None:
            join(previous, up)
        join(up, top)
        add("create_constraint", {"type": "vertical", "refs": [{"entity": up, "feature": "curve"}]})
        add(
            "create_constraint",
            {"type": "horizontal", "refs": [{"entity": top, "feature": "curve"}]},
        )
        dimension = {
            "refs": [{"entity": up, "feature": "curve"}],
            "placement": {"x": x - 5, "y": 10},
        }
        add("create_dimension", {**dimension, "value": 20})
        previous = line((x + 10, 20), (x + 10, 0))
        join(top, previous)
        x += 10
    return calls


def replay(draft: Draft, base: Document) -> None:
    prepare(Plan(draft.label, "", draft.commands, draft.checks), base, ())


def reuse(draft: Draft, base: Document) -> None:
    assert draft.workspace is not None
    plan = Plan(draft.label, "", draft.commands, draft.checks)
    prepare(plan, base, (), result=draft.workspace.document)


def session(calls: list[tuple[str, dict[str, object]]], show: Show) -> tuple[float, float, Draft]:
    base, draft = Bus().document, Draft()
    started, slowest = time.perf_counter(), 0.0
    for name, arguments in calls:
        t = time.perf_counter()
        answer = draft.call(base, (), name, arguments)
        assert not answer.outcome.is_error, answer.outcome.content
        if answer.changed:
            show(draft, base)
        slowest = max(slowest, time.perf_counter() - t)
    return time.perf_counter() - started, slowest, draft


def accept(draft: Draft) -> float:
    bus = Bus()
    t = time.perf_counter()
    with bus.transaction(draft.label):
        for command in draft.commands:
            bus.execute(command)
    assert draft.workspace is not None
    assert bus.document == draft.workspace.document
    return time.perf_counter() - t


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replay-max", type=int, default=130)
    args = parser.parse_args()
    columns = ("changes", "replay: session", "slowest", "reuse: session", "slowest", "accept")
    print(" ".join(f"{c:>{w}}" for c, w in zip(columns, (8, 16, 8, 15, 8, 7), strict=True)))
    for teeth in (2, 7, 14, 28):
        calls = comb(teeth)
        checks = [
            (
                "run_check",
                {"metric": "bbox_height", "expected": 20, "tolerance": 0.001, "ids": [f"e{i}"]},
            )
            for i in (1, 8, 16)
        ]
        calls = calls + checks
        new_total, new_slowest, draft = session(calls, reuse)
        if len(calls) <= args.replay_max:
            old_total, old_slowest, _ = session(calls, replay)
            old = f"{old_total:15.2f}s {old_slowest:7.3f}s"
        else:
            old = f"{'(skipped)':>16} {'':>8}"
        print(f"{len(calls):8d} {old} {new_total:14.2f}s {new_slowest:7.3f}s {accept(draft):6.2f}s")


if __name__ == "__main__":
    main()
