"""Claude Desktop in the window: MCP calls arrive over the real socket and become proposals.

Everything is real except the far end: requests come from `caliper.ai.bridge.ask` (what
`caliper-mcp` sends) or the MCP SDK's client, on a worker thread, while the window answers on
the UI thread. No model and no API key are involved: over MCP, the client is the model.
"""

import dataclasses
import re
import shutil
import socket
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from PySide6.QtGui import QGuiApplication

from caliper.ai.agent import Assistant
from caliper.ai.bridge import BridgeError, Request, Response, ask
from caliper.ai.draft import Draft, Ended
from caliper.ai.mcp_server import MCP_TOOLS, build
from caliper.ai.model import Reply, Stop, ToolCall
from caliper.app.agent import proposal as proposal_module
from caliper.app.agent import ui as ui_module
from caliper.app.agent.estimate import INFORMED
from caliper.app.agent.mcp_host import BUSY
from caliper.app.agent.proposal import Plan, prepare
from caliper.app.agent.timing import RunTimer, Timing, markdown
from caliper.app.agent.ui import DETAILS_HEIGHT
from caliper.app.panels.timing import time_left
from caliper.app.session import Author
from caliper.contracts.commands import CreateCircle
from caliper.contracts.document import Circle, EntityId, Point2, Rectangle
from caliper.engine.commands import handlers
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
    (check,) = window.agent.proposal.checks
    assert check.agent
    assert check.expectation.expected == 100.0
    assert window.proposal_card.title.text() == "Create Rectangle"  # its check goes with it
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
    # Checks and repeats change the sketch too (a check is stored, C-1): they wait as well.
    for tool, arguments in (
        ("run_check", WIDTH_CHECK),
        ("linear_pattern", {"ids": ["e1"], "count": 2, "spacing": 5}),
        ("undo", {}),
    ):
        assert call(window, qtbot, tool, arguments).content == {"error": BUSY}, tool
    assert window.agent.proposal is None
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
    assert count == len(MCP_TOOLS)
    assert not is_error
    assert window.proposal_card.title.text() == "Create Rectangle"
    assert dict(window.session.document.entities) == {}
    window.proposal_card.accept_button.click()
    assert E1 in window.session.document.entities


# --- Large proposals ------------------------------------------------------------------


def comb(teeth: int) -> list[tuple[str, dict[str, object]]]:
    """A comb-shaped profile, one connected cluster so every constraint re-solves all of it:
    8 changes a tooth (3 lines, 3 joins, vertical, horizontal, and a height dimension)."""
    calls: list[tuple[str, dict[str, object]]] = []
    count, x, previous = 0, 0.0, None

    def add(name: str, arguments: dict[str, object]) -> str:
        nonlocal count
        calls.append((name, arguments))
        count += 1
        return f"e{count}"

    def line(a: tuple[float, float], b: tuple[float, float]) -> str:
        return add(
            "create_line",
            {"start": {"x": a[0], "y": a[1]}, "end": {"x": b[0], "y": b[1]}},
        )

    def join(a: str, b: str) -> None:
        refs = [{"entity": a, "feature": "end"}, {"entity": b, "feature": "start"}]
        add("create_constraint", {"type": "coincident", "refs": refs})

    for _ in range(teeth):
        up, top = line((x, 0), (x, 20)), line((x, 20), (x + 10, 20))
        if previous is not None:
            join(previous, up)
        join(up, top)
        add("create_constraint", {"type": "vertical", "refs": [{"entity": up, "feature": "curve"}]})
        add(
            "create_constraint",
            {"type": "horizontal", "refs": [{"entity": top, "feature": "curve"}]},
        )
        add(
            "create_dimension",
            {
                "refs": [{"entity": up, "feature": "curve"}],
                "placement": {"x": x - 5, "y": 10},
                "value": 20,
            },
        )
        previous = line((x + 10, 20), (x + 10, 0))
        join(top, previous)
        x += 10
    return calls


