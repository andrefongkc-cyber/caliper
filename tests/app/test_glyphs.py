"""Constraint glyphs: where they go, what they say, and clicking, hovering, and hiding them."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFontMetricsF, QMouseEvent
from PySide6.QtWidgets import QApplication

from caliper.app import theme
from caliper.app.viewport.glyphs import SYMBOL
from caliper.contracts.commands import (
    CreateConstraint,
    CreateDimension,
    CreateLine,
    DeleteEntities,
)
from caliper.contracts.document import ConstraintType, Feature, Point2, Ref

P = Point2


def line(window, a: Point2, b: Point2) -> str:
    (id,) = window.session.execute(CreateLine(start=a, end=b)).created_ids
    return id


def constrain(window, type: ConstraintType, *refs: Ref) -> str:
    (id,) = window.session.execute(CreateConstraint(type=type, refs=refs)).created_ids
    return id


def curve(id: str) -> Ref:
    return Ref(entity=id, feature=Feature.CURVE)


def centre(glyph) -> QPoint:
    return glyph.rect.center().toPoint()


def hover_at(window, at: QPoint) -> None:
    """A real mouse move event; QTest drops moves to where it thinks the cursor already is."""
    position = QPointF(at)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        position,
        window.canvas.mapToGlobal(position),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.canvas, event)


def click_at(qtbot, window, at: QPoint) -> None:
    canvas = window.canvas
    qtbot.mouseMove(canvas, at)
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)


def test_every_symbol_is_in_the_canvas_font(qapp) -> None:
    metrics = QFontMetricsF(theme.font(size=theme.TYPE.caption))
    assert set(SYMBOL) == set(ConstraintType)
    for type, symbol in SYMBOL.items():
        assert all(metrics.inFontUcs4(ord(ch)) for ch in symbol), type


def test_a_horizontal_line_gets_one_glyph_above_its_middle(window) -> None:
    a = line(window, P(x=0, y=20), P(x=60, y=21))
    h = constrain(window, ConstraintType.HORIZONTAL, curve(a))
    (glyph,) = window.canvas.constraint_glyphs
    assert glyph.id == h
    assert glyph.symbol == "H"
    mid_x, mid_y = window.canvas.view.to_widget(P(x=30, y=20.5))
    assert abs(glyph.rect.center().x() - mid_x) < 1
    assert glyph.rect.center().y() < mid_y  # above: the left of a left-to-right line


def test_a_pair_gets_a_glyph_on_each_and_a_coincidence_only_one(window) -> None:
    a = line(window, P(x=0, y=0), P(x=60, y=0))
    b = line(window, P(x=0, y=30), P(x=60, y=32))
    constrain(window, ConstraintType.PARALLEL, curve(a), curve(b))
    assert len(window.canvas.constraint_glyphs) == 2
    constrain(
        window,
        ConstraintType.COINCIDENT,
        Ref(entity=a, feature=Feature.START),
        Ref(entity=b, feature=Feature.START),
    )
    dot = SYMBOL[ConstraintType.COINCIDENT]
    coincident = [g for g in window.canvas.constraint_glyphs if g.symbol == dot]
    assert len(coincident) == 1


def test_glyphs_on_the_same_spot_stack_instead_of_overlapping(window) -> None:
    a = line(window, P(x=0, y=20), P(x=60, y=21))
    b = line(window, P(x=0, y=50), P(x=60, y=53))
    constrain(window, ConstraintType.HORIZONTAL, curve(a))
    constrain(window, ConstraintType.PARALLEL, curve(a), curve(b))
    mid_x, mid_y = window.canvas.view.to_widget(P(x=30, y=20.5))
    on_a = [
        g
        for g in window.canvas.constraint_glyphs
        if abs(g.rect.center().y() - mid_y) < 60 and abs(g.rect.center().x() - mid_x) < 60
    ]
    assert len(on_a) == 2  # H and one of the parallel pair, both off a's middle
    first, second = on_a
    assert not first.rect.intersects(second.rect)


def test_clicking_a_glyph_selects_its_constraint_and_delete_removes_it(window, qtbot) -> None:
    a = line(window, P(x=0, y=20), P(x=60, y=21))
    h = constrain(window, ConstraintType.HORIZONTAL, curve(a))
    (glyph,) = window.canvas.constraint_glyphs
    click_at(qtbot, window, centre(glyph))
    assert window.session.selection == frozenset({h})
    window.delete_action.trigger()
    assert h not in window.session.document.entities
    assert a in window.session.document.entities
    assert window.canvas.constraint_glyphs == []


def test_hovering_a_glyph_hovers_the_constraint_and_explains_it(window, driver, qtbot) -> None:
    a = line(window, P(x=0, y=20), P(x=60, y=21))
    h = constrain(window, ConstraintType.HORIZONTAL, curve(a))
    (glyph,) = window.canvas.constraint_glyphs
    hover_at(window, centre(glyph))
    assert window.session.hover == h
    assert window.canvas.toolTip() == f"Horizontal {h}: {a} curve"


def test_a_glyph_wins_over_the_geometry_under_it(window, qtbot) -> None:
    a = line(window, P(x=0, y=20), P(x=60, y=21))
    b = line(window, P(x=0, y=23), P(x=60, y=23))  # runs right under a's glyph
    h = constrain(window, ConstraintType.HORIZONTAL, curve(a))
    (glyph,) = window.canvas.constraint_glyphs
    click_at(qtbot, window, centre(glyph))
    assert window.session.selection == frozenset({h})
    del b


def test_hiding_constraints_removes_glyphs_and_lets_clicks_through(window, qtbot) -> None:
    a = line(window, P(x=0, y=20), P(x=60, y=21))
    constrain(window, ConstraintType.HORIZONTAL, curve(a))
    (glyph,) = window.canvas.constraint_glyphs
    window.constraints_action.trigger()
    assert window.canvas.constraint_glyphs == []
    click_at(qtbot, window, centre(glyph))
    assert window.session.selection == frozenset()


def test_clicking_a_dimension_label_selects_the_dimension(window, qtbot) -> None:
    a = line(window, P(x=0, y=20), P(x=60, y=20))
    (dim,) = window.session.execute(
        CreateDimension(refs=(curve(a),), placement=P(x=30, y=40))
    ).created_ids
    wx, wy = window.canvas.view.to_widget(P(x=30, y=40))
    click_at(qtbot, window, QPoint(round(wx), round(wy)))
    assert window.session.selection == frozenset({dim})


def test_deleting_the_line_takes_its_glyphs_with_it(window) -> None:
    a = line(window, P(x=0, y=20), P(x=60, y=21))
    constrain(window, ConstraintType.HORIZONTAL, curve(a))
    window.session.execute(DeleteEntities(ids=(a,)))
    assert window.canvas.constraint_glyphs == []


def test_an_edit_re_measures_only_the_labels_it_touched(window, monkeypatch) -> None:
    from caliper.app.viewport import canvas as canvas_module
    from caliper.contracts.commands import CreateRectangle, ModifyEntity

    s = window.session
    rects = []
    for k in range(5):
        (rect,) = s.execute(
            CreateRectangle(corner=P(x=k * 20, y=0), width=10, height=5)
        ).created_ids
        rects.append(rect)
        bottom = Ref(entity=rect, feature=Feature.BOTTOM)
        s.execute(CreateDimension(refs=(bottom,), placement=P(x=k * 20 + 5, y=-8)))
    window.canvas.annotation_at(0, 0)  # builds every spot once
    calls: list[str] = []
    real = canvas_module.label_spot

    def counting(source, id):
        calls.append(id)
        return real(source, id)

    monkeypatch.setattr(canvas_module, "label_spot", counting)
    s.execute(ModifyEntity(id=rects[2], changes={"width": 12.0}))
    window.canvas.annotation_at(0, 0)
    assert len(calls) == 2  # the rectangle itself (not a dimension) and its one dimension


def test_a_label_is_still_clickable_after_its_shape_moves(window, qtbot) -> None:
    from caliper.contracts.commands import MoveEntities

    a = line(window, P(x=0, y=20), P(x=60, y=20))
    (dim,) = window.session.execute(
        CreateDimension(refs=(curve(a),), placement=P(x=30, y=40))
    ).created_ids
    window.canvas.annotation_at(0, 0)
    window.session.execute(MoveEntities(ids=(a,), dx=0, dy=-10))
    wx, wy = window.canvas.view.to_widget(P(x=30, y=30))
    assert window.canvas.annotation_at(wx, wy) == dim


def test_glyphs_follow_their_line_when_it_moves(window) -> None:
    from caliper.contracts.commands import MoveEntities

    a = line(window, P(x=0, y=20), P(x=60, y=21))
    constrain(window, ConstraintType.HORIZONTAL, curve(a))
    (before,) = window.canvas.constraint_glyphs
    window.session.execute(MoveEntities(ids=(a,), dx=10, dy=0))
    (after,) = window.canvas.constraint_glyphs
    dx = after.rect.center().x() - before.rect.center().x()
    assert dx == pytest.approx(10 * window.canvas.view.scale)


def test_no_glyph_is_picked_while_the_view_is_moving(window, qtbot) -> None:
    a = line(window, P(x=0, y=20), P(x=60, y=21))
    constrain(window, ConstraintType.HORIZONTAL, curve(a))
    (glyph,) = window.canvas.constraint_glyphs
    centre_point = glyph.rect.center()
    window.canvas._view_moved()  # a pan or zoom step
    assert window.canvas.annotation_at(centre_point.x(), centre_point.y()) is None
