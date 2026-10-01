"""Performance benchmarks: what a user waits for, from Claude's first call to an accepted sketch.

    uv run python bench/perf.py                        # every case, printed as a table
    uv run python bench/perf.py --only session         # cases whose name contains "session"
    uv run python bench/perf.py --save before.json     # keep the numbers
    uv run python bench/perf.py --compare before.json  # and show the change against them

The main cases replay real MCP sessions, recorded from the manual test runs
(`bench/sessions/*.json`: every Caliper tool call Claude made, in order), so they measure the
work Claude actually causes rather than a guess at it:

- `session/<name>/engine`: each call through `Draft` alone, as the window runs it but with no
  window. `total` is Caliper's share of proposal creation; the rest of a run is Claude. Of it,
  `solve_ms` went to solving constraints, `check_ms` to `run_check` calls, and `status_ms` to
  `solve_status` calls. `evaluations` counts the solver's residual and derivative evaluations
  (Newton's unit of work); `deferred`, split-cluster solves left to the whole sketch.
- `session/<name>/accept-engine`: accepting the proposal on a fresh bus in one transaction, as
  the window does (`total`), and `replay`: every command validated and solved again, which is
  what Accept did before Performance V2.
- `session/<name>/window`: each call through the window's MCP host (the proposal card, the
  canvas, the Assistant log, the Timing panel), then the events it caused. Offscreen.
- `session/<name>/window-accept` and `window-reject`: pressing the card's buttons.
- `session/<name>/memory`: Python memory while the calls run (tracemalloc): the peak, what
  the pending proposal still holds after them, and the peak while accepting it.
- `session/<name>/results`: how much the tool results would put in Claude's context.
- `session/<name>/workflow`: Caliper's whole share of the workflow, calls plus Accept.
- `bridge/round-trip`: one cheap call over the real socket, as `caliper-mcp` sends it.

The sessions are small (`rectangle`, a short request written by hand), medium
(`ball-bearing`, test 001), and large (`stress-plate-build`, test 002).

Synthetic cases cover what the sessions don't reach: many entities, many constraints, many
checks, a large `inspect_document`, saving and opening a large sketch, drawing it, and what
the Timing panel costs a call. `repeat/*` times one mirror, linear pattern, and circular
pattern call each (the recorded sessions predate those tools). Times are wall-clock seconds
(or milliseconds and microseconds where marked) on this machine; compare runs on the same
machine only.

The script uses only what `main` had before Performance V2 where it can (Accept falls back to
replaying), so `PYTHONPATH=<a main checkout> python bench/perf.py --save before.json` measures
the baseline to `--compare` against. `bench/results/` keeps saved runs, all on Andre's Mac:
`main` just before Performance V2 and the branch after it (2026-09-27), and Performance V2
again against the numerical pass after it, Solver V2.1 (2026-09-28).
"""

import argparse
import gc
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from caliper.ai.bridge import Request, ask, encode_request
from caliper.ai.context import describe
from caliper.ai.draft import Draft
from caliper.contracts.commands import CreateCheck, CreateCircle, CreateConstraint, CreateLine
from caliper.contracts.document import (
    Circle,
    ConstraintType,
    Document,
    Entity,
    EntityId,
    Feature,
    Point2,
    Ref,
)
from caliper.contracts.queries import Expectation, Metric
from caliper.engine.commands import handlers
from caliper.engine.commands.bus import Bus
from caliper.engine.constraints import sketch
from caliper.engine.io import snapshot

SESSIONS = Path(__file__).resolve().parent / "sessions"


@dataclass
class Result:
    name: str
    metrics: dict[str, float] = field(default_factory=dict)
    """Seconds unless the key says otherwise (`_ms`, `_kib`, `_mib`, a count)."""


def session_calls(name: str) -> list[tuple[str, dict[str, object]]]:
    data = json.loads((SESSIONS / f"{name}.json").read_text())
    return [(call["tool"], call["arguments"]) for call in data["calls"]]