def test_a_proposal_from_a_workspace_is_the_same_without_replaying_it() -> None:
    base, draft = Bus().document, Draft()
    for name, arguments in comb(8):
        assert not draft.call(base, (), name, arguments).outcome.is_error
    check = {"metric": "bbox_height", "expected": 20, "tolerance": 0.001, "ids": ["e1"]}
    draft.call(base, (), "run_check", check)
    assert draft.workspace is not None
    assert len(draft.commands) > 60
    plan = Plan(draft.label, "", draft.commands)
    replayed = prepare(plan, base)
    reused = prepare(plan, base, result=draft.workspace.document)
    assert reused == replayed
    assert reused.errors == ()
    assert len(reused.checks) == 1
    assert all(c.after.passed for c in reused.checks)


def test_a_long_mcp_session_never_replays_the_proposal_and_accepts_as_one_step(
    served, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    replayed: list[object] = []

    class Counting(Bus):
        def execute(self, command, **kwargs):  # type: ignore[no-untyped-def]
            replayed.append(command)
            return super().execute(command, **kwargs)

    monkeypatch.setattr(proposal_module, "Bus", Counting)
    window, session = served, served.session
    calls = comb(4)
    for name, arguments in calls:
        assert not call(window, qtbot, name, arguments).is_error
    uprights = [
        f"e{i + 1}"
        for i, (name, arguments) in enumerate(calls)
        if name == "create_line" and arguments["start"] == {"x": arguments["end"]["x"], "y": 0}  # type: ignore[index]
    ]
    assert len(uprights) == 4
    for id in uprights:
        check = {"metric": "bbox_height", "expected": 20, "tolerance": 0.001, "ids": [id]}
        assert not call(window, qtbot, "run_check", check).is_error
    # Every change and check re-showed the proposal, and none of them replayed it (before,
    # the n-th change replayed all n: 1 + 2 + ... + n commands).
    assert replayed == []
    proposal = window.agent.proposal
    assert len(proposal.plan.commands) == len(calls) + 4  # each check is stored (C-1)
    assert len(proposal.checks) == 4
    assert all(c.agent and c.after.passed for c in proposal.checks)
    assert dict(session.document.entities) == {}  # still only a proposal
    history = len(session.history)
    window.proposal_card.accept_button.click()
    assert len(session.history) == history + 1  # one undo step
    assert session.history[-1].commands == proposal.plan.commands
    assert session.document == proposal.result
    window.undo_action.trigger()
    assert dict(session.document.entities) == {}
    window.redo_action.trigger()
    assert session.document == proposal.result


# --- The proposal card with many changes ------------------------------------------------


def within(window, widget) -> bool:
    """`widget` is shown and fits inside the window."""
    top_left = widget.mapTo(window, widget.rect().topLeft())
    bottom_right = widget.mapTo(window, widget.rect().bottomRight())
    return (
        widget.isVisible()
        and window.rect().contains(top_left)
        and window.rect().contains(bottom_right)
    )


def test_a_small_proposal_lists_its_changes_as_before(served, qtbot) -> None:
    card = served.proposal_card
    call(served, qtbot, "create_rectangle", RECTANGLE)
    assert card.details.isVisible()
    assert not card.details.verticalScrollBar().isVisible()
    assert "CreateRectangle" in card.commands.text()
    assert not card.summary.isVisible()
    assert not card.details_button.isVisible()


def test_the_list_of_changes_names_references_plainly(served, qtbot) -> None:
    # C-9: it read like Ref(entity='e1', feature=<Feature.END: 'end'>).
    card = served.proposal_card
    call(served, qtbot, "create_rectangle", RECTANGLE)
    call(served, qtbot, "create_circle", {"center": {"x": 30, "y": 20}, "radius": 4})
    call(
        served,
        qtbot,
        "create_constraint",
        {
            "type": "concentric",
            "refs": [{"entity": "e2", "feature": "curve"}, {"entity": "e1", "feature": "center"}],
        },
    )
    text = card.commands.text()
    assert "e2.curve" in text
    assert "e1.center" in text
    assert "Ref(" not in text
    assert "<Feature" not in text
    assert "type=concentric" in text


def test_a_large_proposal_collapses_to_a_summary_and_stops_growing(served, qtbot) -> None:
    window, card = served, served.proposal_card
    window.resize(1000, 700)
    calls = comb(5)
    for name, arguments in calls[:31]:
        call(window, qtbot, name, arguments)
    check = {"metric": "bbox_height", "expected": 20, "tolerance": 0.001, "ids": ["e1"]}
    call(window, qtbot, "run_check", check)
    assert card.summary.text() == "32 changes · 1 check, all passing"  # the check is one
    assert card.details_button.text() == "Show 32 changes ▾"
    assert not card.details.isVisible()
    assert "Height of e1" in card.checks.text()
    height = card.height()
    assert height < 300
    # More changes arrive: the card doesn't grow. (Not the rest of the comb: the check took
    # an id, e32, so the ids its later calls name are one out.)
    for k in range(13):
        call(window, qtbot, "create_circle", {"center": {"x": 10 * k, "y": 40}, "radius": 2})
    assert card.summary.text().startswith("45 changes · ")
    assert card.height() == height
    assert within(window, card.accept_button)
    assert within(window, card.reject_button)


def test_the_changes_can_be_shown_and_stay_bounded(served, qtbot) -> None:
    window, card = served, served.proposal_card
    window.resize(1000, 700)
    calls = comb(4)
    for name, arguments in calls[:-1]:
        call(window, qtbot, name, arguments)
    collapsed = card.height()
    card.details_button.click()
    assert card.details.isVisible()
    assert card.details_button.text() == "Hide changes ▴"
    assert card.details.height() <= DETAILS_HEIGHT
    assert card.details.verticalScrollBar().isVisible()  # all of it, by scrolling
    assert "CreateLine" in card.commands.text()
    assert card.height() <= collapsed + DETAILS_HEIGHT + card.layout().spacing()
    assert within(window, card.accept_button)
    call(window, qtbot, *calls[-1])  # an update keeps it open
    assert card.details.isVisible()
    card.reject_button.click()
    for name, arguments in comb(1):  # 7 changes: a new large proposal starts collapsed
        call(window, qtbot, name, arguments)
    assert card.large
    assert not card.details.isVisible()


def test_failing_checks_stay_visible_on_a_collapsed_proposal(served, qtbot) -> None:
    window, card = served, served.proposal_card
    for name, arguments in comb(2):
        call(window, qtbot, name, arguments)
    wrong = {"metric": "bbox_height", "expected": 25, "tolerance": 0.001, "ids": ["e1"]}
    call(window, qtbot, "run_check", wrong)
    assert card.large
    assert not card.details.isVisible()
    assert "1 failing" in card.summary.text()
    assert "✗" in card.checks.text()


def test_accepting_a_collapsed_proposal_applies_all_of_it(served, qtbot) -> None:
    window, card, session = served, served.proposal_card, served.session
    calls = comb(2)
    for name, arguments in calls:
        call(window, qtbot, name, arguments)
    assert card.large
    card.accept_button.click()
    assert session.history[-1].author is Author.AGENT
    assert len(session.history[-1].commands) == len(calls)
    assert not card.isVisible()


def test_a_first_proposal_on_an_empty_sketch_hides_the_empty_hint(served, qtbot) -> None:
    # C-8: the "empty sketch" hint drew over Claude's first proposal.
    window = served
    hint = window.canvas.empty_hint
    window.session.new()
    assert hint.isVisible()
    call(window, qtbot, "create_rectangle", RECTANGLE)
    assert window.proposal_card.isVisible()
    assert not hint.isVisible()
    window.proposal_card.reject_button.click()
    assert hint.isVisible()  # nothing was kept: empty again


def test_a_pattern_from_claude_desktop_is_one_proposal_and_one_undo_step(served, qtbot) -> None:
    window, session = served, served.session
    call(window, qtbot, "create_circle", {"center": {"x": 10, "y": 10}, "radius": 3})
    response = call(
        window,
        qtbot,
        "linear_pattern",
        {"ids": ["e1"], "count": 5, "spacing": 30, "count2": 4, "spacing2": 30},
    )
    assert not response.is_error
    assert window.assistant_log.lines()[-1] == "claude-ai → linear_pattern: Linear Pattern"
    window.proposal_card.accept_button.click()
    circles = [e for e in session.document.entities.values() if isinstance(e, Circle)]
    assert len(circles) == 20
    window.undo_action.trigger()
    assert dict(session.document.entities) == {}
    assert call(window, qtbot, "undo").is_error  # accepted: nothing of Claude's is pending


# --- Timing -----------------------------------------------------------------------------
# The host's clock is faked, and each call "takes" the seconds given in `took`: the fake clock
# moves on inside the draft, between the call's arrival and its answer. Exact numbers for the
# arithmetic are in test_timing.py; these check what the window measures and shows.


class Clock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def timed(served, monkeypatch):
    """The served window with a fake clock. Returns (window, clock, took)."""
    window, clock = served, Clock()
    took: dict[str, float] = {}
    window.mcp.timer = RunTimer(clock=clock)
    real = window.mcp.draft.call

    def slow(document, selection, name, arguments):
        clock.now += took.get(name, 0.0)
        return real(document, selection, name, arguments)

    monkeypatch.setattr(window.mcp.draft, "call", slow)
    return window, clock, took


def accept_taking(monkeypatch, seconds: float) -> None:
    """Make the next Accept take `seconds`, by the controller's clock."""
    times = iter([100.0, 100.0 + seconds])
    monkeypatch.setattr(ui_module, "perf_counter", lambda: next(times))


def shown(window) -> dict[str, str]:
    return {name: label.text() for name, label in window.timing.values.items()}


def test_timing_shows_only_while_claude_desktop_can_connect(window) -> None:
    assert window.timing_dock.isHidden()
    assert not window.start_run_action.isEnabled()


def test_every_call_is_timed_and_an_accepted_run_is_shown_in_full(
    timed, qtbot, monkeypatch
) -> None:
    window, clock, took = timed
    took.update(inspect_document=0.2, create_rectangle=1.5, run_check=0.3)
    assert window.timing.summary() == "no run yet"
    call(window, qtbot, "inspect_document")
    clock.now += 4
    call(window, qtbot, "create_rectangle", RECTANGLE)
    clock.now += 6
    call(window, qtbot, "run_check", WIDTH_CHECK)
    accept_taking(monkeypatch, 0.4)
    window.proposal_card.accept_button.click()
    values = shown(window)
    assert values["Total run"] == "0m 12s"  # 0.2 + 4 + 1.5 + 6 + 0.3
    assert values["MCP/tool calls"] == "3"
    assert values["First response"] == "N/A"  # Start run wasn't pressed
    assert values["Longest tool call"] == "1.5s"
    assert values["Proposal creation"] == "0m 08s"  # the rectangle to the check: 1.5 + 6 + 0.3
    assert values["Accept"] == "0.4s"


def test_start_run_measures_the_first_response(timed, qtbot) -> None:
    window, clock, took = timed
    took.update(inspect_document=0.2)
    window.timing.start_button.click()
    assert window.timing.summary() == "0m 00s · waiting for Claude…"  # ticking from the press
    clock.now += 7.3  # the prompt goes out and Claude thinks
    call(window, qtbot, "inspect_document")
    values = shown(window)
    assert values["First response"] == "7.3s"
    assert values["Total run"] == "0m 08s"
    assert values["Proposal creation"] == "N/A"  # it only looked
    assert values["Accept"] == "N/A"


def test_the_shortcut_starts_a_run_too(timed) -> None:
    window, _, _ = timed
    assert window.start_run_action.isEnabled()
    window.start_run_action.trigger()
    assert window.timing.timing is not None
    assert window.timing.timing.calls == 0


def test_a_rejected_proposal_has_no_accept_time(timed, qtbot) -> None:
    window, _, took = timed
    took.update(create_rectangle=2.0)
    call(window, qtbot, "create_rectangle", RECTANGLE)
    window.proposal_card.reject_button.click()
    values = shown(window)
    assert values["Proposal creation"] == "0m 02s"
    assert values["Accept"] == "N/A"


def test_a_stale_accept_that_is_refused_has_no_accept_time(timed, qtbot) -> None:
    window, _, _ = timed
    call(window, qtbot, "create_rectangle", RECTANGLE)
    window.session.execute(CreateCircle(center=Point2(x=50, y=50), radius=3))
    window.proposal_card.accept_button.click()
    assert shown(window)["Accept"] == "N/A"


def test_opening_another_document_starts_a_new_run_at_the_next_call(timed, qtbot) -> None:
    window, _, took = timed
    took.update(create_rectangle=3.0, inspect_document=0.1)
    call(window, qtbot, "create_rectangle", RECTANGLE)
    window.session.new()
    assert shown(window)["MCP/tool calls"] == "1"  # the finished run stays up
    call(window, qtbot, "inspect_document")
    values = shown(window)
    assert values["MCP/tool calls"] == "1"
    assert values["Longest tool call"] == "0.1s"
    assert values["Proposal creation"] == "N/A"


def test_real_calls_are_timed_by_the_real_clock(served, qtbot) -> None:
    window = served
    call(window, qtbot, "create_rectangle", RECTANGLE)
    call(window, qtbot, "run_check", WIDTH_CHECK)
    window.proposal_card.accept_button.click()
    timing = window.mcp.timer.timing
    assert timing is not None
    assert timing.calls == 2
    assert timing.longest_call is not None
    assert timing.total is not None
    assert 0 < timing.longest_call <= timing.total < 5
    assert timing.proposal_creation is not None
    assert timing.accept is not None


def test_the_section_is_one_line_until_opened_and_copies_timing_md(timed, qtbot) -> None:
    window, _, took = timed
    took.update(create_rectangle=65.0)
    call(window, qtbot, "create_rectangle", RECTANGLE)
    section = window.timing
    assert not window.timing_dock.isHidden()
    assert not section.expanded
    assert section.details.isHidden()
    assert section.heading == "▸ 1m 05s · no estimate"  # running; Claude gave no plan
    assert section.toggle.text() == section.heading  # it fits
    section.toggle.click()
    assert not section.details.isHidden()
    section.copy_button.click()
    assert QGuiApplication.clipboard().text() == markdown(section.timing)
    assert window.statusBar().currentMessage() == "Copied the timing for 003-timing.md"


def test_the_panel_ticks_while_the_run_is_live_and_settles_on_accept(
    timed, qtbot, monkeypatch
) -> None:
    window, clock, took = timed
    took.update(create_rectangle=2.0)
    call(window, qtbot, "create_rectangle", RECTANGLE)
    panel = window.timing
    assert panel.ticking
    clock.now += 40  # you're reviewing the proposal
    panel._refresh()  # what the one-second tick does
    assert shown(window)["Total run"] == "0m 42s"
    accept_taking(monkeypatch, 0.4)
    window.proposal_card.accept_button.click()
    assert not panel.ticking
    assert shown(window)["Total run"] == "0m 02s"  # from the start to Claude's last call


def test_claude_s_estimate_shows_the_time_left_and_done_stops_the_time(timed, qtbot) -> None:
    window, clock, took = timed
    took.update(create_rectangle=2.0)
    panel = window.timing
    panel.start_button.click()
    clock.now += 5
    answer = call(window, qtbot, "report_progress", {"calls_left": 30, "checks": 4})
    assert answer.content == {"ok": True, "done": False}
    assert window.proposal_card.isHidden()  # it changed nothing
    # The first estimate is the prior's: rough, so to the minute. No call count on the line.
    assert panel.heading == "▸ 0m 05s · about 1 min left"
    call(window, qtbot, "create_rectangle", RECTANGLE)
    for _ in range(INFORMED):
        clock.now += 3  # Claude working out the next call
        call(window, qtbot, "inspect_document")
    # Measured now: to the second, and counting down between calls.
    assert re.fullmatch(r"▸ 0m 37s · ~\dm \d\ds left", panel.heading), panel.heading
    call(window, qtbot, "report_progress", {"calls_left": 0})
    assert not panel.ticking
    assert panel.heading == "▸ 0m 37s"  # stopped at once, before any Accept: just the time
    assert shown(window)["MCP/tool calls"] == "13"  # the count is in the fields
    assert not window.proposal_card.isHidden()  # the proposal still waits for you


BASE = Timing(
    date=date(2026, 9, 28),
    total=None,
    calls=5,
    first_response=None,
    longest_call=None,
    proposal_creation=None,
    accept=None,
    elapsed=30.0,
)


@pytest.mark.parametrize(
    ("changes", "text"),
    [
        ({}, "no estimate"),
        ({"expected": 5}, "finishing…"),
        ({"expected": 40, "left": 200.0, "rough": True}, "about 3 min left"),
        ({"expected": 40, "left": 25.0, "rough": True}, "under a minute left"),
        ({"expected": 40, "left": 65.4}, "~1m 05s left"),
        ({"expected": 40, "left": 0.4}, "finishing…"),
    ],
    ids=["no plan", "plan done", "rough", "rough and short", "measured", "run out"],
)
def test_the_line_says_how_long_is_left_only_as_precisely_as_it_knows(
    changes: dict[str, object], text: str
) -> None:
    assert time_left(dataclasses.replace(BASE, **changes)) == text  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({}, "calls_left"),
        ({"calls_left": -1}, "calls_left"),
        ({"calls_left": 2.5}, "calls_left"),
        ({"calls_left": 3, "checks": -2}, "checks"),
        ({"calls_left": 3, "repeats": True}, "repeats"),
    ],
)
def test_a_bad_estimate_is_refused_and_changes_nothing(timed, qtbot, arguments, field) -> None:
    window, _, _ = timed
    answer = call(window, qtbot, "report_progress", arguments)
    assert answer.is_error
    assert field in answer.content["error"]
    assert window.mcp.timer.timing is not None
    assert window.mcp.timer.timing.expected is None


