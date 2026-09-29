"""The app's end of the MCP bridge: Claude Desktop's calls, reviewed like the assistant's.

`caliper-mcp` (`caliper.ai.mcp_server`) forwards each MCP tool call here over a local socket
(`caliper.ai.bridge`) that only the user's own processes can open. Each call runs on the UI
thread, one at a time, in the `Draft` against the session's document. When the draft changes
it goes on the proposal card like the in-app assistant's changes, and the user accepts it (one
undo step, credited to the agent) or rejects it there; the client can't. Closing, accepting,
editing, or opening another document ends the draft, and the client's next call says so.
Each call, and the user's Accept, is timed for the Timing panel (`caliper.app.agent.timing`),
and report_progress, Claude's estimate of the calls left, is answered here for its time left.
"""

from collections.abc import Mapping
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from caliper.ai.bridge import (
    MAX_MESSAGE,
    Response,
    decode_request,
    encode_response,
    in_use,
    prepare_directory,
    supported,
)
from caliper.ai.draft import Draft, Ended
from caliper.ai.mcp_server import PROGRESS
from caliper.ai.model import ToolCall, ToolOutcome
from caliper.app.agent.proposal import Plan, Proposal
from caliper.app.agent.timing import RunTimer
from caliper.app.agent.ui import AgentController
from caliper.app.session import DocumentSession
from caliper.engine.io.codec import COMMAND_KINDS

BUSY = (
    "Caliper's own assistant is working on a request right now, so Caliper can't take changes "
    "from you until it's done. Try again in a moment."
)