def spread(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "total": sum(samples),
        "median_ms": 1e3 * statistics.median(ordered),
        "p95_ms": 1e3 * ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
        "max_ms": 1e3 * ordered[-1],
        "calls": float(len(samples)),
    }


# --- Recorded sessions, without a window ---------------------------------------------------


def engine_session(name: str) -> tuple[list[Result], Draft, Document]:
    calls = session_calls(name)
    base = Bus().document
    draft = Draft()
    samples: list[float] = []
    by_tool: dict[str, float] = {}
    solving = [0.0]
    real_settle = handlers.settle

    def timed_settle(*args: object) -> object:
        started = time.perf_counter()
        try:
            return real_settle(*args)  # type: ignore[arg-type]
        finally:
            solving[0] += time.perf_counter() - started

    counted = {"evaluations": 0, "deferred": 0}
    real_evaluate = sketch._evaluate
    real_split = getattr(sketch, "_settle", None)  # not on main before Performance V2

    def counting_evaluate(*args: object) -> object:
        counted["evaluations"] += 1
        return real_evaluate(*args)  # type: ignore[arg-type]

    def counting_split(*args: object, **kwargs: object) -> object:
        outcome = real_split(*args, **kwargs)  # type: ignore[misc]
        if outcome is None:
            counted["deferred"] += 1
        return outcome

    handlers.settle = timed_settle  # type: ignore[assignment]
    sketch._evaluate = counting_evaluate  # type: ignore[assignment]
    if real_split is not None:
        sketch._settle = counting_split
    try:
        for tool, arguments in calls:
            started = time.perf_counter()
            draft.call(base, (), tool, arguments)
            took = time.perf_counter() - started
            samples.append(took)
            by_tool[tool] = by_tool.get(tool, 0.0) + took
    finally:
        handlers.settle = real_settle  # type: ignore[assignment]
        sketch._evaluate = real_evaluate
        if real_split is not None:
            sketch._settle = real_split
    assert draft.workspace is not None
    engine = Result(f"session/{name}/engine", spread(samples))
    engine.metrics["solve_ms"] = 1e3 * solving[0]
    engine.metrics["check_ms"] = 1e3 * by_tool.get("run_check", 0.0)
    engine.metrics["status_ms"] = 1e3 * by_tool.get("solve_status", 0.0)
    engine.metrics["evaluations"] = float(counted["evaluations"])
    engine.metrics["deferred"] = float(counted["deferred"])
    engine.metrics["commands"] = float(len(draft.commands))
    entities = draft.workspace.document.entities.values()  # checks are in the proposal (ADR 0010)
    engine.metrics["checks"] = float(sum(isinstance(e, Expectation) for e in entities))

    accept = Result(f"session/{name}/accept-engine")
    for replaying in (False, True):
        bus = Bus(base)
        started = time.perf_counter()
        with _already(None if replaying else draft), bus.transaction("Accept"):
            for command in draft.commands:
                bus.execute(command)
        accept.metrics["replay" if replaying else "total"] = time.perf_counter() - started
        assert bus.document == draft.workspace.document, "accept must build the same document"
    return [engine, accept], draft, base


def _already(draft: Draft | None) -> AbstractContextManager[None]:
    """Accept's shortcut, committing what the draft already ran: nothing to use on `main`
    before Performance V2, or when replaying on purpose."""
    executed = getattr(draft, "executed", None)
    already = getattr(handlers, "already", None)
    return nullcontext() if executed is None or already is None else already(executed)


def memory_session(name: str) -> Result:
    calls = session_calls(name)
    base = Bus().document
    gc.collect()
    tracemalloc.start()
    draft = Draft()
    for tool, arguments in calls:
        draft.call(base, (), tool, arguments)
    gc.collect()  # what the proposal holds, not garbage waiting to be collected
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    bus = Bus(base)
    with _already(draft), bus.transaction("Accept"):
        for command in draft.commands:
            bus.execute(command)
    _, accepting = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return Result(
        f"session/{name}/memory",
        {
            "peak_mib": peak / 2**20,
            "retained_mib": current / 2**20,
            "accept_peak_mib": (accepting - current) / 2**20,
        },
    )


