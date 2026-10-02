"""Performance V2.2's cases: the costs the 2026-10-01 plan measured, as benchmarks.

`bench/perf.py` runs these with the rest (`--only ui/`, `--only 3d/`, and so on). Each case
reports time and, where the plan names one, a count of the work done: palette refilters,
browser rebuilds, history rebuilds, grid builds, scene builds, kernel calls, prisms rebuilt,
and the solver's Duals and rule matches. Counts are taken by wrapping the function for the
run only, as `engine_session` counts the solver's evaluations; nothing in Caliper changes.

- `ui/*`: the window, offscreen, 1280 x 800. Opening a document, the sketch browser
  rebuilding, a selection change, Select All, a tab round trip, startup, an edit, undo and
  redo, the stress plate's accepted proposal undone and redone, a long History, and the first
  pointer move after an edit (the picking grid built again) against a steady one.
- `file/*` and `replay/*`: saving and opening the stress plate and 10,000 entities; the
  command-line replay of the stress plate (schema 1) and of a part (schema 2), in a new
  process, so its start-up is included.
- `solver/*`: counts of the solver's work over the stress plate and the 150-line chain.
- `v2/features-chain`: ten sketches extruded, the first edited: what recompute rebuilds.
- `3d/*`: frames at three mesh sizes, an orbit, and a line drawn in a 3D sketch with the
  24-hole part behind it.
- `session/stress-plate-build/window-3d`: Claude Desktop's stress plate in the 3D tab.
"""

import math
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import cache
from pathlib import Path
from types import MappingProxyType

from caliper.contracts.commands import CreateCircle
from caliper.contracts.document import (
    Arc,
    Circle,
    DistanceDimension,
    DistanceOrientation,
    Document,
    Entity,
    EntityId,
    Feature,
    Line,
    Point2,
    Rectangle,
    Ref,
)
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot

CELL_MM = 20.0


def _result(name: str, metrics: dict[str, float]) -> object:
    from perf import Result

    return Result(name, metrics)


@contextmanager
def counting(owner: object, name: str, *also: object) -> Iterator[list[int]]:
    """Count calls to `owner.name` while the block runs; `[n]` holds the count. `also` names
    other modules that imported the same function by name, counted with it.

    A slot connected to a signal keeps the function it was connected with, so a window's
    slots are counted only if the window is built inside the block (then zero the count)."""
    owners = (owner, *also)
    reals = [getattr(o, name) for o in owners]
    count = [0]

    def wrap(real: Callable[..., object]) -> Callable[..., object]:
        def counted(*args: object, **kwargs: object) -> object:
            count[0] += 1
            return real(*args, **kwargs)

        return counted

    for o, real in zip(owners, reals, strict=True):
        setattr(o, name, wrap(real))
    try:
        yield count
    finally:
        for o, real in zip(owners, reals, strict=True):
            setattr(o, name, real)


def mixed(count: int) -> Document:
    """`count` entities on a grid: rectangles, circles, lines, arcs; 1 in 20 a dimension. The
    sketch `tests/app/bench_canvas.py` times, unconstrained."""
    entities: dict[EntityId, Entity] = {}
    side = math.ceil(math.sqrt(count))
    last: EntityId | None = None
    for n in range(1, count + 1):
        row, col = divmod(n - 1, side)
        x, y = col * CELL_MM, row * CELL_MM
        eid = EntityId(f"e{n}")
        if n % 20 == 0 and last is not None:
            entities[eid] = DistanceDimension(
                a=Ref(entity=last, feature=Feature.BOTTOM_LEFT),
                b=Ref(entity=last, feature=Feature.BOTTOM_RIGHT),
                orientation=DistanceOrientation.HORIZONTAL,
                offset=-3.0,
            )
            continue
        match n % 4:
            case 0:
                entities[eid] = Rectangle(corner=Point2(x=x + 2, y=y + 2), width=14.0, height=9.0)
                last = eid
            case 1:
                entities[eid] = Circle(center=Point2(x=x + 10, y=y + 10), radius=6.0)
            case 2:
                entities[eid] = Line(start=Point2(x=x + 2, y=y + 3), end=Point2(x=x + 17, y=y + 16))
            case _:
                entities[eid] = Arc(
                    center=Point2(x=x + 10, y=y + 8),
                    radius=7.0,
                    start_angle=15.0,
                    sweep_angle=150.0,
                )
    return Document(entities=MappingProxyType(entities), next_id=count + 1)