def test_a_note_about_the_draft_waits_for_a_call_that_uses_it(timed, qtbot) -> None:
    window, _, _ = timed
    call(window, qtbot, "create_rectangle", RECTANGLE)
    window.proposal_card.reject_button.click()
    assert call(window, qtbot, "report_progress", {"calls_left": 3}).note is None
    assert call(window, qtbot, "inspect_document").note == Ended.CLOSED.value


def test_saving_into_a_test_folder_writes_its_timing_file(timed, qtbot, tmp_path) -> None:
    window, _, took = timed
    took.update(create_rectangle=2.0)
    call(window, qtbot, "create_rectangle", RECTANGLE)
    window.proposal_card.accept_button.click()
    folder = tmp_path / "test-runs-manual" / "004-demo"
    folder.mkdir(parents=True)
    assert window._save_to(folder / "demo.caliper")
    written = (folder / "003-timing.md").read_text()
    assert written == markdown(window.mcp.timer.timing)
    assert window.statusBar().currentMessage() == "Saved demo.caliper · wrote 003-timing.md"
    (folder / "003-timing.md").write_text("mine")
    assert window._save_to(folder / "demo.caliper")
    assert (folder / "003-timing.md").read_text() == "mine"  # never overwritten


def test_saving_elsewhere_or_with_no_run_writes_no_timing(timed, qtbot, tmp_path) -> None:
    window, _, _ = timed
    folder = tmp_path / "test-runs-manual" / "005-empty"
    folder.mkdir(parents=True)
    assert window._save_to(folder / "empty.caliper")  # no run yet
    assert not (folder / "003-timing.md").exists()
    call(window, qtbot, "create_rectangle", RECTANGLE)
    assert window._save_to(tmp_path / "part.caliper")
    assert list(tmp_path.glob("*.md")) == []


