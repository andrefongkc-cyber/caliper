"""pytest-qt fixtures for the shell. Runs offscreen: QT_QPA_PLATFORM=offscreen.

The Linux core job installs no Qt, so every Qt test module is skipped there; the macOS app
job runs them.
"""

import importlib.util
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

HAVE_QT = all(importlib.util.find_spec(name) for name in ("PySide6", "pytestqt"))
QT_FREE = {"test_viewport_math.py", "test_tokens.py"}


def pytest_configure(config: pytest.Config) -> None:
    del config


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
        Command,
        CommandResult,
    )
    from caliper.contracts.document import (
        Point2,
    )
    from caliper.engine.commands.bus import Bus

    class RecordingBus(Bus):
        """The real bus, plus a record of every command sent."""

        def __init__(self) -> None:
            super().__init__()
            self.sent: list[Command] = []

        def execute(self, command: Command, *, merge_key: str | None = None) -> CommandResult:
            self.sent.append(command)
            return super().execute(command, merge_key=merge_key)

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
    def bus() -> RecordingBus:
        return RecordingBus()

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