@cache
def stress_plate() -> tuple[Document, tuple[object, ...]]:
    """The stress plate as Claude Desktop built it, and the resolved commands that built it."""
    from perf import engine_session

    _, draft, _ = engine_session("stress-plate-build")
    assert draft.workspace is not None
    return draft.workspace.document, tuple(draft.commands)


def _window_with(document: Document, mode: str = "2d") -> tuple[object, object]:
    from perf import _window

    app, window = _window()
    window.set_mode(mode)  # type: ignore[attr-defined]
    window.session.replace(Bus(document), None)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    if mode == "2d":
        window.canvas.zoom_to_fit()  # type: ignore[attr-defined]
        app.processEvents()  # type: ignore[attr-defined]
        window.canvas.repaint()  # type: ignore[attr-defined]
    return app, window


def _dispose(window: object) -> None:
    from perf import _dispose as dispose

    dispose(window)


def _median_ms(samples: list[float]) -> float:
    return 1e3 * statistics.median(samples)


# --- The window: documents, the browser, the palette, panels ---------------------------------


def browser_rebuild() -> list[object]:
    """The sketch browser filled from scratch, as opening, New, and a tab switch do."""
    from caliper.app.panels.browser import SketchBrowser

    found = []
    for count, runs in ((2000, 3), (10000, 1)):
        _, window = _window_with(mixed(count))
        samples = []
        with counting(SketchBrowser, "_insert") as inserts:
            for _ in range(runs):
                started = time.perf_counter()
                window.browser.rebuild()  # type: ignore[attr-defined]
                samples.append(time.perf_counter() - started)
        found.append(
            _result(
                f"ui/browser-rebuild/{count}",
                {
                    "rebuild_ms": _median_ms(samples),
                    "rows": float(len(window.browser.items)),  # type: ignore[attr-defined]
                    "inserts": inserts[0] / runs,
                },
            )
        )
        _dispose(window)
    return found


def open_documents() -> list[object]:
    """Opening a document in the window: read the file, show it (the panels fill, the view
    fits), paint. As File > Open does, without touching the user's recent-files list."""
    from perf import _window

    from caliper.app.panels.browser import SketchBrowser

    found = []
    for name, document in (
        ("stress-plate", stress_plate()[0]),
        ("2000", mixed(2000)),
        ("10000", mixed(10000)),
    ):
        text = snapshot.dumps(document)
        with counting(SketchBrowser, "rebuild") as rebuilds:
            app, window = _window()
            rebuilds[0] = 0
            started = time.perf_counter()
            opened = snapshot.loads(text)
            read = time.perf_counter() - started
            window.session.replace(Bus(opened), None)  # type: ignore[attr-defined]
            app.processEvents()  # type: ignore[attr-defined]
            window.canvas.zoom_to_fit()  # type: ignore[attr-defined]
            window.canvas.repaint()  # type: ignore[attr-defined]
            took = time.perf_counter() - started
        found.append(
            _result(
                f"ui/open/{name}",
                {"total_ms": 1e3 * took, "loads_ms": 1e3 * read, "browser_rebuilds": rebuilds[0]},
            )
        )
        _dispose(window)
    return found


