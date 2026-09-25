"""Claude Desktop in the window: MCP calls arrive over the real socket and become proposals.

Everything is real except the far end: requests come from `caliper.ai.bridge.ask` (what
`caliper-mcp` sends) or the MCP SDK's client, on a worker thread, while the window answers on
the UI thread. No model and no API key are involved: over MCP, the client is the model.
"""

import shutil
import socket
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from caliper.ai.agent import Assistant
from caliper.ai.bridge import BridgeError, Request, Response, ask
from caliper.ai.draft import Ended
from caliper.ai.mcp_server import build
from caliper.ai.model import Reply, Stop, ToolCall
from caliper.app.agent.mcp_host import BUSY
from caliper.app.session import Author
from caliper.contracts.commands import CreateCircle
from caliper.contracts.document import EntityId, Point2, Rectangle
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot

E1 = EntityId("e1")
RECTANGLE = {"corner": {"x": 0, "y": 0}, "width": 100, "height": 50}
CIRCLE = {"center": {"x": 10, "y": 10}, "radius": 5}
WIDTH_CHECK = {"metric": "bbox_width", "expected": 100, "tolerance": 0.001, "ids": ["e1"]}


@pytest.fixture
def socket_file() -> Iterator[Path]:
    directory = Path(tempfile.mkdtemp(prefix="cal", dir="/tmp"))
    yield directory / "mcp.sock"
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def served(window, socket_file: Path):
    assert window.serve_mcp(socket_file)
    yield window
    window.mcp.close()