def result_sizes(name: str) -> Result:
    calls = session_calls(name)
    base = Bus().document
    draft = Draft()
    sizes: list[int] = []
    for tool, arguments in calls:
        answer = draft.call(base, (), tool, arguments)
        sizes.append(len(json.dumps(answer.outcome.content, sort_keys=True)))
    return Result(
        f"session/{name}/results",
        {"total_kib": sum(sizes) / 1024, "max_kib": max(sizes) / 1024, "calls": float(len(sizes))},
    )


# --- Recorded sessions, in the window ------------------------------------------------------


def _window() -> tuple[object, object]:
    from PySide6.QtWidgets import QApplication

    from caliper.app import theme
    from caliper.app.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    theme.apply(app)  # type: ignore[arg-type]
    window = MainWindow()
    window.resize(1280, 800)
    window.show()
    app.processEvents()
    return app, window


def _dispose(window: object) -> None:
    """Put a benchmark window away without closing it: closing asks to save the changes."""
    window.hide()  # type: ignore[attr-defined]
    window.deleteLater()  # type: ignore[attr-defined]


def _serve(window: object) -> object:
    from caliper.app.agent.mcp_host import McpHost

    host = McpHost(window.session, window.agent, Path(tempfile.mkdtemp()) / "x.sock", window)  # type: ignore[attr-defined]
    window.mcp = host  # type: ignore[attr-defined]
    host.stepped.connect(window.assistant_log.remote_step)  # type: ignore[attr-defined]
    host.timed.connect(window.timing.show_timing)  # type: ignore[attr-defined]
    if hasattr(window, "timing_dock"):
        window.timing.source = lambda: host.timer.timing  # type: ignore[attr-defined]
    return host


def window_session(name: str, finish: str) -> list[Result]:
    calls = session_calls(name)
    app, window = _window()
    host = _serve(window)
    lines = [
        encode_request(Request(client="claude-ai", tool=tool, arguments=arguments)).rstrip(b"\n")
        for tool, arguments in calls
    ]
    handled: list[float] = []
    events: list[float] = []
    for line in lines:
        started = time.perf_counter()
        host.handle(line)  # type: ignore[attr-defined]
        middle = time.perf_counter()
        app.processEvents()  # type: ignore[attr-defined]
        handled.append(middle - started)
        events.append(time.perf_counter() - middle)
    results = [
        Result(
            f"session/{name}/window", spread([a + b for a, b in zip(handled, events, strict=True)])
        )
    ]
    results[0].metrics["ui_share_ms"] = 1e3 * statistics.median(events)
    card = window.proposal_card  # type: ignore[attr-defined]
    button = card.accept_button if finish == "accept" else card.reject_button
    started = time.perf_counter()
    button.click()
    app.processEvents()  # type: ignore[attr-defined]
    results.append(
        Result(f"session/{name}/window-{finish}", {"total": time.perf_counter() - started})
    )
    if finish == "accept":
        results[-1].metrics["entities"] = float(len(window.session.document.entities))  # type: ignore[attr-defined]
    host.close()  # type: ignore[attr-defined]
    _dispose(window)
    return results


def bridge_round_trip() -> Result:
    """One `inspect_document` on an empty sketch through the real socket: the MCP overhead."""
    app, window = _window()
    path = Path(tempfile.mkdtemp(prefix="cal", dir="/tmp")) / "mcp.sock"
    assert window.serve_mcp(path)  # type: ignore[attr-defined]
    request = Request(client="bench", tool="solve_status", arguments={})
    samples: list[float] = []
    done = threading.Event()

    def client() -> None:
        for _ in range(200):
            started = time.perf_counter()
            ask(request, path, timeout=10)
            samples.append(time.perf_counter() - started)
        done.set()

    threading.Thread(target=client, daemon=True).start()
    while not done.is_set():
        app.processEvents()  # type: ignore[attr-defined]
    window.mcp.close()  # type: ignore[attr-defined]
    _dispose(window)
    return Result("bridge/round-trip", spread(samples))


