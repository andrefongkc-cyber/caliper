"""The assistant's transcript: each request, the Caliper tools it used and what they did, and
its answer, plus each call Claude Desktop makes over MCP. A plain list in the browser, like
History; the proposal card is where changes are reviewed and applied. Above it, while Claude
Desktop can connect, a one-line Timing section: how long its latest task took."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from caliper.ai.agent import Turn
from caliper.ai.model import ToolOutcome
from caliper.app import theme
from caliper.app.agent.timing import FIELDS, Timing, markdown, minutes, rows
from caliper.app.agent.ui import AgentController
from caliper.app.tokens import SPACE


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

    def remote_step(self, client: str, outcome: ToolOutcome) -> None:
        """A call from an MCP client (Claude Desktop), named so it isn't mistaken for yours."""
        self._add(
            f"{client} {step_text(outcome)}", theme.ERROR if outcome.is_error else theme.TEXT_DIM
        )

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


class TimingSection(QFrame):
    """The latest Claude Desktop task's timing (docs/mcp.md, Timing), one line until opened.
    It stays after the run ends, to copy into a test folder's 003-timing.md."""

    start_requested = Signal()
    """Start run was pressed."""
    copied = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("timing")
        self.timing: Timing | None = None
        self.expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.s, SPACE.xs, SPACE.s, SPACE.xs)
        layout.setSpacing(SPACE.xs)
        header = QHBoxLayout()
        header.setSpacing(SPACE.xs)
        self.heading = ""
        """The collapsed line in full; the toggle shows as much as fits."""
        self.toggle = QPushButton()
        self.toggle.setObjectName("timing-toggle")
        self.toggle.setFlat(True)
        # Takes the width Start run leaves, and elides: the browser can be 250 px wide.
        self.toggle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.toggle.clicked.connect(self._toggle)
        self.start_button = QPushButton("Start run")
        self.start_button.setToolTip(
            "Press as you send the task in Claude Desktop, so First response and Total run "
            "count from then (⌘⇧R). Without it a run starts at Claude's first call."
        )
        self.start_button.clicked.connect(self.start_requested)
        header.addWidget(self.toggle, 1)
        header.addWidget(self.start_button)
        layout.addLayout(header)

        self.details = QWidget()
        grid = QGridLayout(self.details)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE.m)
        grid.setVerticalSpacing(SPACE.xxs)
        self.values: dict[str, QLabel] = {}
        for row, name in enumerate(("Date", *FIELDS)):
            label = QLabel(name)
            label.setProperty("role", "dim")
            value = QLabel()
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1, Qt.AlignmentFlag.AlignRight)
            self.values[name] = value
        self.copy_button = QPushButton("Copy for 003-timing.md")
        self.copy_button.setToolTip("Copy these numbers in the test folder's 003-timing.md format")
        self.copy_button.clicked.connect(self.copy)
        grid.addWidget(self.copy_button, len(FIELDS) + 1, 0, 1, 2)
        layout.addWidget(self.details)
        self.show_timing(None)

    def show_timing(self, timing: Timing | None) -> None:
        self.timing = timing
        values = {} if timing is None else dict(rows(timing))
        self.values["Date"].setText("" if timing is None else timing.date.isoformat())
        for name in FIELDS:
            self.values[name].setText(values.get(name, ""))
        self.copy_button.setEnabled(timing is not None)
        self._fit()

    def summary(self) -> str:
        """The collapsed line after "Timing"."""
        timing = self.timing
        if timing is None:
            return "no run yet"
        if not timing.calls:
            return "waiting for Claude…"
        calls = f"{timing.calls} call{'s' if timing.calls != 1 else ''}"
        return f"{minutes(timing.total)} · {calls}"

    def copy(self) -> None:
        if self.timing is not None:
            QGuiApplication.clipboard().setText(markdown(self.timing))
            self.copied.emit()

    def _toggle(self) -> None:
        self.expanded = not self.expanded
        self._fit()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _fit(self) -> None:
        arrow = "▾" if self.expanded else "▸"
        self.heading = f"{arrow} Timing  {self.summary()}"
        self.toggle.setToolTip(self.heading)
        self._elide()
        self.details.setVisible(self.expanded)

    def _elide(self) -> None:
        option = QStyleOptionButton()
        self.toggle.initStyleOption(option)
        space = self.toggle.style().subElementRect(
            QStyle.SubElement.SE_PushButtonContents, option, self.toggle
        )  # where the style draws the text: inside the button's own margins
        metrics = self.toggle.fontMetrics()
        text = metrics.elidedText(self.heading, Qt.TextElideMode.ElideRight, space.width())
        self.toggle.setText(text)
