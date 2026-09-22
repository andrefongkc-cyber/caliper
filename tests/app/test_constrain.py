"""Adding constraints: actions enabled by what applies, the Constrain tool, and Q."""

import pytest
from PySide6.QtCore import Qt

from caliper.app.shortcuts import duplicate_shortcuts
from caliper.contracts.commands import (
    CreateConstraint,
    CreateLine,
    CreateRectangle,
    ModifyEntity,
)
from caliper.contracts.document import Constraint, ConstraintType, Feature, Line, Point2, Ref

P = Point2
T = ConstraintType


@pytest.fixture
def two_lines(window) -> tuple[str, str]:
    s = window.session
    (a,) = s.execute(CreateLine(start=P(x=0, y=0), end=P(x=60, y=2))).created_ids
    (b,) = s.execute(CreateLine(start=P(x=60, y=30), end=P(x=0, y=33))).created_ids
    s.bus.sent.clear()
    return a, b


def curve(id: str) -> Ref:
    return Ref(entity=id, feature=Feature.CURVE)


def enabled(window) -> set[ConstraintType]:
    return {t for t, a in window.constraint_actions.items() if a.isEnabled()}


def test_nothing_selected_offers_nothing_and_says_how(window, two_lines) -> None:
    assert enabled(window) == set()
    assert "Constrain (K)" in window.constraint_actions[T.PARALLEL].toolTip()


def test_two_selected_lines_offer_what_fits_two_lines(window, two_lines) -> None:
    window.session.set_selection(frozenset(two_lines))
    offered = enabled(window)
    assert {T.PARALLEL, T.PERPENDICULAR, T.EQUAL, T.COINCIDENT} <= offered
    assert T.CONCENTRIC not in offered  # lines have no centre
    assert T.HORIZONTAL not in offered  # one line or two points, not two lines
    assert T.PIERCE not in offered


def test_a_parallel_constraint_is_one_command_and_one_undo_step(window, bus, two_lines) -> None:
    a, b = two_lines
    window.session.set_selection(frozenset(two_lines))
    window.constraint_actions[T.PARALLEL].trigger()
    assert bus.sent == [CreateConstraint(type=T.PARALLEL, refs=(curve(a), curve(b)))]
    assert window.undo_action.text() == "Undo Add Parallel Constraint"
    window.undo_action.trigger()
    assert not any(isinstance(e, Constraint) for e in window.session.document.entities.values())


def test_the_h_key_makes_the_selected_line_horizontal(window, driver, two_lines) -> None:
    a, _ = two_lines
    window.session.set_selection(frozenset({a}))
    window.canvas.setFocus()
    driver.key(Qt.Key.Key_H)
    line = window.session.document.entities[a]
    assert isinstance(line, Line)
    assert line.start.y == pytest.approx(line.end.y)


def test_a_selected_rectangle_offers_nothing_until_a_side_is_picked(window, driver) -> None:
    (rect,) = window.session.execute(
        CreateRectangle(corner=P(x=0, y=0), width=40, height=20)
    ).created_ids
    window.session.set_selection(frozenset({rect}))
    assert enabled(window) == set()
    driver.tool("Constrain")
    driver.click(20, 0)  # the bottom side, away from its corners and midpoint features
    assert window.session.references == (Ref(entity=rect, feature=Feature.BOTTOM),)
    assert T.FIX in enabled(window)


def test_the_constrain_tool_picks_endpoints_in_order_and_i_joins_them(
    window, driver, bus, two_lines
) -> None:
    a, b = two_lines
    driver.tool("Constrain")
    driver.click(60, 2)  # a's end
    driver.click(60, 30)  # b's start
    end_a = Ref(entity=a, feature=Feature.END)
    start_b = Ref(entity=b, feature=Feature.START)
    assert window.session.references == (end_a, start_b)
    driver.key(Qt.Key.Key_I)
    (sent,) = bus.sent
    assert isinstance(sent, CreateConstraint)
    assert sent.type is T.COINCIDENT
    assert window.session.references == ()  # applied: ready to pick the next pair
    assert window.controller.active.name == "Constrain"
    line_b = window.session.document.entities[b]
    assert isinstance(line_b, Line)
    assert line_b.start == P(x=60, y=2)  # the second pick moved to the first


def test_clicking_a_pick_again_drops_it_and_esc_clears_then_leaves(
    window, driver, two_lines
) -> None:
    driver.tool("Constrain")
    driver.click(60, 2)
    driver.click(30, 17.5)  # nothing there
    assert window.session.references == ()
    driver.click(60, 2)
    driver.click(60, 2)
    assert window.session.references == ()
    driver.click(60, 2)
    driver.key(Qt.Key.Key_Escape)
    assert window.session.references == ()
    assert window.controller.active.name == "Constrain"
    driver.key(Qt.Key.Key_Escape)
    assert window.controller.active.name == "Select"


def test_a_rejected_constraint_keeps_the_picks_and_says_why(window, driver, two_lines) -> None:
    a, _ = two_lines
    window.session.set_selection(frozenset({a}))
    window.constraint_actions[T.HORIZONTAL].trigger()
    messages: list[str] = []
    window.session.message.connect(messages.append)
    window.constraint_actions[T.FIX].trigger()
    window.constraint_actions[T.VERTICAL].trigger()  # a fixed horizontal line can't turn
    assert any("conflicts" in m or "implied" in m for m in messages), messages


def test_q_toggles_construction_on_the_selection_as_one_step(window, bus, two_lines) -> None:
    a, b = two_lines
    window.session.set_selection(frozenset(two_lines))
    window.construction_action.trigger()
    assert bus.sent == [
        ModifyEntity(id=a, changes={"construction": True}),
        ModifyEntity(id=b, changes={"construction": True}),
    ]
    assert window.undo_action.text() == "Undo Toggle Construction"
    window.construction_action.trigger()  # all construction now: back to real
    entities = window.session.document.entities
    assert not any(entities[i].construction for i in two_lines)


def test_constraints_are_in_the_palette(window) -> None:
    window.command_panel.search.setText("parallel")
    assert "Parallel" in window.command_panel.visible_titles()


def test_no_two_actions_share_a_key(window) -> None:
    assert duplicate_shortcuts(window.findChildren(type(window.undo_action))) == {}


def test_q_on_a_mixed_selection_makes_all_of_it_construction(window, two_lines) -> None:
    a, _ = two_lines
    window.session.execute(ModifyEntity(id=a, changes={"construction": True}))
    window.session.set_selection(frozenset(two_lines))
    window.construction_action.trigger()
    entities = window.session.document.entities
    assert all(entities[i].construction for i in two_lines)