# --- Synthetic scale -----------------------------------------------------------------------


def _circles(count: int) -> Document:
    entities: dict[EntityId, Entity] = {}
    for n in range(1, count + 1):
        x, y = (n % 50) * 12.0, (n // 50) * 12.0
        entities[EntityId(f"e{n}")] = Circle(center=Point2(x=x, y=y), radius=4.0)
    return Document(entities=MappingProxyType(entities), next_id=count + 1)


def _paired(count: int) -> Document:
    """`count` circles, each pair of them equal: one constraint to every two circles."""
    bus = Bus(_circles(count))
    ids = sorted(bus.document.entities, key=lambda i: int(i[1:]))
    for left, right in zip(ids[:-1:2], ids[1::2], strict=False):
        bus.execute(
            CreateConstraint(
                type=ConstraintType.EQUAL,
                refs=(
                    Ref(entity=left, feature=Feature.CURVE),
                    Ref(entity=right, feature=Feature.CURVE),
                ),
            )
        )
    return bus.document


def many_entities() -> Result:
    """2,000 circles, then one more through the bus: the per-command cost at that size."""
    bus = Bus(_circles(2000))
    samples = []
    for n in range(50):
        started = time.perf_counter()
        bus.execute(CreateCircle(center=Point2(x=-10.0 * n, y=-10.0), radius=2.0))
        samples.append(time.perf_counter() - started)
    return Result("synthetic/many-entities/create", spread(samples))


def many_constraints() -> Result:
    """A chain of 150 lines joined end to end, horizontal and vertical by turns: one large
    connected cluster, built constraint by constraint."""
    bus = Bus()
    samples = []
    previous: EntityId | None = None
    for n in range(150):
        horizontal = n % 2 == 0
        start = Point2(x=float(n), y=float(n))
        end = Point2(
            x=start.x + (10.0 if horizontal else 0.0), y=start.y + (0.0 if horizontal else 10.0)
        )
        (line,) = bus.execute(CreateLine(start=start, end=end)).created_ids  # type: ignore[union-attr]
        commands = [
            CreateConstraint(
                type=ConstraintType.HORIZONTAL if horizontal else ConstraintType.VERTICAL,
                refs=(Ref(entity=line, feature=Feature.CURVE),),
            )
        ]
        if previous is not None:
            commands.append(
                CreateConstraint(
                    type=ConstraintType.COINCIDENT,
                    refs=(
                        Ref(entity=previous, feature=Feature.END),
                        Ref(entity=line, feature=Feature.START),
                    ),
                )
            )
        for command in commands:
            started = time.perf_counter()
            bus.execute(command)
            samples.append(time.perf_counter() - started)
        previous = line
    result = Result("synthetic/many-constraints/chain-150", spread(samples))
    started = time.perf_counter()
    bus.queries.solve_status()
    result.metrics["status_ms"] = 1e3 * (time.perf_counter() - started)
    return result


def repeats() -> list[Result]:
    """Mirror, linear pattern, and circular pattern, each one tool call in a workspace, as the
    MCP host runs them: a 5 by 4 grid of dimensioned holes, half a 12-point star mirrored about
    its centreline, and the whole star from one fully constrained point. `commands` is how
    many of Caliper's commands the call ran, each solved."""
    import itertools
    import math

    from caliper.ai.model import ToolCall
    from caliper.ai.tools import Workspace

    def call(w: Workspace, name: str, **arguments: object) -> dict[str, object]:
        outcome = w.call(ToolCall(id="bench", name=name, arguments=arguments))
        assert not outcome.is_error, outcome.content
        assert isinstance(outcome.content, dict)
        return outcome.content

    def ref(entity: object, feature: str) -> dict[str, object]:
        return {"entity": entity, "feature": feature}

    def made(content: dict[str, object]) -> str:
        created = content["created"]
        assert isinstance(created, list)
        return str(created[0])

    def at(radius: float, degrees: float) -> dict[str, float]:
        t = math.radians(degrees)
        return {"x": 120 + radius * math.cos(t), "y": 80 + radius * math.sin(t)}

    def star_layout() -> tuple[Workspace, str, str, str, str]:
        """The star's fixed centre, a vertical centreline, and its R25 and R12 circles."""
        w = Workspace(Document.empty())
        centre = made(call(w, "create_point", position=at(0, 0), construction=True))
        call(w, "create_constraint", type="fix", refs=[ref(centre, "point")])
        axis = made(call(w, "create_line", start=at(0, 0), end=at(40, 90), construction=True))
        call(
            w,
            "create_constraint",
            type="coincident",
            refs=[ref(centre, "point"), ref(axis, "start")],
        )
        call(w, "create_constraint", type="vertical", refs=[ref(axis, "curve")])
        rings = []
        for radius in (25, 12):
            ring = made(call(w, "create_circle", center=at(0, 0), radius=radius, construction=True))
            call(
                w,
                "create_constraint",
                type="concentric",
                refs=[ref(ring, "curve"), ref(centre, "point")],
            )
            rings.append(ring)
        return w, centre, axis, rings[0], rings[1]

    def grid() -> tuple[Workspace, dict[str, object]]:
        w = Workspace(Document.empty())
        origin = made(call(w, "create_point", position={"x": 0, "y": 0}))
        call(w, "create_constraint", type="fix", refs=[ref(origin, "point")])
        hole = made(call(w, "create_circle", center={"x": 60, "y": 25}, radius=3))
        for orientation, value in (("horizontal", 60), ("vertical", 25)):
            call(
                w,
                "create_distance_dimension",
                a=ref(origin, "point"),
                b=ref(hole, "center"),
                orientation=orientation,
                offset=5,
                value=value,
            )
        return w, {"ids": [hole], "count": 5, "spacing": 30, "count2": 4, "spacing2": 30}

    def half_star() -> tuple[Workspace, dict[str, object]]:
        w, _, axis, _, _ = star_layout()
        corners = [at(25 if k % 2 == 0 else 12, 90 + 15 * k) for k in range(13)]
        lines = [
            made(call(w, "create_line", start=a, end=b)) for a, b in itertools.pairwise(corners)
        ]
        for a, b in itertools.pairwise(lines):
            call(w, "create_constraint", type="coincident", refs=[ref(a, "end"), ref(b, "start")])
        return w, {"ids": lines, "axis": axis}

    def star_point() -> tuple[Workspace, dict[str, object]]:
        w, centre, axis, outer, inner = star_layout()
        right = made(call(w, "create_line", start=at(12, 75), end=at(25, 90)))
        left = made(call(w, "create_line", start=at(25, 90), end=at(12, 105)))
        for refs in (
            [ref(right, "end"), ref(left, "start")],
            [ref(axis, "curve"), ref(right, "end")],
            [ref(outer, "curve"), ref(right, "end")],
            [ref(inner, "curve"), ref(left, "end")],
        ):
            call(w, "create_constraint", type="coincident", refs=refs)
        call(
            w,
            "create_constraint",
            type="symmetric",
            refs=[ref(left, "end"), ref(right, "start"), ref(axis, "curve")],
        )
        return w, {"ids": [right, left], "center": centre, "count": 12}

    results = []
    for name, tool, build in (
        ("repeat/linear-pattern/grid-5x4", "linear_pattern", grid),
        ("repeat/mirror/half-star", "mirror_entities", half_star),
        ("repeat/circular-pattern/star-12", "circular_pattern", star_point),
    ):
        samples, commands = [], 0
        for _ in range(3):
            w, arguments = build()
            before = len(w.commands)
            started = time.perf_counter()
            call(w, tool, **arguments)
            samples.append(time.perf_counter() - started)
            commands = len(w.commands) - before
        result = Result(name, spread(samples))
        result.metrics["commands"] = commands
        results.append(result)
    return results


def many_checks() -> Result:
    """Preparing a proposal with 200 checks, and the Checks panel re-measuring 200 checks."""
    from caliper.app.agent.proposal import Plan, prepare

    document = _circles(400)
    checks = tuple(
        CreateCheck(
            metric=Metric.BBOX_WIDTH, expected=8.0, tolerance=1e-6, ids=(EntityId(f"e{n}"),)
        )
        for n in range(1, 201)
    )
    plan = Plan("Checks", "", checks)  # checks are commands in the proposal (ADR 0010)
    bus = Bus(document)
    for check in checks:
        bus.execute(check)
    samples = []
    for _ in range(20):
        started = time.perf_counter()
        prepare(plan, document, result=bus.document)
        samples.append(time.perf_counter() - started)
    return Result("synthetic/many-checks/prepare-200", spread(samples))


def v2_milestone() -> Result:
    """V2's milestone (ADR 0013): a 120 x 50 plate extruded 10 mm, the width driven to 140,
    a label moved, then undone, each step timed with the volume asked for after it. The
    counts are prisms the kernel built again: the width rebuilds one, the label and undo none."""
    from caliper.contracts.commands import (
        CreateDistanceDimension,
        CreateExtrude,
        CreateRectangle,
        ModifyEntity,
    )
    from caliper.contracts.document import DistanceOrientation
    from caliper.contracts.errors import Error
    from caliper.engine import features
    from caliper.engine.geometry import default_kernel
    from caliper.engine.geometry.fake_kernel import FakeKernel

    real = default_kernel() or FakeKernel()

    class Counting:
        def __init__(self) -> None:
            self.prisms = 0

        def __getattr__(self, name: str) -> object:
            return getattr(real, name)

        def extrude(self, *args: object) -> object:
            self.prisms += 1
            return real.extrude(*args)  # type: ignore[arg-type]

    kernel = Counting()
    features.forget()
    result = Result("v2/milestone")

    def step(name: str, change: Callable[[], object]) -> None:
        before = kernel.prisms
        started = time.perf_counter()
        change()
        volume = bus.queries.solid_properties()
        result.metrics[f"{name}_ms"] = 1e3 * (time.perf_counter() - started)
        result.metrics[f"{name}_prisms"] = float(kernel.prisms - before)
        assert not isinstance(volume, Error), volume

    bus = Bus(kernel=kernel)  # type: ignore[arg-type]
    plate, width = EntityId("e1"), EntityId("e2")

    def build() -> None:
        bus.execute(CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0))
        bus.execute(
            CreateDistanceDimension(
                a=Ref(entity=plate, feature=Feature.BOTTOM_LEFT),
                b=Ref(entity=plate, feature=Feature.BOTTOM_RIGHT),
                orientation=DistanceOrientation.HORIZONTAL,
                offset=-10.0,
                value=120.0,
            )
        )
        bus.execute(CreateExtrude(depth=10.0))

    step("build", build)
    step("widen", lambda: bus.execute(ModifyEntity(id=width, changes={"value": 140.0})))
    step("label", lambda: bus.execute(ModifyEntity(id=width, changes={"offset": -20.0})))
    step("undo", lambda: (bus.undo(), bus.undo()))
    result.metrics["analytic"] = float(isinstance(real, FakeKernel))
    return result