# --- Performance V2: what each call and Accept no longer redo --------------------------------


def test_accept_commits_what_the_draft_already_solved(served, qtbot, monkeypatch) -> None:
    """Accept runs every command through the session's bus, in one undo step, but each has
    already run on this very document in the draft: nothing is validated or solved again."""
    window, session = served, served.session
    calls = comb(3)
    for name, arguments in calls:
        call(window, qtbot, name, arguments)
    executed = window.mcp.draft.executed
    assert executed is not None
    built = executed.result
    solved: list[object] = []
    real = handlers._apply
    monkeypatch.setattr(handlers, "_apply", lambda d, c: solved.append(c) or real(d, c))
    announced: list[object] = []
    session.changed.connect(announced.append)
    window.proposal_card.accept_button.click()
    assert solved == []
    assert session.document is built  # the draft's own document, not a rebuilt copy
    assert len(announced) == 1  # the views heard of every change at once
    assert session.history[-1].author is Author.AGENT
    assert len(session.history[-1].commands) == len(calls)
    window.undo_action.trigger()
    assert dict(session.document.entities) == {}


def test_a_large_collapsed_proposal_doesnt_list_its_changes_on_every_call(
    served, qtbot, monkeypatch
) -> None:
    window, card = served, served.proposal_card
    listed: list[object] = []
    real = ui_module._command_text
    monkeypatch.setattr(ui_module, "_command_text", lambda c: listed.append(c) or real(c))
    calls = comb(2)
    for name, arguments in calls:
        call(window, qtbot, name, arguments)
    assert card.large
    assert not card.details.isVisible()
    assert len(listed) == 6 * 7 // 2  # only while the proposal was small enough to show
    listed.clear()
    card.details_button.click()
    assert len(listed) == len(calls)  # shown now: written once, in full
    assert "CreateLine" in card.commands.text()


