"""Agent proposals: scripted plans, scratch preparation, review, accept and reject."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.app.agent.proposal import Plan, prepare
from caliper.app.agent.scripted import EXAMPLES, understand
from caliper.app.session import Author
from caliper.contracts.commands import CreateCircle, CreateRectangle, ModifyEntity
from caliper.contracts.document import Circle, Point2
from caliper.contracts.queries import Expectation, Metric
from caliper.engine.commands.bus import Bus


@pytest.fixture
def plate_bus() -> tuple[Bus, str]:
    bus = Bus()
    (plate,) = bus.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50)
    ).created_ids
    return bus, plate


# --- Scripted agent (no Qt) ---------------------------------------------------------------


@pytest.mark.parametrize("request_text", EXAMPLES)
def test_every_advertised_example_is_understood(plate_bus, request_text: str) -> None:
    bus, plate = plate_bus
    understood = understand(request_text, bus.document, frozenset({plate}))
    assert understood.plan is not None, understood.message
    assert prepare(understood.plan, bus.document, ()).acceptable


def test_corner_holes_are_verified_by_the_engine(plate_bus) -> None:
    bus, plate = plate_bus
    plan = understand("4 holes diameter 6 inset 10", bus.document, frozenset({plate})).plan
    assert plan is not None
    proposal = prepare(plan, bus.document, ())
    assert len(plan.commands) == 4
    assert len(proposal.checks) == 9
    assert all(c.after.passed for c in proposal.checks)
    assert all(c.after.actual == pytest.approx(10) for c in proposal.checks[:8])
    holes = [e for e in proposal.result.entities.values() if isinstance(e, Circle)]
    assert {(h.center.x, h.center.y) for h in holes} == {(10, 10), (110, 10), (110, 40), (10, 40)}


def test_proposal_never_touches_the_real_document(plate_bus) -> None:
    bus, plate = plate_bus
    before = bus.document
    plan = understand("make it 140 wide", bus.document, frozenset({plate})).plan
    assert plan is not None
    prepare(plan, bus.document, ())
    assert bus.document is before
    assert bus.undo_label == "Create Rectangle"


def test_user_checks_that_would_break_are_flagged(plate_bus) -> None:
    bus, plate = plate_bus
    mine = Expectation(metric=Metric.BBOX_WIDTH, expected=120, tolerance=0.01, ids=(plate,))
    plan = understand("make it 140 wide", bus.document, frozenset({plate})).plan
    assert plan is not None
    proposal = prepare(plan, bus.document, (mine,))
    (broken,) = proposal.broken_checks
    assert broken.expectation == mine
    assert broken.before.passed
    assert broken.after.actual == 140


def test_rejected_commands_make_the_proposal_unacceptable(plate_bus) -> None:
    bus, _ = plate_bus
    plan = Plan("Bad", "A negative radius.", (CreateCircle(center=Point2(x=0, y=0), radius=-1),))
    proposal = prepare(plan, bus.document, ())
    assert not proposal.acceptable
    assert proposal.errors[0].field == "radius"


@pytest.mark.parametrize(
    ("text", "selection", "reply"),
    [
        ("make it 140 wide", "none", "Select one shape first"),
        ("make e9 140 wide", "none", "There's no e9"),
        ("4 holes diameter 6 inset 40", "plate", "doesn't leave room"),
        ("3 holes diameter 6 inset 10", "plate", "only place 4 holes"),
        ("draw me a gearbox", "plate", "scripted stand-in"),
    ],
)
def test_requests_it_cant_do_are_explained(
    plate_bus, text: str, selection: str, reply: str
) -> None:
    bus, plate = plate_bus
    ids = frozenset({plate}) if selection == "plate" else frozenset()
    understood = understand(text, bus.document, ids)
    assert understood.plan is None
    assert reply in understood.message


# --- In the app ---------------------------------------------------------------------------


def ask(window, text: str) -> None:
    window.prompt_bar.input.setText(text)
    window.prompt_bar.input.returnPressed.emit()


@pytest.fixture
def plate(window) -> str:
    (plate,) = window.session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50)
    ).created_ids
    window.session.set_selection(frozenset({plate}))
    return plate


def test_asking_shows_a_proposal_without_changing_anything(window, bus, plate) -> None:
    before = window.session.document
    ask(window, "4 holes diameter 6 inset 10")
    assert window.proposal_card.isVisible()
    assert window.proposal_card.title.text() == "Add Corner Holes"
    assert window.session.document is before
    assert window.agent.proposal is not None


def test_accept_applies_one_undo_step_credited_to_the_agent(window, plate) -> None:
    ask(window, "4 holes diameter 6 inset 10")
    window.proposal_card.accept_button.click()
    session = window.session
    assert len(session.document.entities) == 5
    assert session.history[-1].label == "Add Corner Holes"
    assert session.history[-1].author is Author.AGENT
    assert window.undo_action.text() == "Undo Add Corner Holes"
    assert len(session.checks) == 9  # the agent's checks stay as requirements
    assert window.checks.summary.text() == "9 of 9 pass"
    assert not window.proposal_card.isVisible()
    window.undo_action.trigger()
    assert len(session.document.entities) == 1


def test_reject_discards_it(window, plate) -> None:
    ask(window, "make it 140 wide")
    window.proposal_card.reject_button.click()
    assert window.agent.proposal is None
    assert window.session.document.entities[plate].width == 120


def test_escape_rejects_from_the_prompt_and_the_canvas(window, qtbot, plate) -> None:
    ask(window, "make it 140 wide")
    qtbot.keyClick(window.prompt_bar.input, Qt.Key.Key_Escape)
    assert window.agent.proposal is None
    ask(window, "make it 140 wide")
    window.canvas.setFocus()
    qtbot.keyClick(window.canvas, Qt.Key.Key_Escape)
    assert window.agent.proposal is None
    assert window.controller.active.name == "Select"


def test_a_stale_proposal_is_refused(window, plate) -> None:
    ask(window, "make it 140 wide")
    window.session.execute(ModifyEntity(id=plate, changes={"height": 60.0}))
    window.agent.accept()
    assert window.session.document.entities[plate].width == 120
    assert "changed since that proposal" in window.statusBar().currentMessage()


def test_breaking_a_user_check_is_shown_before_accepting(window, plate) -> None:
    window.session.add_check(
        Expectation(metric=Metric.BBOX_WIDTH, expected=120, tolerance=0.01, ids=(plate,))
    )
    ask(window, "make it 140 wide")
    card = window.proposal_card
    assert card.accept_button.text() == "Accept anyway"
    assert "Breaks your check: Width of e1 = 120 ± 0.01" in card.warning.text()


def test_unknown_requests_explain_what_works(window, plate) -> None:
    ask(window, "draw me a gearbox")
    assert window.agent.proposal is None
    assert "scripted stand-in" in window.statusBar().currentMessage()


def test_proposal_is_drawn_as_ghost_geometry(window, qtbot, plate) -> None:
    window.resize(1600, 1000)  # room beside the card for the holes to be more than a few pixels
    qtbot.wait(20)
    window.canvas.zoom_to_fit()
    image_before = window.canvas.grab().toImage()
    ask(window, "4 holes diameter 6 inset 10")
    window.proposal_card.hide()  # its border shares the agent colour; measure the canvas only
    image_after = window.canvas.grab().toImage()
    from caliper.app import theme

    def agent_pixels(image) -> int:
        t = theme.AGENT
        return sum(
            1
            for x in range(0, image.width(), 1)
            for y in range(0, image.height(), 1)
            if abs(image.pixelColor(x, y).red() - t.red()) < 40
            and abs(image.pixelColor(x, y).green() - t.green()) < 40
            and abs(image.pixelColor(x, y).blue() - t.blue()) < 40
        )

    assert agent_pixels(image_after) > agent_pixels(image_before) + 50


def test_cmd_l_focuses_the_prompt(window, qtbot) -> None:
    window.canvas.setFocus()
    window.ask_action.trigger()
    assert QApplication.focusWidget() is window.prompt_bar.input