def large_inspect() -> Result:
    document = _circles(2000)
    samples, size = [], 0
    for limit in (40, 40, 40, 400):
        started = time.perf_counter()
        found = describe(document, limit=limit)
        samples.append(time.perf_counter() - started)
        size = max(size, len(json.dumps(found)))
    result = Result("synthetic/inspect-document/2000", spread(samples))
    result.metrics["max_kib"] = size / 1024
    return result


def large_file() -> Result:
    """Saving and opening a 2,000-circle sketch with 1,000 constraints (3,000 entities)."""
    document = _paired(2000)
    folder = Path(tempfile.mkdtemp())
    written, read, saved, text = [], [], [], ""
    for n in range(5):
        started = time.perf_counter()
        text = snapshot.dumps(document)
        written.append(time.perf_counter() - started)
        started = time.perf_counter()
        snapshot.loads(text)
        read.append(time.perf_counter() - started)
        started = time.perf_counter()
        snapshot.save(document, folder / f"large-{n}.caliper")
        saved.append(time.perf_counter() - started)
    return Result(
        "synthetic/file/2000",
        {
            "dumps_ms": 1e3 * statistics.median(written),
            "loads_ms": 1e3 * statistics.median(read),
            "save_ms": 1e3 * statistics.median(saved),
            "size_kib": len(text.encode()) / 1024,
        },
    )