class McpHost(QObject):
    stepped = Signal(str, object)
    """A client's name and a `ToolOutcome`: one call it made, for the assistant log."""
    timed = Signal(object)
    """The current run's `Timing`: after each call, Accept, and Start run."""

    def __init__(
        self,
        session: DocumentSession,
        controller: AgentController,
        path: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.controller = controller
        self.path = path
        self.draft = Draft()
        self.timer = RunTimer()
        self._client = "MCP client"
        self._tool: str | None = None
        """The tool of the call being answered, for its timing."""
        self._shown: Proposal | None = None
        """The draft's proposal, while it's the one on the card."""
        self._accepted: Proposal | None = None
        self._updating = False
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._connected)
        controller.applied.connect(self._applied)
        controller.proposal_changed.connect(self._proposal_changed)
        session.document_replaced.connect(partial(self.draft.end, Ended.OPENED))
        session.document_replaced.connect(lambda: self.timer.end())

    def start(self) -> str | None:
        """Listen for `caliper-mcp`. None when listening, else why not, for the status bar."""
        if not supported():
            return "Claude Desktop can't connect: MCP needs macOS or Linux."
        try:
            prepare_directory(self.path)
        except OSError as e:
            return f"Claude Desktop can't connect: {e}"
        # Check first: listening with UserAccessOption renames its socket into place, which
        # would quietly take the path from a Caliper window already serving it.
        if in_use(self.path):
            return "Claude Desktop is connected to another Caliper window."
        QLocalServer.removeServer(str(self.path))  # left behind by a Caliper that crashed
        if self._server.listen(str(self.path)):
            return None
        return f"Claude Desktop can't connect: {self._server.errorString()}"

    @property
    def listening(self) -> bool:
        return self._server.isListening()

    def close(self) -> None:
        self._server.close()

    def start_run(self) -> None:
        """Start run: time a new task from now (press it as you send the prompt)."""
        self.timer.start()
        self.timed.emit(self.timer.timing)

    # --- Requests -----------------------------------------------------------------------

    def handle(self, line: bytes) -> bytes:
        """One request line in, one response line out, timed."""
        arrived = self.timer.arrived()
        changed, self._tool = False, None
        try:
            response, changed = self._respond(line)
        finally:
            self.timer.finished(arrived, changed=changed, tool=self._tool)
        self.timed.emit(self.timer.timing)
        return response

    def _respond(self, line: bytes) -> tuple[bytes, bool]:
        """The response, and whether the draft changed."""
        try:
            request = decode_request(line)
        except ValueError as e:
            return encode_response(Response({"error": str(e)}, is_error=True)), False
        self._tool = request.tool
        if request.tool == PROGRESS.name:
            self._client = request.client
            outcome = self._progress(request.arguments)
            self.stepped.emit(request.client, outcome)
            return encode_response(Response(outcome.content, outcome.is_error)), False
        if request.tool in COMMAND_KINDS and self.controller.busy:
            return encode_response(Response({"error": BUSY}, is_error=True)), False
        self._client = request.client
        try:
            answer = self.draft.call(
                self.session.document, self.session.selection, request.tool, request.arguments
            )
        except Exception as e:  # a bug in Caliper: say so rather than leave the client waiting
            error = Response({"error": f"Caliper failed: {e}"}, is_error=True)
            return encode_response(error), False
        self.stepped.emit(request.client, answer.outcome)
        if answer.changed:
            self._show()
        for expectation in answer.checked:  # nothing pending to carry it: straight to Checks
            if expectation not in self.session.checks:
                self.session.add_check(expectation)
        outcome = answer.outcome
        response = Response(outcome.content, outcome.is_error, answer.note)
        return encode_response(response), answer.changed

    def _progress(self, arguments: Mapping[str, object]) -> ToolOutcome:
        """report_progress: Claude's estimate, for the time left. It never touches the draft,
        so a note about the draft waits for the next call that does."""
        call = ToolCall(id="mcp", name=PROGRESS.name, arguments=arguments)
        counts: dict[str, int | None] = {}
        for name in ("calls_left", "repeats", "checks"):
            value = arguments.get(name)
            if value is None and name != "calls_left":
                counts[name] = None
            elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
                error = {"error": f"{name} must be a whole number, 0 or more"}
                return ToolOutcome(call, error, is_error=True)
            else:
                counts[name] = value
        left = counts["calls_left"]
        assert left is not None
        self.timer.progress(left, repeats=counts["repeats"], checks=counts["checks"])
        return ToolOutcome(call, {"ok": True, "done": left == 0})

    def _connected(self) -> None:
        while (socket := self._server.nextPendingConnection()) is not None:
            buffer = bytearray()
            socket.readyRead.connect(partial(self._read, socket, buffer))
            socket.disconnected.connect(socket.deleteLater)

    def _read(self, socket: QLocalSocket, buffer: bytearray) -> None:
        buffer += socket.readAll().data()
        while b"\n" in buffer:
            line, _, rest = bytes(buffer).partition(b"\n")
            buffer[:] = rest
            socket.write(self.handle(line))
        if len(buffer) > MAX_MESSAGE:
            socket.write(encode_response(Response({"error": "request too long"}, is_error=True)))
            buffer.clear()
            socket.disconnectFromServer()
        socket.flush()

    # --- The draft on the card ----------------------------------------------------------

    def _show(self) -> None:
        draft = self.draft
        self._updating = True
        try:
            if draft.workspace is None:  # the client undid all of it
                if self._shown is not None and self.controller.proposal is self._shown:
                    self.controller.reject()
                self._shown = None
                return
            plan = Plan(
                draft.label,
                f"Changes from {self._client} over MCP. Accept to apply them as one step.",
                draft.commands,
                draft.checks,
                draft.executed,
            )
            assert draft.base is not None
            self._shown = self.controller.propose(plan, draft.base, result=draft.workspace.document)
        finally:
            self._updating = False

    def _applied(self, proposal: Proposal, seconds: float) -> None:
        self._accepted = proposal
        if proposal is self._shown:
            self.timer.accepted(seconds)

    def _proposal_changed(self) -> None:
        shown = self._shown
        if self._updating or shown is None or self.controller.proposal is shown:
            return
        self._shown = None
        if self._accepted is shown:
            self.draft.end(Ended.ACCEPTED)
        elif self.session.document is not shown.base:
            self.draft.end(Ended.CHANGED)
        else:
            self.draft.end(Ended.CLOSED)
        self._accepted = None
        self.timer.proposal_ended()
        self.timed.emit(self.timer.timing)
