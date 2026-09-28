"""The agent's surfaces: a prompt bar under the canvas and a proposal card over it.

Ask in the bar (⌘L). The card shows the plan, each check before and after, and any of your
checks it would break. The canvas draws the result as ghost geometry. Accept (⌘Return)
applies it as one undo step credited to the agent; Reject (Esc) discards it.

With an assistant (`caliper.ai`, e.g. CALIPER_ASSISTANT=claude) the request goes to a model
that works through Caliper's commands on a copy of the document, off the UI thread; what it
did comes back as the same kind of proposal. Without one, the scripted stand-in answers.
"""

import dataclasses
import threading
from collections.abc import Mapping
from contextlib import nullcontext
from time import perf_counter

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from caliper.ai.agent import Assistant, Turn
from caliper.app import theme
from caliper.app.agent.proposal import CheckChange, Plan, Proposal, prepare
from caliper.app.agent.scripted import understand
from caliper.app.panels.checks import describe
from caliper.app.panels.describe import n
from caliper.app.session import Author, DocumentSession
from caliper.app.tokens import SPACE
from caliper.contracts.document import Document, Point2
from caliper.contracts.queries import CheckResult
from caliper.engine.commands.handlers import already

CARD_WIDTH = 360
DETAIL_LINES = 6
"""Proposals with more changes than this collapse their list of changes behind a summary."""
DETAILS_HEIGHT = 180
"""Pixels the list of changes may take when shown, before it scrolls."""
SCRIPTED_PLACEHOLDER = "Ask for a change, like “4 holes diameter 6 inset 10”  (⌘L)"
ASSISTANT_PLACEHOLDER = (
    "Ask for a change, like “a 100 by 50 rectangle 20 mm right of the origin”  (⌘L)"
)