def timing_overhead() -> Result:
    """What timing costs each call: the run timer's bookkeeping and the Timing panel showing
    it (which is also all a tick of its one-second clock does)."""
    from caliper.app.agent.timing import RunTimer
    from caliper.app.panels.timing import TimingPanel

    _, window = _window()
    timer = RunTimer()
    panel = TimingPanel()
    panel.source = lambda: timer.timing
    panel.expanded = True
    timer.start()
    samples = []
    for _ in range(500):
        started = time.perf_counter()
        arrived = timer.arrived()
        timer.finished(arrived, changed=True)
        panel.show_timing(timer.timing)
        samples.append(time.perf_counter() - started)
    panel.deleteLater()
    _dispose(window)
    return Result("timing/overhead", {"per_call_us": 1e6 * statistics.median(samples)})


def large_render() -> Result:
    """A full redraw of a 2,000-entity sketch with its constraint badges, then with them hidden."""
    app, window = _window()
    window.session.replace(Bus(_paired(2000)), None)  # type: ignore[attr-defined]
    canvas = window.canvas  # type: ignore[attr-defined]
    canvas.zoom_to_fit()
    result = Result("synthetic/render/2000")
    for shown in (True, False):
        canvas.show_constraints = shown
        samples = []
        for _ in range(5):
            canvas._layer = None  # force the full static redraw, as a document change does
            canvas._layer_document = None
            canvas._spots_document = None
            canvas._glyphs_key = None
            started = time.perf_counter()
            canvas.repaint()
            app.processEvents()  # type: ignore[attr-defined]
            samples.append(time.perf_counter() - started)
        result.metrics[f"{'badges' if shown else 'no_badges'}_ms"] = 1e3 * statistics.median(
            samples
        )
    _dispose(window)
    return result