def selection() -> list[object]:
    """One selection change (a circle picked, then cleared), and Select All, on the stress
    plate: time per change, and the command palette's refilters."""
    from caliper.app.palette import CommandPalette

    document = stress_plate()[0]
    app, window = _window_with(document)
    session = window.session  # type: ignore[attr-defined]
    circle = next(i for i, e in sorted(document.entities.items()) if isinstance(e, Circle))
    found = []
    for name, pick, runs in (
        ("selection-change", lambda: session.set_selection(frozenset({circle})), 20),
        ("select-all", lambda: window.select_all_action.trigger(), 6),  # type: ignore[attr-defined]
    ):
        samples = []
        with counting(CommandPalette, "_refilter") as refilters:
            for _ in range(runs):
                started = time.perf_counter()
                pick()
                app.processEvents()  # type: ignore[attr-defined]
                session.set_selection(frozenset())
                app.processEvents()  # type: ignore[attr-defined]
                samples.append((time.perf_counter() - started) / 2)
        found.append(
            _result(
                f"ui/{name}/stress-plate",
                {"change_ms": _median_ms(samples), "refilters": refilters[0] / (2 * runs)},
            )
        )
    _dispose(window)
    return found


def tab_switch() -> object:
    """2D to 3D and back, the stress plate in the 2D tab and an empty part in 3D."""
    from caliper.app.palette import CommandPalette
    from caliper.app.panels.browser import SketchBrowser
    from caliper.app.panels.features import FeatureTree
    from caliper.app.viewport import scene3d

    samples, runs = [], 6
    with (
        counting(CommandPalette, "_refilter") as refilters,
        counting(SketchBrowser, "rebuild") as browsers,
        counting(FeatureTree, "rebuild") as trees,
        counting(scene3d.Scene, "of") as scenes,
    ):
        app, window = _window_with(stress_plate()[0])
        for counter in (refilters, browsers, trees, scenes):
            counter[0] = 0
        for _ in range(runs):
            started = time.perf_counter()
            window.set_mode("3d")  # type: ignore[attr-defined]
            app.processEvents()  # type: ignore[attr-defined]
            window.set_mode("2d")  # type: ignore[attr-defined]
            app.processEvents()  # type: ignore[attr-defined]
            window.canvas.repaint()  # type: ignore[attr-defined]
            samples.append(time.perf_counter() - started)
    _dispose(window)
    return _result(
        "ui/tab-switch/stress-plate",
        {
            "round_trip_ms": _median_ms(samples),
            "refilters": refilters[0] / runs,
            "browser_rebuilds": browsers[0] / runs,
            "feature_rebuilds": trees[0] / runs,
            "scene_builds": scenes[0] / runs,
        },
    )


def startup() -> object:
    """A new window, built and shown (the 3D tab, as the app starts). The first window in a
    process also renders the icons; this is the median of three after it."""
    from PySide6.QtWidgets import QApplication

    from caliper.app import theme
    from caliper.app.main_window import MainWindow
    from caliper.app.palette import CommandPalette

    app = QApplication.instance() or QApplication([])
    theme.apply(app)  # type: ignore[arg-type]
    samples, windows = [], []
    with counting(CommandPalette, "_refilter") as refilters:
        for _ in range(4):
            started = time.perf_counter()
            window = MainWindow()
            window.resize(1280, 800)
            window.show()
            app.processEvents()
            samples.append(time.perf_counter() - started)
            windows.append(window)
    for window in windows:
        _dispose(window)
    return _result(
        "ui/startup",
        {"window_ms": _median_ms(samples[1:]), "refilters": refilters[0] / len(samples)},
    )


