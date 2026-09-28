"""Performance V2 in the window: work the UI no longer repeats, with the same end result.

- A transaction (Accept, Toggle Construction) is announced to the views once, when it closes.
- The Assistant log appends in constant time: it shows the last lines, and keeps every one.
- Hidden constraint glyphs cost nothing: where they'd hang isn't even worked out.
- A turn of the in-app assistant that only checked keeps its checks.
"""

from caliper.ai.model import Reply
from caliper.app.panels import assistant as assistant_panel
from caliper.app.panels.assistant import AssistantLog
from caliper.app.viewport import glyphs
from caliper.contracts.commands import CreateCircle, CreateConstraint, CreateLine
from caliper.contracts.document import ConstraintType, EntityId, Feature, Point2, Ref
from caliper.contracts.queries import Metric
from tests.app.test_assistant import WIDTH_CHECK, ask, calls, with_model


def test_a_transaction_is_announced_to_the_views_once(window) -> None:
    session = window.session
    announced: list[object] = []
    session.changed.connect(announced.append)
    redrawn: list[None] = []
    session.document_changed.connect(lambda: redrawn.append(None))
    with session.transaction("Three circles"):
        for n in range(3):
            session.execute(CreateCircle(center=Point2(x=10.0 * n, y=0), radius=2))
        assert announced == []  # not yet: views would redraw three times
    assert len(announced) == 1
    assert len(redrawn) == 1
    (change,) = announced
    assert change.delta.added == {EntityId("e1"), EntityId("e2"), EntityId("e3")}
    assert set(window.browser.items) == {"e1", "e2", "e3"}  # views built from the one change
    assert session.bus.undo_label == "Three circles"


def test_a_transaction_that_fails_announces_nothing(window) -> None:
    session = window.session
    announced: list[object] = []
    session.changed.connect(announced.append)
    try:
        with session.transaction("Doomed"):
            session.execute(CreateCircle(center=Point2(x=0, y=0), radius=2))
            raise RuntimeError("stop")
    except RuntimeError:
        pass
    assert announced == []
    assert dict(session.document.entities) == {}


def test_the_log_shows_the_latest_lines_and_keeps_them_all(window, monkeypatch) -> None:
    monkeypatch.setattr(assistant_panel, "SHOWN_LINES", 5)
    log = AssistantLog(window.agent)
    for n in range(12):
        log._add(f"line {n}", log.palette().text().color())
    assert log.document().blockCount() == 5  # only the last five are laid out and shown
    assert log.toPlainText().splitlines() == [f"line {n}" for n in range(7, 12)]
    assert log.lines() == [f"line {n}" for n in range(12)]  # nothing lost


def test_hidden_glyphs_cost_nothing_until_they_are_shown(window, monkeypatch) -> None:
    session, canvas = window.session, window.canvas
    session.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=50, y=1)))
    session.execute(
        CreateConstraint(
            type=ConstraintType.HORIZONTAL,
            refs=(Ref(entity=EntityId("e1"), feature=Feature.CURVE),),
        )
    )
    window.constraints_action.trigger()  # hide them
    hung: list[EntityId] = []
    real = glyphs.hanging
    monkeypatch.setattr(glyphs, "hanging", lambda q, d, id: hung.append(id) or real(q, d, id))
    canvas.repaint()
    session.execute(CreateLine(start=Point2(x=0, y=10), end=Point2(x=50, y=12)))
    canvas.repaint()
    assert canvas.constraint_glyphs == []
    assert hung == []
    window.constraints_action.trigger()  # show them again: worked out once, then drawn
    canvas.repaint()
    assert [g.id for g in canvas.constraint_glyphs] == ["e2"]
    assert hung


def test_an_assistant_turn_that_only_checks_keeps_its_checks(window, qtbot) -> None:
    window.session.execute(CreateCircle(center=Point2(x=50, y=25), radius=50))  # e1: 100 wide
    with_model(window, calls(WIDTH_CHECK), Reply(text="It's 100 wide."))
    ask(window, qtbot, "check the width")
    assert window.agent.proposal is None  # nothing to review: it only looked
    assert [(c.metric, c.expected) for c in window.session.checks] == [(Metric.BBOX_WIDTH, 100.0)]