def render_3d() -> list[Result]:
    """Frames of the 3D view (ADR 0012) at 1280 x 800, drawn without a GPU: the milestone plate,
    and a 240 x 160 plate with 24 round holes. On OCCT when installed, else the analytic kernel."""
    from caliper.contracts.commands import CreateExtrude, CreateRectangle
    from caliper.engine import features
    from caliper.engine.geometry import default_kernel

    app, window = _window()
    window.resize(1280, 800)  # type: ignore[attr-defined]
    found = []
    for name, build in (
        ("plate", [CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0)]),
        (
            "holes-24",
            [
                CreateRectangle(corner=Point2(x=0.0, y=0.0), width=240.0, height=160.0),
                *(
                    CreateCircle(center=Point2(x=20.0 + 40 * i, y=20.0 + 40 * j), radius=8.0)
                    for i in range(6)
                    for j in range(4)
                ),
            ],
        ),
    ):
        features.forget()
        window.session.replace(Bus(), None)  # type: ignore[attr-defined]
        for command in (*build, CreateExtrude(depth=10.0)):
            window.session.execute(command)  # type: ignore[attr-defined]
        window.set_mode("3d")  # type: ignore[attr-defined]
        app.processEvents()  # type: ignore[attr-defined]
        view = window.view3d  # type: ignore[attr-defined]
        view.resize(1280, 800)
        view.frame_ms.clear()
        for _ in range(20):
            view.repaint()
        result = Result(f"v2/render-3d/{name}", spread([ms / 1e3 for ms in view.frame_ms]))
        result.metrics["triangles"] = float(len(view.mesh.triangles))
        result.metrics["occt"] = float(default_kernel() is not None)
        found.append(result)
        window.set_mode("2d")  # type: ignore[attr-defined]
    _dispose(window)
    return found