def editing() -> list[object]:
    """A dimension's value typed on the stress plate (solve, panels, repaint), then undo and
    redo of it, with the per-change work the panels do."""
    from caliper.app.palette import CommandPalette
    from caliper.app.panels.checks import ChecksPanel
    from caliper.app.panels.features import FeatureTree
    from caliper.app.panels.history import HistoryList
    from caliper.contracts.commands import ModifyEntity
    from caliper.contracts.document import RadialDimension

    document = stress_plate()[0]
    dimension = next(
        i
        for i, e in sorted(document.entities.items())
        if isinstance(e, DistanceDimension | RadialDimension)
    )
    value = document.entities[dimension].value  # type: ignore[union-attr]
    edits, runs = [], 10
    with (
        counting(CommandPalette, "_refilter") as refilters,
        counting(HistoryList, "rebuild") as histories,
        counting(ChecksPanel, "refresh") as checks,
        counting(FeatureTree, "rebuild") as trees,
    ):
        app, window = _window_with(document)
        session = window.session  # type: ignore[attr-defined]
        for counter in (refilters, histories, checks, trees):
            counter[0] = 0
        for k in range(runs):
            started = time.perf_counter()
            session.execute(
                ModifyEntity(id=dimension, changes={"value": value + (0.5 if k % 2 == 0 else 0.0)})
            )
            app.processEvents()  # type: ignore[attr-defined]
            window.canvas.repaint()  # type: ignore[attr-defined]
            edits.append(time.perf_counter() - started)
    undos, redos = [], []
    for _ in range(4):
        for samples, action in ((undos, window.undo_action), (redos, window.redo_action)):  # type: ignore[attr-defined]
            started = time.perf_counter()
            action.trigger()
            app.processEvents()  # type: ignore[attr-defined]
            window.canvas.repaint()  # type: ignore[attr-defined]
            samples.append(time.perf_counter() - started)
    _dispose(window)
    return [
        _result(
            "ui/edit/stress-plate",
            {
                "edit_ms": _median_ms(edits),
                "refilters": refilters[0] / runs,
                "history_rebuilds": histories[0] / runs,
                "checks_refreshes": checks[0] / runs,
                "feature_rebuilds": trees[0] / runs,
            },
        ),
        _result(
            "ui/undo-redo/stress-plate",
            {"undo_ms": _median_ms(undos), "redo_ms": _median_ms(redos)},
        ),
    ]


def accepted_undo_redo() -> object:
    """Claude Desktop's stress plate accepted in the window, then undone and redone whole."""
    from perf import _serve, _window, session_calls

    from caliper.ai.bridge import Request, encode_request
    from caliper.app.panels.browser import SketchBrowser

    app, window = _window()
    host = _serve(window)
    for tool, arguments in session_calls("stress-plate-build"):
        request = Request(client="claude-ai", tool=tool, arguments=arguments)
        host.handle(encode_request(request).rstrip(b"\n"))  # type: ignore[attr-defined]
        app.processEvents()  # type: ignore[attr-defined]
    window.proposal_card.accept_button.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    undos, redos = [], []
    with counting(SketchBrowser, "_insert") as inserts:
        for _ in range(3):
            for samples, action in ((undos, window.undo_action), (redos, window.redo_action)):  # type: ignore[attr-defined]
                started = time.perf_counter()
                action.trigger()
                app.processEvents()  # type: ignore[attr-defined]
                window.canvas.repaint()  # type: ignore[attr-defined]
                samples.append(time.perf_counter() - started)
    host.close()  # type: ignore[attr-defined]
    _dispose(window)
    return _result(
        "ui/redo-accepted/stress-plate",
        {"undo_ms": _median_ms(undos), "redo_ms": _median_ms(redos), "inserts": inserts[0] / 3},
    )


def long_history() -> object:
    """One more change after 2,000 recorded ones: what the History panel adds to each."""
    from perf import _window

    from caliper.app.panels.history import HistoryList
    from caliper.app.session import HistoryEntry
    from caliper.contracts.commands import CreateCircle

    samples, runs = [], 10
    with counting(HistoryList, "rebuild") as rebuilds:
        app, window = _window()
        session = window.session  # type: ignore[attr-defined]
        now = time.time()
        session._history = [  # a long session, set up directly rather than by 2,000 commands
            HistoryEntry(label="Create Circle", author="You", at=now) for _ in range(2000)
        ]
        session._history_position = 2000
        session.history_changed.emit()
        app.processEvents()  # type: ignore[attr-defined]
        rebuilds[0] = 0
        for k in range(runs):
            started = time.perf_counter()
            session.execute(CreateCircle(center=Point2(x=10.0 * k, y=0.0), radius=1.0))
            app.processEvents()  # type: ignore[attr-defined]
            samples.append(time.perf_counter() - started)
    _dispose(window)
    return _result(
        "ui/history/2000",
        {"change_ms": _median_ms(samples), "history_rebuilds": rebuilds[0] / runs},
    )


