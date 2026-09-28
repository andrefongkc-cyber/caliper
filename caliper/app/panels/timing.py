"""The Timing panel, above Properties: how long the latest Claude Desktop task took.

One line until opened (`▸ 4m 07s · 57 calls`), then the six fields of docs/mcp.md, Timing,
with Start run and a copy in the test folders' 003-timing.md format. While a run is live its
time ticks every second, with the time left once Claude has said how many calls it expects
(`▸ 1m 12s · ~3m 40s left · 35 calls`); it settles on the recorded Total run when the run
stops (`caliper.app.agent.timing`). Shown only while Claude Desktop can connect.
"""

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from caliper.app.agent.timing import FIELDS, TIMING_FILE, Timing, markdown, minutes, rows
from caliper.app.tokens import SPACE

TICK_MS = 1000


class TimingPanel(QFrame):
    start_requested = Signal()
    """Start run was pressed."""
    copied = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("timing")
        # As tall as it shows: one line, or the fields when opened.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.timing: Timing | None = None
        self.source: Callable[[], Timing | None] | None = None
        """Where the ticking reads the live run from (the MCP host's timer)."""
        self.expanded = False
        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._refresh)
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
        # Takes the width Start run leaves, and elides: the dock can be 260 px wide.
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
        self.copy_button = QPushButton(f"Copy for {TIMING_FILE}")
        self.copy_button.setToolTip(
            f"Copy these numbers in the test folder's {TIMING_FILE} format. Saving the drawing "
            "into its test folder writes the file for you."
        )
        self.copy_button.clicked.connect(self.copy)
        grid.addWidget(self.copy_button, len(FIELDS) + 1, 0, 1, 2)
        layout.addWidget(self.details)
        self.show_timing(None)

    @property
    def ticking(self) -> bool:
        return self._tick.isActive()

    def show_timing(self, timing: Timing | None) -> None:
        self.timing = timing
        values = {} if timing is None else dict(rows(timing))
        if timing is not None and timing.live:
            values["Total run"] = minutes(timing.elapsed)
        self.values["Date"].setText("" if timing is None else timing.date.isoformat())
        for name in FIELDS:
            self.values[name].setText(values.get(name, ""))
        self.copy_button.setEnabled(timing is not None)
        if timing is not None and timing.live and self.source is not None:
            if not self._tick.isActive():
                self._tick.start()
        else:
            self._tick.stop()
        self._fit()

    def summary(self) -> str:
        """The collapsed line after the arrow."""
        timing = self.timing
        if timing is None:
            return "no run yet"
        shown = minutes(timing.elapsed if timing.live else timing.total)
        if not timing.calls:
            return f"{shown} · waiting for Claude…" if timing.live else "waiting for Claude…"
        calls = f"{timing.calls} call{'s' if timing.calls != 1 else ''}"
        if timing.left is not None:
            left = f"~{minutes(timing.left)} left" if timing.left >= 1 else "almost done"
            return f"{shown} · {left} · {calls}"  # before the calls, which elide first
        return f"{shown} · {calls}" + (" · running" if timing.live else "")

    def copy(self) -> None:
        if self.timing is not None:
            QGuiApplication.clipboard().setText(markdown(self.timing))
            self.copied.emit()

    def _refresh(self) -> None:
        if self.source is not None:
            self.show_timing(self.source())

    def _toggle(self) -> None:
        self.expanded = not self.expanded
        self._fit()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _fit(self) -> None:
        arrow = "▾" if self.expanded else "▸"
        self.heading = f"{arrow} {self.summary()}"
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
