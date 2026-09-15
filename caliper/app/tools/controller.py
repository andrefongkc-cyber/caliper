"""The tool-mode state machine.

Modes are the fixed V1 tool list. Every transition goes through `activate` or `escape`:

- `activate(name)` cancels the operation in progress, then switches mode.
- `escape()` cancels the operation in progress if there is one; otherwise it returns to
  Select. So Esc during a drag cancels the drag, and a second Esc leaves the tool.

Within a mode, each tool is its own small state machine with an explicit `phase`.
"""

from PySide6.QtCore import QObject, Signal

from caliper.app.session import DocumentSession
from caliper.app.tools.base import Tool
from caliper.app.tools.dimension import DimensionTool
from caliper.app.tools.measure import MeasureTool
from caliper.app.tools.select import SelectTool
from caliper.app.tools.shapes import ArcTool, CircleTool, LineTool, RectangleTool

SELECT = "Select"


class ToolController(QObject):
    changed = Signal()
    """The active tool or its state changed: repaint and refresh the hint."""

    def __init__(self, session: DocumentSession, parent: QObject | None = None) -> None:
        super().__init__(parent)
        tools: list[Tool] = [
            SelectTool(session),
            LineTool(session),
            CircleTool(session),
            ArcTool(session),
            RectangleTool(session),
            DimensionTool(session),
            MeasureTool(session),
        ]
        self.tools: dict[str, Tool] = {tool.name: tool for tool in tools}
        self._active = self.tools[SELECT]

    @property
    def active(self) -> Tool:
        return self._active

    def activate(self, name: str) -> None:
        self._active.cancel()
        self._active = self.tools[name]
        self.changed.emit()

    def escape(self) -> None:
        if self._active.busy:
            self._active.cancel()
            self.changed.emit()
        elif self._active.name != SELECT:
            self.activate(SELECT)

    def cancel_operation(self) -> None:
        """Abandon any operation in progress without leaving the tool, e.g. before undo."""
        self._active.cancel()
        self.changed.emit()