def pointer_after_edit() -> list[object]:
    """The first pointer move after an edit (the picking grid is built for the new document)
    against the next one, at 2,000 and 10,000 entities."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from caliper.contracts.commands import ModifyEntity
    from caliper.engine import spatial

    found = []
    for count in (2000, 10000):
        document = mixed(count)
        app, window = _window_with(document)
        canvas = window.canvas  # type: ignore[attr-defined]
        rectangle = next(i for i, e in document.entities.items() if isinstance(e, Rectangle))
        w, h = canvas.width(), canvas.height()

        def move(k: int, canvas: object = canvas, w: int = w, h: int = h) -> None:
            at = QPointF((k * 37) % (w - 40) + 20, (k * 53) % (h - 40) + 20)
            event = QMouseEvent(
                QEvent.Type.MouseMove,
                at,
                canvas.mapToGlobal(at),  # type: ignore[attr-defined]
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(canvas, event)  # type: ignore[arg-type]

        first, steady, runs = [], [], 12
        with counting(spatial, "Grid") as grids:
            for k in range(runs):
                window.session.execute(  # type: ignore[attr-defined]
                    ModifyEntity(id=rectangle, changes={"width": 14.0 + (k % 2)})
                )
                app.processEvents()  # type: ignore[attr-defined]
                canvas.repaint()
                for samples, step in ((first, 2 * k), (steady, 2 * k + 1)):
                    started = time.perf_counter()
                    move(step)
                    samples.append(time.perf_counter() - started)
        found.append(
            _result(
                f"ui/pointer-after-edit/{count}",
                {
                    "first_move_ms": _median_ms(first),
                    "steady_move_ms": _median_ms(steady),
                    "grid_builds": grids[0] / runs,
                },
            )
        )
        _dispose(window)
    return found


# --- Files and replay ------------------------------------------------------------------------


def files() -> list[object]:
    """Saving and opening the stress plate and 10,000 entities, in memory; and how often
    decoding looked up a class's type hints."""
    from caliper.engine.io import codec

    found = []
    for name, document, runs in (
        ("stress-plate", stress_plate()[0], 7),
        ("10000", mixed(10000), 3),
    ):
        text = snapshot.dumps(document)
        dumped, loaded = [], []
        with counting(codec, "get_type_hints") as hints:
            for _ in range(runs):
                started = time.perf_counter()
                snapshot.dumps(document)
                dumped.append(time.perf_counter() - started)
                started = time.perf_counter()
                snapshot.loads(text)
                loaded.append(time.perf_counter() - started)
        found.append(
            _result(
                f"file/{name}",
                {
                    "dumps_ms": _median_ms(dumped),
                    "loads_ms": _median_ms(loaded),
                    "size_kib": len(text.encode()) / 1024,
                    "type_hint_lookups": hints[0] / runs,
                },
            )
        )
    return found


def cli_replay() -> list[object]:
    """`python -m caliper.engine replay` in a new process: the stress plate's 265 commands
    (schema 1), and a part started with no sketch (schema 2). Python's start-up is included."""
    from caliper.contracts.commands import CreateExtrude, CreateRectangle, CreateSketch
    from caliper.contracts.document import Plane
    from caliper.engine import part
    from caliper.engine.io import script

    bus = Bus(part.no_sketch())
    resolved = tuple(
        bus.execute(command).command  # type: ignore[union-attr]
        for command in (
            CreateSketch(plane=Plane.XY),
            CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0),
            CreateExtrude(depth=10.0),
        )
    )
    folder = Path(tempfile.mkdtemp())
    found = []
    for name, commands, empty in (
        ("stress-plate", stress_plate()[1], False),
        ("part-schema2", resolved, True),
    ):
        path = folder / f"{name}.script.json"
        path.write_text(script.dumps(commands, empty=empty))  # type: ignore[arg-type]
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            done = subprocess.run(
                [sys.executable, "-m", "caliper.engine", "replay", str(path)], capture_output=True
            )
            samples.append(time.perf_counter() - started)
            assert done.returncode == 0, done.stderr
        found.append(_result(f"replay/cli/{name}", {"total": statistics.median(samples)}))
    return found


# --- The solver and recompute ------------------------------------------------------------