def test_a_growing_proposal_moves_the_view_only_when_it_outgrows_it(served, qtbot) -> None:
    window = served
    canvas = window.canvas
    call(window, qtbot, "create_rectangle", RECTANGLE)
    framed = (canvas.view.scale, canvas.view.origin_x, canvas.view.origin_y)
    call(window, qtbot, "create_circle", {"center": {"x": 50, "y": 25}, "radius": 5})  # inside
    assert (canvas.view.scale, canvas.view.origin_x, canvas.view.origin_y) == framed
    call(window, qtbot, "create_circle", {"center": {"x": 900, "y": 25}, "radius": 5})  # outside
    assert canvas.view.scale < framed[0]


def test_a_check_run_with_nothing_pending_is_proposed_then_kept(served, qtbot) -> None:
    # AI-1 put such a check straight into the Checks panel. It's stored in the sketch now
    # (C-1), so like any change from Claude it's proposed, and the user accepts it.
    window, session = served, served.session
    call(window, qtbot, "create_rectangle", RECTANGLE)
    window.proposal_card.accept_button.click()
    assert session.checks == ()
    assert not call(window, qtbot, "run_check", WIDTH_CHECK).is_error  # after Accept
    assert window.proposal_card.title.text() == "Create Check"
    assert session.checks == ()  # not until it's accepted
    call(window, qtbot, "run_check", WIDTH_CHECK)  # the same check again: proposed once
    assert len(window.agent.proposal.checks) == 1
    window.proposal_card.accept_button.click()
    assert [c.expected for c in session.checks] == [100.0]
    assert window.checks.summary.text() == "1 of 1 pass"
    window.undo_action.trigger()
    assert session.checks == ()


def test_tool_results_show_what_changed_without_echoing_the_command(served, qtbot) -> None:
    window = served
    response = call(window, qtbot, "create_rectangle", RECTANGLE)
    assert "command" not in response.content
    assert response.content["created"] == ["e1"]
    assert response.content["changed"]["added"]["e1"]["width"] == 100.0