class PromptBar(QFrame):
    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("prompt-bar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE.m, SPACE.s, SPACE.m, SPACE.s)
        layout.setSpacing(SPACE.m)
        self.chip = QLabel("Scripted agent")
        self.chip.setObjectName("agent-chip")
        self.chip.setToolTip(
            "A stand-in that understands a few fixed phrasings. The AI layer arrives in V3; "
            "it will use this same review flow."
        )
        self.input = QLineEdit()
        self.input.setObjectName("prompt")
        self.input.setPlaceholderText(SCRIPTED_PLACEHOLDER)
        self.input.returnPressed.connect(self._submit)
        self.input.installEventFilter(self)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip(
            "Stop the assistant after its current step. What it did so far is dropped; "
            "your sketch is as it was."
        )
        self.stop_button.clicked.connect(self.stopped)
        self.stop_button.hide()
        layout.addWidget(self.chip)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.stop_button)

    escaped = Signal()
    stopped = Signal()
    """Stop was pressed while the assistant worked."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self.input
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self.escaped.emit()
            return True
        return False

    def _submit(self) -> None:
        text = self.input.text().strip()
        if text:
            self.submitted.emit(text)

    def set_model(self, name: str | None) -> None:
        """Say who answers: a model by name, or the scripted stand-in."""
        if name is None:
            self.chip.setText("Scripted agent")
            self.input.setPlaceholderText(SCRIPTED_PLACEHOLDER)
        else:
            self.chip.setText(name)
            self.chip.setToolTip(
                f"{name} works through Caliper's commands on a copy of your sketch. "
                "Nothing changes until you accept."
            )
            self.input.setPlaceholderText(ASSISTANT_PLACEHOLDER)

    def set_busy(self, busy: bool) -> None:
        self.input.setEnabled(not busy)
        self.stop_button.setVisible(busy)
        self.stop_button.setEnabled(True)
        if busy:
            self.input.setPlaceholderText("Working…")
        else:
            self.input.setPlaceholderText(
                SCRIPTED_PLACEHOLDER
                if self.chip.text() == "Scripted agent"
                else ASSISTANT_PLACEHOLDER
            )


class ProposalCard(QFrame):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("proposal-card")
        self.setFixedWidth(CARD_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.l, SPACE.l, SPACE.l, SPACE.l)
        layout.setSpacing(SPACE.s)
        self.eyebrow = QLabel("AGENT PROPOSAL")
        self.eyebrow.setObjectName("proposal-eyebrow")
        self.title = QLabel()
        self.title.setFont(theme.font(size=15, bold=True))
        self.explanation = QLabel()
        self.explanation.setWordWrap(True)
        self.summary = QLabel()
        self.summary.setObjectName("proposal-summary")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.details_button = QPushButton()
        self.details_button.setObjectName("proposal-details-toggle")
        self.details_button.setFlat(True)
        self.details_button.clicked.connect(self._toggle_details)
        self.expanded = False
        """Whether a large proposal's list of changes is shown. Kept while the card is open."""
        self.count = 0
        self._listing: tuple[object, ...] = ()
        """The commands the list of changes should show."""
        self._listed: tuple[object, ...] | None = None
        """The commands `commands` shows now: the list is written only while it's visible."""
        self.commands = QLabel()
        self.commands.setObjectName("proposal-commands")
        self.commands.setWordWrap(True)
        self.commands.setFont(theme.font(size=11, mono=True))
        # Wrap to the scroll area's width, which narrows when its scroll bar appears.
        self.commands.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.details = QScrollArea()
        self.details.setObjectName("proposal-details")
        self.details.setFrameShape(QFrame.Shape.NoFrame)
        self.details.setWidgetResizable(True)
        self.details.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.details.setWidget(self.commands)
        self.details.viewport().setAutoFillBackground(False)
        self.checks = QLabel()
        self.checks.setObjectName("proposal-checks")
        self.checks.setWordWrap(True)
        self.checks.setTextFormat(Qt.TextFormat.RichText)
        self.warning = QLabel()
        self.warning.setProperty("role", "error")
        self.warning.setWordWrap(True)
        buttons = QHBoxLayout()
        self.reject_button = QPushButton("Reject")
        self.reject_button.setToolTip("Esc")
        self.reject_button.clicked.connect(self.rejected)
        self.accept_button = QPushButton("Accept")
        self.accept_button.setObjectName("accept")
        self.accept_button.setToolTip("⌘Return")
        self.accept_button.setDefault(True)
        self.accept_button.clicked.connect(self.accepted)
        buttons.addStretch(1)
        buttons.addWidget(self.reject_button)
        buttons.addWidget(self.accept_button)
        for widget in (
            self.eyebrow,
            self.title,
            self.explanation,
            self.summary,
            self.details_button,
            self.details,
            self.checks,
            self.warning,
        ):
            layout.addWidget(widget)
        layout.addLayout(buttons)
        self.hide()

    @property
    def large(self) -> bool:
        """Too many changes to list in full: they collapse behind the summary."""
        return self.count > DETAIL_LINES

    def show_proposal(self, proposal: Proposal) -> None:
        plan = proposal.plan
        if not self.isVisible():
            self.expanded = False  # a new proposal starts collapsed; an update keeps it
        self.title.setText(plan.label)
        self.explanation.setText(plan.explanation)
        self._listing = plan.commands
        self.count = len(plan.commands)
        self.summary.setText(summary(proposal))
        rows = check_rows(proposal.checks)
        self.checks.setText("<br>".join(rows))
        self.checks.setVisible(bool(rows))
        problems = [e.message for e in proposal.errors]
        problems += [
            f"Breaks your check: {describe(c.expectation)}" for c in proposal.broken_checks
        ]
        self.warning.setText("\n".join(problems))
        self.warning.setVisible(bool(problems))
        self.accept_button.setEnabled(not proposal.errors)
        self.accept_button.setText("Accept anyway" if proposal.broken_checks else "Accept")
        self._fit()
        self.show()
        self.raise_()

    def _toggle_details(self) -> None:
        self.expanded = not self.expanded
        self._fit()

    def _fit(self) -> None:
        """Show the changes in full when there are few, else behind the summary and toggle, and
        size the card to what it shows: it never grows past the list's own bounded height."""
        large, count = self.large, self.count
        self.summary.setVisible(large)
        self.details_button.setVisible(large)
        self.details_button.setText(
            "Hide changes ▴" if self.expanded else f"Show {count} changes ▾"
        )
        showing = not large or self.expanded
        self.details.setVisible(showing)
        if showing:
            # Written and measured only when shown: a collapsed 250-change proposal would
            # otherwise format and lay out all 250 lines on every change the agent makes.
            if self._listed is not self._listing:
                self.commands.setText("\n".join(_command_text(c) for c in self._listing))
                self._listed = self._listing
            inner = CARD_WIDTH - 2 * SPACE.l
            self.details.setFixedHeight(min(self.commands.heightForWidth(inner), DETAILS_HEIGHT))
        layout = self.layout()
        assert layout is not None
        layout.activate()
        self.resize(CARD_WIDTH, layout.totalHeightForWidth(CARD_WIDTH))  # wrapped text sets height
        self.reposition()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.move(parent.width() - self.width() - SPACE.l, SPACE.l)