def solver_counts() -> list[object]:
    """How much work the solver does over the stress plate's session and the 150-line chain:
    Duals made, rule matches, point lookups, Newton solves, rows factored, redundancy checks."""
    from perf import many_constraints, session_calls

    from caliper.ai.draft import Draft
    from caliper.engine.commands import validation
    from caliper.engine.constraints import ad, linalg, model, relations, sketch, suggest

    def counted(run: Callable[[], object]) -> dict[str, float]:
        with (
            counting(ad.Dual, "__init__") as duals,
            counting(relations, "match", sketch, validation, suggest) as matches,
            counting(model.Frame, "point") as points,
            counting(sketch, "_newton") as newtons,
            counting(linalg.RowBasis, "add") as rows,
            counting(sketch, "_redundancy") as redundancy,
        ):
            run()
        return {
            "duals": float(duals[0]),
            "matches": float(matches[0]),
            "points": float(points[0]),
            "newton_solves": float(newtons[0]),
            "rows_added": float(rows[0]),
            "redundancy_checks": float(redundancy[0]),
        }

    def plate() -> None:
        draft, base = Draft(), Bus().document
        for tool, arguments in session_calls("stress-plate-build"):
            draft.call(base, (), tool, arguments)

    return [
        _result("solver/counts/stress-plate", counted(plate)),
        _result("solver/counts/chain-150", counted(many_constraints)),
    ]


def features_chain() -> object:
    """Ten sketches, a 20 x 10 rectangle in each, each extruded: then the first sketch's
    rectangle widened, the last one's, and a label moved. The counts are what the kernel
    built again: prisms, and the solids joined after them."""
    from caliper.contracts.commands import (
        CreateDistanceDimension,
        CreateExtrude,
        CreateRectangle,
        CreateSketch,
        ModifyEntity,
    )
    from caliper.contracts.document import Plane
    from caliper.engine import features
    from caliper.engine.geometry import default_kernel
    from caliper.engine.geometry.fake_kernel import FakeKernel

    real = default_kernel() or FakeKernel()

    class Counting:
        def __init__(self) -> None:
            self.prisms = 0
            self.unions = 0

        def __getattr__(self, name: str) -> object:
            return getattr(real, name)

        def extrude(self, *args: object) -> object:
            self.prisms += 1
            return real.extrude(*args)  # type: ignore[arg-type]

        def union(self, *args: object) -> object:
            self.unions += 1
            return real.union(*args)  # type: ignore[arg-type]

    kernel = Counting()
    features.forget()
    bus = Bus(kernel=kernel)  # type: ignore[arg-type]
    rectangles, dimensions = [], []
    for k in range(10):
        sketch = bus.document.features[0].id if k == 0 else None
        if k:
            (sketch,) = bus.execute(CreateSketch(plane=Plane.XY)).created_ids  # type: ignore[union-attr]
        (rectangle,) = bus.execute(  # type: ignore[union-attr]
            CreateRectangle(
                corner=Point2(x=30.0 * k, y=0.0), width=20.0, height=10.0, sketch=sketch
            )
        ).created_ids
        (dimension,) = bus.execute(  # type: ignore[union-attr]
            CreateDistanceDimension(
                a=Ref(entity=rectangle, feature=Feature.BOTTOM_LEFT),
                b=Ref(entity=rectangle, feature=Feature.BOTTOM_RIGHT),
                orientation=DistanceOrientation.HORIZONTAL,
                offset=-5.0,
            )
        ).created_ids
        bus.execute(CreateExtrude(depth=5.0, sketch=sketch))
        rectangles.append(rectangle)
        dimensions.append(dimension)
    bus.queries.solid_properties()
    metrics: dict[str, float] = {}
    for name, change in (
        ("first", ModifyEntity(id=rectangles[0], changes={"width": 21.0})),
        ("last", ModifyEntity(id=rectangles[-1], changes={"width": 21.0})),
        ("label", ModifyEntity(id=dimensions[0], changes={"offset": -7.0})),
    ):
        prisms, unions = kernel.prisms, kernel.unions
        started = time.perf_counter()
        bus.execute(change)
        bus.queries.solid_properties()
        metrics[f"{name}_ms"] = 1e3 * (time.perf_counter() - started)
        metrics[f"{name}_prisms"] = float(kernel.prisms - prisms)
        metrics[f"{name}_unions"] = float(kernel.unions - unions)
    metrics["analytic"] = float(isinstance(real, FakeKernel))
    return _result("v2/features-chain", metrics)


