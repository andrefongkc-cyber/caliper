"""A proposal waits with its tab (C-18).

Each tab is its own document, and an agent works on the one shown. Switching tabs used to
drop a pending proposal, as opening a file does. The tab left is untouched while the other is
shown, so its proposal is still good: it waits there, and it's on the card again on the way
back, to accept or reject as before. Claude Desktop is told where its changes are, and its
calls work on the tab shown meanwhile.
"""

import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from caliper.ai.bridge import Request, Response, ask
from caliper.app.agent.proposal import Plan
from caliper.app.main_window import MainWindow
from caliper.contracts.commands import CreateCircle, CreateExtrude, CreateRectangle
from caliper.contracts.document import Circle, Extrude, Point2, Rectangle
from caliper.engine import features, geometry
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.app.parts import sketch_on

RECTANGLE = CreateRectangle(corner=Point2(x=0, y=0), width=100, height=50)


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def propose(window: MainWindow, label: str, *commands: object) -> None:
    window.agent.propose(Plan(label, "What the agent would do.", commands), window.session.document)  # type: ignore[arg-type]
    QApplication.processEvents()


def kinds(window: MainWindow) -> list[type]:
    return [type(e) for e in window.session.document.entities.values()]


# --- The card -----------------------------------------------------------------------------------


def test_a_proposal_waits_on_its_tab_and_is_back_on_the_card_after_a_switch(
    window: MainWindow,
) -> None:
    propose(window, "Add Plate", RECTANGLE)
    waiting = window.agent.proposal
    assert window.proposal_card.isVisible()
    window.set_mode("3d")
    assert window.agent.proposal is None  # nothing is proposed for the part
    assert not window.proposal_card.isVisible()
    window.set_mode("2d")
    assert window.agent.proposal is waiting
    assert window.proposal_card.isVisible()
    assert window.proposal_card.title.text() == "Add Plate"
    assert kinds(window) == []  # still only proposed
    window.proposal_card.accept_button.click()
    assert Rectangle in kinds(window)
    window.undo_action.trigger()
    assert kinds(window) == []  # one undo step, as it would have been


def test_each_tab_keeps_its_own_proposal(window: MainWindow) -> None:
    propose(window, "Add Plate", RECTANGLE)
    sketch = sketch_on(window)  # to the 3D tab, and a profile to extrude there
    window.session.execute(RECTANGLE)
    window.finish_sketch()
    propose(window, "Extrude", CreateExtrude(depth=10.0, sketch=sketch))
    assert window.proposal_card.title.text() == "Extrude"
    window.set_mode("2d")
    assert window.proposal_card.title.text() == "Add Plate"
    window.set_mode("3d")
    QApplication.processEvents()
    assert window.proposal_card.title.text() == "Extrude"
    assert window.view3d.scene.proposed  # and seen as it was (C-16)
    window.proposal_card.accept_button.click()
    assert any(isinstance(f, Extrude) for f in window.session.document.features)
    window.set_mode("2d")
    assert window.proposal_card.title.text() == "Add Plate"  # the other still waits
    assert kinds(window) == []


def test_rejecting_on_one_tab_leaves_nothing_waiting_there(window: MainWindow) -> None:
    propose(window, "Add Plate", RECTANGLE)
    window.agent.reject()
    window.set_mode("3d")
    window.set_mode("2d")
    assert window.agent.proposal is None
    assert not window.proposal_card.isVisible()


def test_a_new_document_in_the_tab_still_drops_its_proposal(window: MainWindow) -> None:
    propose(window, "Add Plate", RECTANGLE)
    window.new_action.trigger()
    assert window.agent.proposal is None
    window.set_mode("3d")
    window.set_mode("2d")
    assert window.agent.proposal is None  # dropped, not waiting


def test_a_proposal_made_while_another_waited_for_this_tab_cannot_happen_twice(
    window: MainWindow,
) -> None:
    """Back on a tab whose proposal waited, that proposal is the one on the card; a new one
    asked for there replaces it, as a new proposal always replaces the one showing."""
    propose(window, "Add Plate", RECTANGLE)
    window.set_mode("3d")
    window.set_mode("2d")
    propose(window, "Add Hole", CreateCircle(center=Point2(x=10, y=10), radius=3))
    assert window.proposal_card.title.text() == "Add Hole"
    window.set_mode("3d")
    window.set_mode("2d")
    assert window.proposal_card.title.text() == "Add Hole"
    window.proposal_card.accept_button.click()
    assert kinds(window) == [Circle]


# --- Claude Desktop -----------------------------------------------------------------------------


@pytest.fixture
def served(window: MainWindow) -> Iterator[MainWindow]:
    directory = Path(tempfile.mkdtemp(prefix="cal", dir="/tmp"))  # a socket path stays short
    assert window.serve_mcp(directory / "mcp.sock")
    yield window
    assert window.mcp is not None
    window.mcp.close()
    shutil.rmtree(directory, ignore_errors=True)


