"""A change the constraints can't allow: what's in the way is shown, until the next change."""

import pytest

from caliper.contracts.commands import (
    CreateConstraint,
    CreateDimension,
    CreateLine,
    ModifyEntity,
    Rejected,
)
from caliper.contracts.document import ConstraintType, Feature, Point2, Ref

P = Point2


@pytest.fixture
def held(window) -> dict[str, str]:
    """A line fixed at its start with a driving length of 60: its end can't go anywhere new."""
    s = window.session
    (line,) = s.execute(CreateLine(start=P(x=0, y=20), end=P(x=60, y=20))).created_ids
    curve = Ref(entity=line, feature=Feature.CURVE)
    (fix,) = s.execute(
        CreateConstraint(type=ConstraintType.FIX, refs=(Ref(entity=line, feature=Feature.START),))
    ).created_ids
    (length,) = s.execute(
        CreateDimension(refs=(curve,), placement=P(x=30, y=35), value=60.0)
    ).created_ids
    return {"line": line, "fix": fix, "length": length}


def _stretch(window, held) -> object:
    return window.session.execute(ModifyEntity(id=held["line"], changes={"end": P(x=90, y=20)}))


def test_a_rejected_edit_flags_what_is_in_the_way(window, held) -> None:
    result = _stretch(window, held)
    assert isinstance(result, Rejected)
    assert window.session.flagged >= {held["fix"], held["length"]}
    assert "conflicts" in window.statusBar().currentMessage()


def test_flagged_constraints_are_drawn_in_the_error_colour(window, held) -> None:
    _stretch(window, held)
    (glyph,) = [g for g in window.canvas.constraint_glyphs if g.id == held["fix"]]
    image = window.canvas.grab().toImage()
    ratio = window.canvas.devicePixelRatioF()
    edge = glyph.rect.topLeft()
    top = [
        image.pixelColor(round((edge.x() + 0.5 + i) * ratio), round((edge.y() + 0.5) * ratio))
        for i in range(3, 12)
    ]
    # Antialiasing blends the 1 px border with the canvas, so compare hues, not exact colours.
    assert all(c.red() > c.green() + 60 and c.red() > c.blue() + 60 for c in top), top


def test_the_next_change_clears_the_flags(window, held) -> None:
    _stretch(window, held)
    window.session.execute(CreateLine(start=P(x=0, y=60), end=P(x=10, y=60)))
    assert window.session.flagged == frozenset()


def test_undo_clears_the_flags(window, held) -> None:
    _stretch(window, held)
    window.undo_action.trigger()
    assert window.session.flagged == frozenset()


def test_a_rejection_naming_nothing_flags_nothing(window) -> None:
    window.session.execute(CreateLine(start=P(x=0, y=0), end=P(x=0, y=0)))  # zero length
    assert window.session.flagged == frozenset()