# --- 3D ------------------------------------------------------------------------------------


def _holes(columns: int, rows: int) -> Document:
    from caliper.contracts.commands import CreateExtrude, CreateRectangle

    bus = Bus()
    bus.execute(
        CreateRectangle(corner=Point2(x=0.0, y=0.0), width=40.0 * columns, height=40.0 * rows)
    )
    for i in range(columns):
        for j in range(rows):
            bus.execute(CreateCircle(center=Point2(x=20.0 + 40 * i, y=20.0 + 40 * j), radius=8.0))
    bus.execute(CreateExtrude(depth=10.0))
    return bus.document


def frames_3d() -> list[object]:
    """Frames of the 3D view at 1280 x 800 for parts of about 2,600, 8,700, and 20,700
    triangles; the scene built from the mesh; and the camera's axes worked out per frame."""
    from caliper.app.viewport import camera3d, scene3d
    from caliper.app.viewport.camera3d import Camera
    from caliper.engine import features
    from caliper.engine.geometry import default_kernel

    found = []
    for columns, rows in ((6, 4), (10, 8), (16, 12)):
        features.forget()
        _, window = _window_with(_holes(columns, rows), mode="3d")
        view = window.view3d  # type: ignore[attr-defined]
        view.resize(1280, 800)
        view.refresh()
        mesh = view.mesh
        assert mesh is not None
        builds = []
        for _ in range(3):
            started = time.perf_counter()
            scene3d.Scene.of(window.session.document, mesh)  # type: ignore[attr-defined]
            builds.append(time.perf_counter() - started)
        scene_ms = _median_ms(builds)
        view.camera = Camera().fitted(view.scene.box(), 1280, 800)
        view.frame_ms.clear()
        for _ in range(10):
            view.repaint()
        with counting(camera3d.Camera, "axes") as axes:
            view.repaint()
        found.append(
            _result(
                f"3d/frame/{len(mesh.triangles)}",
                {
                    "frame_ms": statistics.median(view.frame_ms),
                    "scene_ms": scene_ms,
                    "axes_per_frame": float(axes[0]),
                    "triangles": float(len(mesh.triangles)),
                    "occt": float(default_kernel() is not None),
                },
            )
        )
        _dispose(window)
    return found


