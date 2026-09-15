"""pytest-qt fixtures for the shell. Runs offscreen: QT_QPA_PLATFORM=offscreen.

The Linux core job installs no Qt, so every Qt test module is skipped there; the macOS app
job runs them.
"""

import importlib.util
import math
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

HAVE_QT = all(importlib.util.find_spec(name) for name in ("PySide6", "pytestqt"))
QT_FREE = {"test_viewport_math.py"}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "bus(stub_unbuilt=False, complete_queries=False): configure the RecordingBus fixture",
    )


def pytest_ignore_collect(collection_path: Path) -> bool | None:
    if not HAVE_QT and collection_path.suffix == ".py" and collection_path.name not in QT_FREE:
        return collection_path.name.startswith("test_")
    return None


if HAVE_QT:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from pytestqt.qtbot import QtBot

    from caliper.app import theme
    from caliper.app.main_window import MainWindow
    from caliper.app.session import DocumentSession
    from caliper.app.viewport.canvas import Canvas
    from caliper.contracts.commands import (
        Applied,
        Command,
        CommandResult,
        DeleteEntities,
        Delta,
        MoveEntities,
    )
    from caliper.contracts.document import (
        Arc,
        Circle,
        DistanceDimension,
        EntityId,
        Feature,
        Line,
        Point2,
        Rectangle,
        Ref,
    )
    from caliper.contracts.errors import Error, ErrorCode
    from caliper.contracts.queries import Queries
    from caliper.engine.commands.bus import Bus
    from caliper.engine.queries import DocumentQueries

    class RecordingBus(Bus):
        """The real bus, plus a record of every command sent.

        `stub_unbuilt=True` answers MoveEntities and DeleteEntities (not built in the
        engine yet) with an empty Applied, so tests can check what the shell sends.
        `complete_queries=True` swaps in `CompletedQueries`.
        """

        def __init__(self, *, stub_unbuilt: bool = False, complete_queries: bool = False):
            super().__init__()
            self.sent: list[Command] = []
            self._stub = stub_unbuilt
            self._complete = complete_queries

        def execute(self, command: Command, *, merge_key: str | None = None) -> CommandResult:
            self.sent.append(command)
            if self._stub and isinstance(command, MoveEntities | DeleteEntities):
                label = type(command).__name__
                return Applied(
                    command=command,
                    delta=Delta.empty(self.document.next_id),
                    label=label,
                    created_ids=(),
                )
            return super().execute(command, merge_key=merge_key)

        @property
        def queries(self) -> Queries:
            if self._complete:
                return CompletedQueries(self.document)
            return super().queries

    class CompletedQueries(DocumentQueries):
        """Test-only stand-ins for the point queries Stream A hasn't built yet."""

        def feature_point(self, ref: Ref) -> Point2 | Error:
            entity = self._document.entities.get(ref.entity)
            points = _features(entity) if entity is not None else {}
            if ref.feature not in points:
                return Error(code=ErrorCode.REFERENCE_INVALID_FEATURE, message="no such feature")
            return points[ref.feature]

        def nearest_feature(self, point: Point2, tolerance: float) -> Ref | None:
            best: tuple[float, EntityId, Feature] | None = None
            for id, entity in sorted(self._document.entities.items()):
                for feature, at in sorted(_features(entity).items()):
                    distance = math.hypot(at.x - point.x, at.y - point.y)
                    if distance <= tolerance and (best is None or distance < best[0]):
                        best = (distance, id, feature)
            return None if best is None else Ref(entity=best[1], feature=best[2])

        def dimension_value(self, id: EntityId) -> float | Error:
            entity = self._document.entities.get(id)
            if not isinstance(entity, DistanceDimension):
                return Error(code=ErrorCode.ENTITY_WRONG_KIND, message="not a distance")
            a, b = self.feature_point(entity.a), self.feature_point(entity.b)
            assert isinstance(a, Point2)
            assert isinstance(b, Point2)
            return math.hypot(b.x - a.x, b.y - a.y)

    def _features(entity: object) -> dict[Feature, Point2]:
        match entity:
            case Line(start=s, end=e):
                mid = Point2(x=(s.x + e.x) / 2, y=(s.y + e.y) / 2)
                return {Feature.START: s, Feature.END: e, Feature.MID: mid}
            case Circle(center=c) | Arc(center=c):
                return {Feature.CENTER: c}
            case Rectangle(corner=c, width=w, height=h):
                return {
                    Feature.BOTTOM_LEFT: c,
                    Feature.BOTTOM_RIGHT: Point2(x=c.x + w, y=c.y),
                    Feature.TOP_RIGHT: Point2(x=c.x + w, y=c.y + h),
                    Feature.TOP_LEFT: Point2(x=c.x, y=c.y + h),
                    Feature.CENTER: Point2(x=c.x + w / 2, y=c.y + h / 2),
                }
        return {}

    Modifier = Qt.KeyboardModifier
    NO_MODIFIER = Qt.KeyboardModifier.NoModifier

    class Driver:
        """Mouse and keyboard input in model coordinates, sent through real Qt events."""

        def __init__(self, qtbot: QtBot, window: MainWindow) -> None:
            self.qtbot = qtbot
            self.window = window
            self.canvas: Canvas = window.canvas
            self.buttons = Qt.MouseButton.NoButton

        def at(self, x: float, y: float) -> QPoint:
            wx, wy = self.canvas.view.to_widget(Point2(x=x, y=y))
            return QPoint(round(wx), round(wy))

        def press(self, x: float, y: float, modifier: Modifier = NO_MODIFIER) -> None:
            self.buttons = Qt.MouseButton.LeftButton
            self.qtbot.mousePress(self.canvas, Qt.MouseButton.LeftButton, modifier, self.at(x, y))

        def release(self, x: float, y: float, modifier: Modifier = NO_MODIFIER) -> None:
            self.buttons = Qt.MouseButton.NoButton
            self.qtbot.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, modifier, self.at(x, y))

        def move(self, x: float, y: float, modifier: Modifier = NO_MODIFIER) -> None:
            # Sent directly: QTest drops a move to the position it believes the cursor is at.
            position = QPointF(self.at(x, y))
            event = QMouseEvent(
                QEvent.Type.MouseMove,
                position,
                self.canvas.mapToGlobal(position),
                Qt.MouseButton.NoButton,
                self.buttons,
                modifier,
            )
            QApplication.sendEvent(self.canvas, event)

        def click(self, x: float, y: float, modifier: Modifier = NO_MODIFIER) -> None:
            self.move(x, y)
            self.press(x, y, modifier)
            self.release(x, y, modifier)

        def drag(self, points: Sequence[tuple[float, float]]) -> None:
            (x0, y0), *rest = points
            self.move(x0, y0)
            self.press(x0, y0)
            for x, y in rest:
                self.move(x, y)
            self.release(*points[-1])

        def key(self, key: Qt.Key, modifier: Modifier = NO_MODIFIER) -> None:
            self.qtbot.keyClick(self.canvas, key, modifier)

        def tool(self, name: str) -> None:
            self.window.tool_actions[name].trigger()

    def make_window(qtbot: QtBot, bus: Bus | None = None) -> MainWindow:
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        theme.apply(app)
        session = DocumentSession(bus)
        window = MainWindow(session)
        session.setParent(window)
        qtbot.addWidget(window)
        window.resize(1000, 700)
        window.show()
        qtbot.waitExposed(window)
        # A fixed view: 5 px per mm with the model origin near the bottom left. The minor
        # grid is then 5 mm, so round-number test points land exactly on grid points.
        view = window.canvas.view
        view.scale, view.origin_x, view.origin_y = 5.0, 100.0, float(window.canvas.height() - 100)
        # Never block a test on a modal dialog.
        window.confirm_discard = lambda: True  # type: ignore[method-assign]
        return window

    @pytest.fixture
    def bus(request: pytest.FixtureRequest) -> RecordingBus:
        marker = request.node.get_closest_marker("bus")
        return RecordingBus(**(marker.kwargs if marker else {}))

    @pytest.fixture
    def window(qtbot: QtBot, bus: RecordingBus) -> MainWindow:
        return make_window(qtbot, bus)

    @pytest.fixture
    def driver(qtbot: QtBot, window: MainWindow) -> Driver:
        return Driver(qtbot, window)

    @pytest.fixture
    def new_window(qtbot: QtBot) -> Callable[[], MainWindow]:
        """Open another window on a fresh bus, as if the app had been relaunched."""
        return lambda: make_window(qtbot, RecordingBus())