# --- Running -------------------------------------------------------------------------------


def cases() -> list[tuple[str, Callable[[], list[Result]]]]:
    found: list[tuple[str, Callable[[], list[Result]]]] = []
    for name in ("rectangle", "ball-bearing", "stress-plate-build"):  # small, medium, large
        found.append((f"session/{name}/engine", lambda n=name: engine_session(n)[0]))
        found.append((f"session/{name}/memory", lambda n=name: [memory_session(n)]))
        found.append((f"session/{name}/results", lambda n=name: [result_sizes(n)]))
        found.append((f"session/{name}/window", lambda n=name: window_session(n, "accept")))
        found.append(
            (f"session/{name}/window-reject", lambda n=name: window_session(n, "reject")[1:])
        )
    found.append(("bridge/round-trip", lambda: [bridge_round_trip()]))
    found.append(("synthetic/many-entities", lambda: [many_entities()]))
    found.append(("synthetic/many-constraints", lambda: [many_constraints()]))
    found.append(("synthetic/many-checks", lambda: [many_checks()]))
    found.append(("repeat", repeats))
    found.append(("synthetic/inspect-document", lambda: [large_inspect()]))
    found.append(("v2/milestone", lambda: [v2_milestone()]))
    found.append(("v2/render-3d", render_3d))
    found.append(("synthetic/file", lambda: [large_file()]))
    found.append(("synthetic/render", lambda: [large_render()]))
    found.append(("timing/overhead", lambda: [timing_overhead()]))
    return found


def workflow(results: dict[str, Result]) -> list[Result]:
    """Caliper's share of the whole workflow: every call in the window, then Accept."""
    out = []
    for name in ("rectangle", "ball-bearing", "stress-plate-build"):  # small, medium, large
        calls = results.get(f"session/{name}/window")
        accept = results.get(f"session/{name}/window-accept")
        if calls and accept:
            out.append(
                Result(
                    f"session/{name}/workflow",
                    {"total": calls.metrics["total"] + accept.metrics["total"]},
                )
            )
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="run cases whose name contains this")
    parser.add_argument("--save", type=Path, help="write the results as JSON")
    parser.add_argument("--compare", type=Path, help="show the change against saved results")
    args = parser.parse_args(argv[1:])
    results: dict[str, Result] = {}
    for name, run in cases():
        if args.only in name:
            for result in run():
                results[result.name] = result
    for result in workflow(results):
        results[result.name] = result
    before = json.loads(args.compare.read_text()) if args.compare else {}
    for name, result in results.items():
        print(name)
        for key, value in result.metrics.items():
            old = before.get(name, {}).get(key)
            change = ""
            if isinstance(old, float | int) and old and key != "calls":
                change = f"   was {old:10.3f}  ({value / old:6.2f}x)"
            print(f"    {key:16s} {value:10.3f}{change}")
    if args.save:
        args.save.write_text(
            json.dumps({n: r.metrics for n, r in results.items()}, indent=1, sort_keys=True) + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
