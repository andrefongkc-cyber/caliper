"""The assistant's transcript: each request, the Caliper tools it used and what they did, and
its answer. A plain list in the browser, like History; the proposal card is where changes are
reviewed and applied."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QWidget

from caliper.ai.agent import Turn
from caliper.ai.model import ToolOutcome
from caliper.app import theme
from caliper.app.agent.ui import AgentController


def step_text(outcome: ToolOutcome) -> str:
    """One line for one tool call: what was asked and what came of it."""
    name, content = outcome.call.name, outcome.content
    if outcome.is_error:
        return f"✗ {name}: {_problem(content)}"
    if isinstance(content, dict):
        if "label" in content:  # a command that changed something
            created = content.get("created") or []
            ids = (
                f" ({', '.join(str(i) for i in created)})"
                if isinstance(created, list) and created
                else ""
            )
            return f"→ {name}: {content['label']}{ids}"
        if "passed" in content:
            mark = "✓" if content["passed"] else "✗"
            actual = "" if content.get("actual") is None else f" {content['actual']:g}"
            return f"{mark} {name}:{actual}"
        if "undone" in content:
            return f"→ {name}: took back {content['undone']}"
        if "state" in content:
            return f"→ {name}: {content['state']}, {content['dof']} degrees of freedom left"
        if "changed" in content:
            return f"→ {name}: nothing changed"
    return f"→ {name}"


def _problem(content: object) -> str:
    if isinstance(content, dict):
        rejected = content.get("rejected")
        if isinstance(rejected, list) and rejected and isinstance(rejected[0], dict):
            return str(rejected[0].get("message"))
        if "error" in content:
            error = content["error"]
            return str(error.get("message") if isinstance(error, dict) else error)
    return str(content)


class AssistantLog(QListWidget):
    def __init__(self, controller: AgentController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("assistant-log")
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.controller = controller
        controller.turn_started.connect(self._asked)
        controller.step_done.connect(self._stepped)
        controller.turn_finished.connect(self._finished)

    def lines(self) -> list[str]:
        return [self.item(i).text() for i in range(self.count())]

    def _add(self, text: str, colour: QColor) -> None:
        item = QListWidgetItem(text)
        item.setForeground(colour)
        self.addItem(item)
        self.scrollToBottom()

    def _asked(self, text: str) -> None:
        self._add(f"You: {text}", theme.TEXT)

    def _stepped(self, outcome: ToolOutcome) -> None:
        self._add(step_text(outcome), theme.ERROR if outcome.is_error else theme.TEXT_DIM)

    def _finished(self, result: Turn | Exception) -> None:
        if isinstance(result, Exception):
            self._add(f"The assistant failed: {result}", theme.ERROR)
            return
        name = self.controller.assistant.model.name if self.controller.assistant else "Assistant"
        if result.reply:
            self._add(f"{name}: {result.reply}", theme.AGENT)
        if result.error is not None:
            self._add(result.error, theme.ERROR)
        if result.commands:
            count = len(result.commands)
            changes = f"{count} change{'s' if count != 1 else ''}"
            self._add(f"Proposed {changes}: accept or reject on the canvas.", theme.TEXT_DIM)