def orbit_3d() -> object:
    """Orbiting the 24-hole part: frames for a camera change only, and whether any scene was
    built again for it."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from caliper.app.viewport import scene3d
    from caliper.engine import features

    features.forget()
    _, window = _window_with(_holes(6, 4), mode="3d")
    view = window.view3d  # type: ignore[attr-defined]
    view.refresh()
    view.frame_ms.clear()
    with counting(scene3d.Scene, "of") as scenes:
        QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(300, 300))
        for k in range(1, 21):
            QTest.mouseMove(view, QPoint(300 + 6 * k, 300 - 2 * k))
            view.repaint()
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPoint(420, 260))
    frames = list(view.frame_ms)
    _dispose(window)
    return _result(
        "3d/orbit/holes-24",
        {"frame_ms": statistics.median(frames), "scene_builds": float(scenes[0])},
    )


def sketch_over_part() -> object:
    """A line drawn in a sketch on Front, edited in 3D, with the 24-hole part behind it: the
    solid doesn't change, so neither should the scene behind."""
    from perf import _window

    from caliper.app.viewport import scene3d
    from caliper.contracts.commands import CreateExtrude, CreateLine, CreateRectangle
    from caliper.contracts.document import Plane
    from caliper.engine import features
    from caliper.engine.geometry import default_kernel
    from caliper.engine.geometry.fake_kernel import FakeKernel

    features.forget()
    app, window = _window()
    window.set_mode("3d")  # type: ignore[attr-defined]
    session = window.session  # type: ignore[attr-defined]
    window.new_sketch(Plane.XY)  # type: ignore[attr-defined]
    session.execute(CreateRectangle(corner=Point2(x=0.0, y=0.0), width=240.0, height=160.0))
    for i in range(6):
        for j in range(4):
            session.execute(
                CreateCircle(center=Point2(x=20.0 + 40 * i, y=20.0 + 40 * j), radius=8.0)
            )
    window.finish_sketch()  # type: ignore[attr-defined]
    session.execute(CreateExtrude(depth=10.0))
    app.processEvents()  # type: ignore[attr-defined]
    window.new_sketch(Plane.XZ)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    window.canvas.repaint()  # type: ignore[attr-defined]
    kernel = type(default_kernel() or FakeKernel())
    samples, runs = [], 8
    with counting(scene3d.Scene, "of") as scenes, counting(kernel, "volume") as volumes:
        for k in range(runs):
            started = time.perf_counter()
            session.execute(
                CreateLine(start=Point2(x=10.0 * k, y=20.0), end=Point2(x=10.0 * k + 5, y=40.0))
            )
            app.processEvents()  # type: ignore[attr-defined]
            window.canvas.repaint()  # type: ignore[attr-defined]
            samples.append(time.perf_counter() - started)
    _dispose(window)
    return _result(
        "3d/sketch-edit-over-part/holes-24",
        {
            "edit_ms": _median_ms(samples),
            "scene_builds": scenes[0] / runs,
            "volume_calls": volumes[0] / runs,
        },
    )


def session_in_3d() -> list[object]:
    """Claude Desktop's stress plate in the 3D tab: the part's one sketch on Top, faced while
    each proposal is shown, then Accept."""
    from perf import _serve, _window, session_calls, spread

    from caliper.ai.bridge import Request, encode_request

    app, window = _window()
    window.set_mode("3d")  # type: ignore[attr-defined]
    window.session.replace(Bus(), None)  # type: ignore[attr-defined]  # ids as recorded: e0 first
    app.processEvents()  # type: ignore[attr-defined]
    host = _serve(window)
    handled, events = [], []
    for tool, arguments in session_calls("stress-plate-build"):
        line = encode_request(Request(client="claude-ai", tool=tool, arguments=arguments))
        started = time.perf_counter()
        host.handle(line.rstrip(b"\n"))  # type: ignore[attr-defined]
        middle = time.perf_counter()
        app.processEvents()  # type: ignore[attr-defined]
        handled.append(middle - started)
        events.append(time.perf_counter() - middle)
    calls = spread([a + b for a, b in zip(handled, events, strict=True)])
    calls["ui_share_ms"] = 1e3 * statistics.median(events)
    started = time.perf_counter()
    window.proposal_card.accept_button.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    accept = time.perf_counter() - started
    host.close()  # type: ignore[attr-defined]
    _dispose(window)
    return [
        _result("session/stress-plate-build/window-3d", calls),
        _result("session/stress-plate-build/window-3d-accept", {"total": accept}),
    ]


def cases() -> list[tuple[str, Callable[[], list[object]]]]:
    return [
        ("ui/browser-rebuild", browser_rebuild),
        ("ui/open", open_documents),
        ("ui/selection", selection),
        ("ui/tab-switch", lambda: [tab_switch()]),
        ("ui/startup", lambda: [startup()]),
        ("ui/edit", editing),
        ("ui/redo-accepted", lambda: [accepted_undo_redo()]),
        ("ui/history", lambda: [long_history()]),
        ("ui/pointer-after-edit", pointer_after_edit),
        ("file/", files),
        ("replay/cli", cli_replay),
        ("solver/counts", solver_counts),
        ("v2/features-chain", lambda: [features_chain()]),
        ("3d/frame", frames_3d),
        ("3d/orbit", lambda: [orbit_3d()]),
        ("3d/sketch-edit-over-part", lambda: [sketch_over_part()]),
        ("session/stress-plate-build/window-3d", session_in_3d),
    ]
