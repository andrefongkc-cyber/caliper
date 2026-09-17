"""Time the canvas in a real window: `uv run python tests/app/bench_canvas.py`.

Not a test (pytest doesn't collect it). It opens the main window, loads a synthetic sketch,
and times the frames a user actually causes, against a 120 Hz budget:

- cached repaint: nothing changed, the static layer is reused
- pointer move: hover hit-testing and snapping, then the repaint it triggers
- pan and zoom: the view changes, so the static layer is rebuilt
- one edit: `ModifyEntity` through the session, then the repaint

Run it once as is (Cocoa, Retina) and once with `QT_QPA_PLATFORM=offscreen` to compare.
Times are CPU time to handle the event and paint into Qt's backing store; they do not
include the compositor presenting the frame.
"""

import argparse
import math
import statistics
import sys
import time
from collections.abc import Callable
from types import MappingProxyType

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from caliper.app import theme
from caliper.app.main_window import MainWindow
from caliper.app.session import DocumentSession
from caliper.contracts.commands import ModifyEntity
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

BUDGET_120_HZ = 1000 / 120
BUDGET_60_HZ = 1000 / 60
CELL_MM = 20.0


def sketch(count: int) -> Document:
    """`count` entities on a square grid: rectangles, circles, lines, arcs; 1 in 20 a dimension."""
    entities: dict[EntityId, Entity] = {}
    side = math.ceil(math.sqrt(count))
    n = 0
    last_rectangle: EntityId | None = None
    while n < count:
        row, col = divmod(n, side)
        x, y = col * CELL_MM, row * CELL_MM
        n += 1
        eid = EntityId(f"e{n}")
        if n % 20 == 0 and last_rectangle is not None:
            entities[eid] = DistanceDimension(
                a=Ref(entity=last_rectangle, feature=Feature.BOTTOM_LEFT),
                b=Ref(entity=last_rectangle, feature=Feature.BOTTOM_RIGHT),
                orientation=DistanceOrientation.HORIZONTAL,
                offset=-3.0,
            )
            continue
        match n % 4:
            case 0:
                entities[eid] = Rectangle(corner=Point2(x=x + 2, y=y + 2), width=14.0, height=9.0)
                last_rectangle = eid
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


def timed(action: Callable[[], None]) -> float:
    start = time.perf_counter_ns()
    action()
    return (time.perf_counter_ns() - start) / 1e6


def summary(samples: list[float]) -> tuple[float, float]:
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return statistics.median(ordered), p95


def verdict(ms: float) -> str:
    if ms <= BUDGET_120_HZ:
        return "fits 120 Hz"
    if ms <= BUDGET_60_HZ:
        return "60 Hz only"
    return "drops frames"


def bench(app: QApplication, count: int, runs: int, warmup: int) -> list[tuple[str, list[float]]]:
    session = DocumentSession()
    window = MainWindow(session)
    session.setParent(window)
    window.confirm_discard = lambda: True  # type: ignore[method-assign]
    window.resize(1440, 900)
    window.show()
    deadline = time.monotonic() + 10
    while not (window.windowHandle() and window.windowHandle().isExposed()):
        app.processEvents()
        if time.monotonic() > deadline:
            raise SystemExit("the window never became visible")
    canvas = window.canvas

    session.replace(Bus(sketch(count)), None)
    app.processEvents()
    canvas.zoom_to_fit()
    app.processEvents()
    canvas.repaint()

    first_rectangle = next(
        i for i, e in session.document.entities.items() if isinstance(e, Rectangle)
    )
    w, h = canvas.width(), canvas.height()
    step = [0]

    def settle() -> None:
        app.processEvents()

    def pointer_handler() -> None:
        k = step[0]
        pos = QPointF((k * 37) % (w - 40) + 20, (k * 53) % (h - 40) + 20)
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            pos,
            canvas.mapToGlobal(pos),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(canvas, event)

    def pan() -> None:
        canvas.view.pan(3.0 if step[0] % 2 else -3.0, 0.0)

    def zoom() -> None:
        canvas.view.zoom_about(1.02 if step[0] % 2 else 1 / 1.02, w / 2, h / 2)

    def edit() -> None:
        width = 15.0 if step[0] % 2 else 14.0
        session.execute(ModifyEntity(id=first_rectangle, changes={"width": width}))

    results: dict[str, list[float]] = {
        "cached repaint": [],
        "pointer move: handler": [],
        "pointer move: repaint": [],
        "pointer move: total": [],
        "pan frame": [],
        "zoom frame": [],
        "edit: command": [],
        "edit: repaint": [],
        "edit: total": [],
    }
    for k in range(warmup + runs):
        step[0] = k
        keep = k >= warmup

        settle()
        repaint = timed(canvas.repaint)
        if keep:
            results["cached repaint"].append(repaint)

        settle()
        handler = timed(pointer_handler)
        paint = timed(canvas.repaint)
        if keep:
            results["pointer move: handler"].append(handler)
            results["pointer move: repaint"].append(paint)
            results["pointer move: total"].append(handler + paint)

        settle()
        frame = timed(lambda: (pan(), canvas.repaint()))
        if keep:
            results["pan frame"].append(frame)

        settle()
        frame = timed(lambda: (zoom(), canvas.repaint()))
        if keep:
            results["zoom frame"].append(frame)

        settle()
        command = timed(edit)
        paint = timed(canvas.repaint)
        if keep:
            results["edit: command"].append(command)
            results["edit: repaint"].append(paint)
            results["edit: total"].append(command + paint)

    window.close()
    window.deleteLater()
    app.processEvents()
    return list(results.items())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[2000, 10000])
    parser.add_argument("--runs", type=int, default=40)
    parser.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args(argv)

    app = QApplication([sys.argv[0]])
    theme.apply(app)
    screen = QGuiApplication.primaryScreen()
    print(
        f"platform {QGuiApplication.platformName()} · screen {screen.name()} · "
        f"{screen.refreshRate():.0f} Hz · device pixel ratio {screen.devicePixelRatio():g} · "
        f"window 1440x900 · {args.runs} runs after {args.warmup} warm-up"
    )
    print(f"budget: {BUDGET_120_HZ:.2f} ms per frame at 120 Hz, {BUDGET_60_HZ:.2f} ms at 60 Hz\n")

    for count in args.sizes:
        rows = bench(app, count, args.runs, args.warmup)
        print(f"{count:,} entities")
        print(f"  {'frame':<24}{'median':>10}{'p95':>10}   verdict (median)")
        for name, samples in rows:
            median, p95 = summary(samples)
            print(f"  {name:<24}{median:>8.2f}ms{p95:>8.2f}ms   {verdict(median)}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