def in_background(qtbot, work: Callable[[], Any]) -> Any:
    """Run `work` on a thread while the window keeps answering, and return what it returned."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = work()
        except BaseException as e:
            box["error"] = e

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=15000)
    if "error" in box:
        raise box["error"]
    return box["value"]


def call(window, qtbot, tool: str, arguments: dict[str, object] | None = None) -> Response:
    request = Request(client="claude-ai", tool=tool, arguments=arguments or {})
    return in_background(qtbot, partial(ask, request, window.mcp.path, timeout=10))


# --- Proposals, accept, undo, redo ------------------------------------------------------


def test_a_change_from_claude_desktop_is_proposed_and_applies_as_one_undo_step(
    served, qtbot
) -> None:
    window, session = served, served.session
    response = call(window, qtbot, "create_rectangle", RECTANGLE)
    assert not response.is_error
    assert response.note is None
    # Reviewed first: on the card, the document untouched.
    assert window.proposal_card.isVisible()
    assert window.proposal_card.title.text() == "Create Rectangle"
    assert dict(session.document.entities) == {}
    assert window.assistant_log.lines() == ["claude-ai → create_rectangle: Create Rectangle (e1)"]
    call(window, qtbot, "run_check", WIDTH_CHECK)
    assert window.agent.proposal.plan.checks[0].expected == 100.0
    base = session.document

    window.proposal_card.accept_button.click()
    assert session.document.entities[E1] == Rectangle(
        corner=Point2(x=0.0, y=0.0), width=100.0, height=50.0
    )
    assert session.history[-1].author is Author.AGENT
    assert len(session.checks) == 1
    accepted = session.document
    # Replaying what was accepted writes the same file.
    replay = Bus(base)
    for command in session.history[-1].commands:
        replay.execute(command)
    assert snapshot.dumps(replay.document) == snapshot.dumps(accepted)

    assert window.undo_action.text() == "Undo Create Rectangle"
    window.undo_action.trigger()
    assert dict(session.document.entities) == {}
    window.redo_action.trigger()
    assert session.document == accepted
    # The client hears it was accepted, once.
    assert call(window, qtbot, "inspect_document").note == Ended.ACCEPTED.value
    assert call(window, qtbot, "inspect_document").note is None


def test_several_calls_make_one_proposal_and_one_undo_step(served, qtbot) -> None:
    window, session = served, served.session
    call(window, qtbot, "create_rectangle", RECTANGLE)
    call(window, qtbot, "create_circle", CIRCLE)
    assert window.proposal_card.title.text() == "Assistant Changes"
    assert len(window.agent.proposal.plan.commands) == 2
    before = len(session.history)
    window.proposal_card.accept_button.click()
    assert len(session.history) == before + 1
    assert len(session.document.entities) == 2


def test_rejecting_leaves_the_sketch_alone_and_the_client_hears_of_it(served, qtbot) -> None:
    window = served
    call(window, qtbot, "create_rectangle", RECTANGLE)
    window.proposal_card.reject_button.click()
    assert not window.proposal_card.isVisible()
    assert dict(window.session.document.entities) == {}
    assert call(window, qtbot, "solve_status").note == Ended.CLOSED.value


def test_undoing_all_of_it_withdraws_the_proposal(served, qtbot) -> None:
    window = served
    call(window, qtbot, "create_rectangle", RECTANGLE)
    assert not call(window, qtbot, "undo").is_error
    assert window.agent.proposal is None
    assert not window.proposal_card.isVisible()
    assert call(window, qtbot, "solve_status").note is None  # the client did it itself


# --- When the user acts meanwhile -------------------------------------------------------


def test_an_edit_while_changes_are_pending_drops_them(served, qtbot) -> None:
    window, session = served, served.session
    call(window, qtbot, "create_rectangle", RECTANGLE)
    session.execute(CreateCircle(center=Point2(x=50, y=50), radius=3))
    response = call(window, qtbot, "create_rectangle", RECTANGLE)
    assert response.note == Ended.CHANGED.value
    assert response.content["created"] == ["e2"]
    assert window.agent.proposal.base is session.document  # a fresh proposal on the new sketch
    window.proposal_card.accept_button.click()
    assert len(session.document.entities) == 2


def test_accepting_a_stale_proposal_is_refused(served, qtbot) -> None:
    window, session = served, served.session
    call(window, qtbot, "create_rectangle", RECTANGLE)
    session.execute(CreateCircle(center=Point2(x=50, y=50), radius=3))
    window.proposal_card.accept_button.click()
    assert list(session.document.entities) == ["e1"]  # only the user's circle
    assert call(window, qtbot, "solve_status").note == Ended.CHANGED.value


def test_opening_another_document_drops_pending_changes(served, qtbot) -> None:
    window = served
    call(window, qtbot, "create_rectangle", RECTANGLE)
    window.session.new()
    assert window.agent.proposal is None
    assert call(window, qtbot, "inspect_document").note == Ended.OPENED.value


def test_while_the_in_app_assistant_works_changes_wait_but_looking_is_fine(served, qtbot) -> None:
    window = served
    window.agent.busy = True
    refused = call(window, qtbot, "create_rectangle", RECTANGLE)
    assert refused.is_error
    assert refused.content == {"error": BUSY}
    assert not call(window, qtbot, "solve_status").is_error
    window.agent.busy = False


def test_the_in_app_assistant_still_works_and_its_proposal_replaces_a_pending_one(
    served, qtbot
) -> None:
    window = served
    call(window, qtbot, "create_rectangle", RECTANGLE)
    circle = Reply(
        text="",
        calls=(ToolCall(id="c0", name="create_circle", arguments=CIRCLE),),
        stop=Stop.TOOLS,
    )

    class Scripted:
        name = "scripted"

        def __init__(self) -> None:
            self.replies = [circle, Reply(text="Added a circle.")]

        def reply(self, system, conversation, tools) -> Reply:
            return self.replies.pop(0)

    window.agent.set_assistant(Assistant(Scripted()))
    window.prompt_bar.input.setText("Add a circle.")
    window.prompt_bar.input.returnPressed.emit()
    qtbot.waitUntil(lambda: not window.agent.busy)
    assert window.proposal_card.title.text() == "Create Circle"
    assert call(window, qtbot, "solve_status").note == Ended.CLOSED.value
    window.proposal_card.accept_button.click()
    assert list(window.session.document.entities) == ["e1"]


# --- The socket -------------------------------------------------------------------------


def test_a_window_opens_no_socket_unless_asked(window) -> None:
    assert window.mcp is None


def test_the_socket_is_private(served) -> None:
    path = served.mcp.path
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_one_window_owns_the_socket_and_a_crashed_ones_is_replaced(
    served, new_window, socket_file: Path
) -> None:
    other = new_window()
    messages: list[str] = []
    other.session.message.connect(messages.append)
    assert not other.serve_mcp(socket_file)
    assert messages == ["Claude Desktop is connected to another Caliper window."]
    served.mcp.close()
    socket_file.touch()  # what a Caliper that crashed leaves behind
    assert other.serve_mcp(socket_file)
    other.mcp.close()


def test_a_garbled_request_is_answered_and_the_bridge_keeps_serving(served, qtbot) -> None:
    def garbage() -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(str(served.mcp.path))
            s.sendall(b"this is not json\n")
            return s.recv(65536)

    reply = in_background(qtbot, garbage)
    assert b'"is_error":true' in reply
    assert not call(served, qtbot, "solve_status").is_error


def test_without_the_app_the_client_is_told_to_open_it(socket_file: Path) -> None:
    with pytest.raises(BridgeError, match="Caliper isn't running"):
        ask(Request(client="claude-ai", tool="solve_status", arguments={}), socket_file)


# --- The whole way: an MCP client, the server, the bridge, the window ------------------


def test_an_mcp_client_reaches_the_window_through_the_server(served, qtbot) -> None:
    window = served

    async def session() -> tuple[int, bool]:
        async with Client(build(partial(ask, path=window.mcp.path))) as client:
            tools = (await client.list_tools()).tools
            result = await client.call_tool("create_rectangle", RECTANGLE)
            return len(tools), result.is_error

    count, is_error = in_background(qtbot, lambda: anyio.run(session))
    assert count == 21
    assert not is_error
    assert window.proposal_card.title.text() == "Create Rectangle"
    assert dict(window.session.document.entities) == {}
    window.proposal_card.accept_button.click()
    assert E1 in window.session.document.entities