class AgentController(QObject):
    """Connects the bar, the card, the canvas preview, and the session."""

    proposal_changed = Signal()
    proposal_shown = Signal(object)
    """A new Proposal is on screen; the window frames what it touches."""
    turn_started = Signal(str)
    """The assistant was asked this."""
    step_done = Signal(object)
    """A `ToolOutcome`: one tool call the assistant made, and its result."""
    turn_finished = Signal(object)
    """The assistant's `Turn`, or the exception that ended it."""
    applied = Signal(object, float)
    """The user accepted this `Proposal` and its commands ran, as one undo step, in this many
    seconds from the press."""
    _answered = Signal(object, int)

    def __init__(
        self,
        session: DocumentSession,
        bar: PromptBar,
        card: ProposalCard,
        parent: QObject | None = None,
        *,
        assistant: Assistant | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.bar = bar
        self.card = card
        self.proposal: Proposal | None = None
        self.assistant: Assistant | None = None
        self.busy = False
        self._generation = 0
        """Counts documents opened and turns stopped, so an answer to either is dropped."""
        self._stop: threading.Event | None = None
        """Set to stop the turn the assistant is working on (`stop`)."""
        bar.submitted.connect(self.ask)
        card.accepted.connect(self.accept)
        card.rejected.connect(self.reject)
        session.document_replaced.connect(self.reject)
        session.document_replaced.connect(self._forget)
        bar.escaped.connect(self.reject)
        bar.stopped.connect(self.stop)
        self._answered.connect(self._show_turn)  # queued: it arrives from the worker thread
        self.set_assistant(assistant)

    def set_assistant(self, assistant: Assistant | None) -> None:
        self.assistant = assistant
        self.bar.set_model(None if assistant is None else assistant.model.name)

    def ask(self, text: str) -> None:
        if self.assistant is not None:
            self._ask_assistant(self.assistant, text)
            return
        document = self.session.document
        understood = understand(text, document, self.session.selection)
        if understood.plan is None:
            self.reject()
            self.session.message.emit(understood.message)
            return
        self.propose(understood.plan, document)

    def propose(self, plan: Plan, base: Document, *, result: Document | None = None) -> Proposal:
        """Show `plan`, prepared against `base`, for review, in place of any other proposal.
        `result` is the document a workspace already built from it (see `prepare`)."""
        self.proposal = prepare(plan, base, self.session.checks, result=result)
        self.card.show_proposal(self.proposal)
        self.proposal_changed.emit()
        self.proposal_shown.emit(self.proposal)
        return self.proposal

    def accept(self) -> None:
        pressed = perf_counter()
        proposal = self.proposal
        if proposal is None or proposal.errors:
            return
        if self.session.document is not proposal.base:
            self.reject()
            self.session.message.emit("The sketch changed since that proposal. Ask again.")
            return
        # Every command still goes through the bus, into one undo step, but as it already ran
        # from this very document in the workspace: its outcome is committed, not solved again.
        executed = proposal.plan.executed
        with (
            already(executed) if executed is not None else nullcontext(),
            self.session.transaction(proposal.plan.label, author=Author.AGENT),
        ):
            for command in proposal.plan.commands:
                self.session.execute(command, author=Author.AGENT)
        for expectation in proposal.plan.checks:
            if expectation not in self.session.checks:
                self.session.add_check(expectation)
        self.bar.input.clear()
        self.applied.emit(proposal, perf_counter() - pressed)
        self._close()
        self.session.message.emit(f"Applied {proposal.plan.label}")

    def reject(self) -> None:
        if self.proposal is not None:
            self._close()

    def stop(self) -> None:
        """Stop the assistant's turn after its current step and drop it. It worked on a copy,
        so the sketch is untouched; the request it was on is forgotten."""
        if not self.busy or self._stop is None or self._stop.is_set():
            return
        self._stop.set()
        self._generation += 1  # its answer, when the current step ends, is dropped
        self.bar.stop_button.setEnabled(False)
        self.session.message.emit("Stopping the assistant after its current step…")

    # --- The assistant ------------------------------------------------------------------

    def _ask_assistant(self, assistant: Assistant, text: str) -> None:
        if self.busy:
            self.session.message.emit("Still working on the last request.")
            return
        self.reject()
        self.busy = True
        self.bar.input.clear()
        self.bar.set_busy(True)
        self.turn_started.emit(text)
        document, selection, generation = (
            self.session.document,
            self.session.selection,
            self._generation,
        )
        stop = self._stop = threading.Event()

        def work() -> None:
            result: Turn | Exception
            try:
                result = assistant.ask(
                    text, document, selection, on_step=self.step_done.emit, stop=stop.is_set
                )
            except Exception as e:  # a bug in Caliper: show it rather than lose it
                result = e
            self._answered.emit(result, generation)

        threading.Thread(target=work, name="caliper-assistant", daemon=True).start()

    def _show_turn(self, result: Turn | Exception, generation: int) -> None:
        self.busy = False
        self.bar.set_busy(False)
        if generation != self._generation:
            # Stopped, or another document was opened while the model worked.
            if self._stop is not None and self._stop.is_set():
                self.session.message.emit("Stopped the assistant. Your sketch is as it was.")
            return
        self.turn_finished.emit(result)
        if isinstance(result, Exception):
            self.session.message.emit(f"The assistant failed: {result}")
            return
        if result.commands:
            plan = Plan(
                result.label,
                result.reply or "The assistant's changes.",
                result.commands,
                result.checks,
                result.executed,
            )
            self.propose(plan, result.base, result=result.result)
        else:
            for expectation in result.checks:  # no change to carry them: straight to Checks
                if expectation not in self.session.checks:
                    self.session.add_check(expectation)
            if result.error is not None:
                self.session.message.emit(result.error)

    def _forget(self) -> None:
        self._generation += 1
        if self.assistant is not None:
            self.assistant.reset()

    def _close(self) -> None:
        self.proposal = None
        self.card.hide()
        self.proposal_changed.emit()


MAX_CHECK_ROWS = 4


def _command_text(command: object) -> str:
    fields = ", ".join(
        f"{field.name}={_value(getattr(command, field.name))}"
        for field in dataclasses.fields(command)  # type: ignore[arg-type]
        if getattr(command, field.name) is not None
    )
    return f"{type(command).__name__}({fields})"


def _value(value: object) -> str:
    if isinstance(value, float):
        return n(value)
    if isinstance(value, Point2):
        return f"({n(value.x)}, {n(value.y)})"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{k}: {_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, tuple):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    return str(value)


def _mark(result: CheckResult) -> str:
    colour = theme.PASSED.name() if result.passed else theme.ERROR.name()
    shown = "" if result.actual is None else f" {n(result.actual)}"
    return f'<span style="color:{colour}">{"✓" if result.passed else "✗"}{shown}</span>'


def summary(proposal: Proposal) -> str:
    """How much a proposal does and how its checks stand, in one line: failures in red."""
    count, checks = len(proposal.plan.commands), proposal.checks
    changes = f"{count} change{'s' if count != 1 else ''}"
    if not checks:
        return f"{changes} · no checks"
    failing = sum(not c.after.passed for c in checks)
    stated = f"{len(checks)} check{'s' if len(checks) != 1 else ''}"
    if not failing:
        return f"{changes} · {stated}, all passing"
    return (
        f"{changes} · {stated}, <span style='color:{theme.ERROR.name()}'>{failing} failing</span>"
    )


def check_rows(changes: tuple[CheckChange, ...]) -> list[str]:
    """User checks first (they matter most), then failing agent checks, then the rest."""

    def rank(change: CheckChange) -> int:
        if not change.agent:
            return 0
        return 1 if not change.after.passed else 2

    rows: list[str] = []
    ordered = sorted(changes, key=rank)
    for change in ordered[:MAX_CHECK_ROWS]:
        label = describe(change.expectation).split(" = ")[0]
        dim = theme.TEXT_DIM.name()
        if change.agent:
            detail = _mark(change.after)
        else:
            yours = f"<span style='color:{dim}'>yours</span>"
            detail = f"{_mark(change.before)} → {_mark(change.after)} {yours}"
        rows.append(f"{detail}&nbsp; {label}")
    hidden = ordered[MAX_CHECK_ROWS:]
    if hidden:
        passing = sum(c.after.passed for c in hidden)
        colour = theme.PASSED.name() if passing == len(hidden) else theme.ERROR.name()
        rows.append(f"<span style='color:{colour}'>+ {len(hidden)} more, {passing} pass</span>")
    return rows