def in_background(qtbot, work: Callable[[], Any]) -> Any:  # type: ignore[no-untyped-def]
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


def call(window, qtbot, tool: str, arguments: dict[str, object] | None = None) -> Response:  # type: ignore[no-untyped-def]
    request = Request(client="claude-ai", tool=tool, arguments=arguments or {})
    return in_background(qtbot, partial(ask, request, window.mcp.path, timeout=10))


PLATE = {"corner": {"x": 0, "y": 0}, "width": 100, "height": 50}
HOLE = {"center": {"x": 10, "y": 10}, "radius": 3}


def test_claudes_pending_changes_wait_on_their_tab_and_it_is_told_where_they_are(
    served: MainWindow, qtbot
) -> None:  # type: ignore[no-untyped-def]
    window = served
    assert not call(window, qtbot, "create_rectangle", PLATE).is_error
    assert window.proposal_card.isVisible()
    window.set_mode("3d")
    assert not window.proposal_card.isVisible()
    looked = call(window, qtbot, "inspect_document")
    assert not looked.is_error
    assert "3D part tab" in (looked.note or "")
    assert "not dropped" in (looked.note or "")
    assert looked.content["entity_count"] == 0  # the part, which is what's shown
    again = call(window, qtbot, "inspect_document")
    assert again.note is None  # said once


def test_claudes_first_change_after_a_switch_is_held_back_not_drawn_on_the_other_tab(
    served: MainWindow, qtbot
) -> None:  # type: ignore[no-untyped-def]
    """Claude sends its next change before it knows the user switched tabs: meant for the 2D
    sketch, it would land in the part (test run 007 drew its hole there). It's refused, with
    where things stand, and nothing changes; sent again, it's Claude's choice and it's done."""
    window = served
    call(window, qtbot, "create_rectangle", PLATE)
    window.set_mode("3d")
    part = window.session.document
    held = call(window, qtbot, "create_circle", HOLE)
    assert held.is_error
    said = held.content["error"]
    assert said.startswith("Nothing was changed.")
    assert "3D part tab" in said
    assert "not dropped" in said
    assert window.agent.proposal is None  # nothing proposed for the part
    assert window.session.document is part
    again = call(window, qtbot, "create_circle", HOLE)
    assert not again.is_error, again.content
    assert again.note is None  # it has been told
    assert window.proposal_card.isVisible()  # the part's own proposal now
    window.set_mode("2d")
    assert window.proposal_card.title.text() == "Create Rectangle"  # the plate still waits


def test_back_on_the_tab_claude_adds_to_the_same_proposal(served: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    window = served
    call(window, qtbot, "create_rectangle", PLATE)
    window.set_mode("3d")
    window.set_mode("2d")  # away and back between two calls: nothing to tell
    assert window.proposal_card.isVisible()
    added = call(window, qtbot, "create_circle", HOLE)
    assert not added.is_error
    assert added.note is None
    assert len(window.agent.proposal.plan.commands) == 2  # type: ignore[union-attr]
    window.proposal_card.accept_button.click()
    assert sorted(k.__name__ for k in kinds(window)) == ["Circle", "Rectangle"]
    window.undo_action.trigger()
    assert kinds(window) == []  # one step
    after = call(window, qtbot, "inspect_document")
    assert "accepted" in (after.note or "")  # as accepting always says


def test_a_draft_on_each_tab_and_closing_one_tells_claude_on_that_tab(
    served: MainWindow, qtbot
) -> None:  # type: ignore[no-untyped-def]
    window = served
    call(window, qtbot, "create_rectangle", PLATE)
    window.set_mode("3d")
    assert call(window, qtbot, "create_sketch", {"plane": "xy"}).is_error  # held back, once
    made = call(window, qtbot, "create_sketch", {"plane": "xy"})
    assert not made.is_error, made.content
    assert window.proposal_card.isVisible()  # the part's own proposal
    window.set_mode("2d")
    assert window.proposal_card.title.text() == "Create Rectangle"
    window.proposal_card.reject_button.click()
    told = call(window, qtbot, "inspect_document")
    assert "closed" in (told.note or "")
    window.set_mode("3d")
    assert window.proposal_card.isVisible()  # the part's still waits
    assert window.agent.proposal is not None


def test_a_run_being_timed_goes_on_across_a_switch(served: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    window = served
    call(window, qtbot, "create_rectangle", PLATE)
    assert window.mcp is not None
    timing = window.mcp.timer.timing
    assert timing is not None
    assert timing.live
    window.set_mode("3d")
    window.set_mode("2d")
    call(window, qtbot, "create_circle", HOLE)
    after = window.mcp.timer.timing
    assert after is not None
    assert after.live
    assert after.calls == timing.calls + 1  # the same run, one call on
