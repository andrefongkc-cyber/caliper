"""Degrees-of-freedom colouring on the canvas, and the sketch's state in the status bar."""

import pytest

from caliper.app import theme
from caliper.app.solve_state import describe
from caliper.contracts.commands import (
    CreateConstraint,
    CreateDimension,
    CreateLine,
    DeleteEntities,
)
from caliper.contracts.document import (
    Constraint,
    ConstraintType,
    DistanceDimension,
    DistanceOrientation,
    Document,
    Feature,
    Line,
    Point2,
    Ref,
)
from caliper.contracts.queries import ConstraintState, SolveStatus
from caliper.engine.commands.bus import Bus
from tests.app.conftest import make_window

P = Point2


def _line(window, y: float = 20.0) -> str:
    """A 60 mm line rising 1 mm, clear of the axes: (30, y + 0.5) is on it before and after."""
    (id,) = window.session.execute(CreateLine(start=P(x=0, y=y), end=P(x=60, y=y + 1))).created_ids
    return id


def _drawn_in(window, x: float, y: float) -> str:
    """Which colour the line through model (x, y) is drawn in: "constrained" or "geometry".

    Takes the brightest pixel across the line (antialiasing never fills one pixel with the
    exact pen colour) and tells the two apart by hue: the constrained colour is cyan, plain
    geometry is a near-neutral grey.
    """
    image = window.canvas.grab().toImage()
    ratio = window.canvas.devicePixelRatioF()
    wx, wy = window.canvas.view.to_widget(P(x=x, y=y))
    column = [image.pixelColor(round(wx * ratio), round(wy * ratio) + d) for d in range(-2, 3)]
    brightest = max(column, key=lambda c: c.lightness())
    assert brightest.lightness() > theme.CANVAS.lightness() + 40, "no line there"
    return "constrained" if brightest.blue() - brightest.red() > 40 else "geometry"


def _constrain(window, type: ConstraintType, *refs: Ref) -> None:
    window.session.execute(CreateConstraint(type=type, refs=refs))


def _fully_constrain(window, line: str) -> None:
    curve = Ref(entity=line, feature=Feature.CURVE)
    _constrain(window, ConstraintType.FIX, Ref(entity=line, feature=Feature.START))
    _constrain(window, ConstraintType.HORIZONTAL, curve)
    window.session.execute(CreateDimension(refs=(curve,), placement=P(x=30, y=10), value=60.0))


def test_nothing_is_said_about_an_unconstrained_sketch(window) -> None:
    _line(window)
    assert window.solve_label.isHidden()


def test_the_first_constraint_shows_the_degrees_of_freedom(window) -> None:
    line = _line(window)
    _constrain(window, ConstraintType.HORIZONTAL, Ref(entity=line, feature=Feature.CURVE))
    assert not window.solve_label.isHidden()
    assert window.solve_label.text() == "3 degrees of freedom"


def test_fully_constrained_geometry_changes_colour_and_says_so(window) -> None:
    line = _line(window)
    other = _line(window, y=40)
    assert _drawn_in(window, 30, 20.5) == "geometry"
    _fully_constrain(window, line)
    assert window.solve_label.text() == "4 degrees of freedom"  # the other line still moves
    assert _drawn_in(window, 30, 20.5) == "constrained"
    assert _drawn_in(window, 30, 40.5) == "geometry"
    window.session.execute(DeleteEntities(ids=(other,)))
    assert window.solve_label.text() == "Fully constrained"


def test_undo_takes_the_colour_back(window) -> None:
    line = _line(window)
    _fully_constrain(window, line)
    window.undo_action.trigger()
    assert _drawn_in(window, 30, 20.5) == "geometry"
    assert window.solve_label.text() == "1 degree of freedom"


def test_a_conflicting_file_is_named_in_red(qtbot) -> None:
    """Commands never leave a sketch conflicting; a file edited by hand can."""
    line = Line(start=P(x=0, y=0), end=P(x=60, y=20))
    curve = Ref(entity="e1", feature=Feature.CURVE)
    document = Document(
        entities={
            "e1": line,
            "e2": Constraint(type=ConstraintType.HORIZONTAL, refs=(curve,)),
            "e3": DistanceDimension(
                a=Ref(entity="e1", feature=Feature.START),
                b=Ref(entity="e1", feature=Feature.END),
                orientation=DistanceOrientation.ALIGNED,
                offset=-10,
                value=40.0,
            ),
        },
        next_id=4,
    )
    window = make_window(qtbot, Bus(document))
    status = window.session.queries.solve_status()
    assert status.state is ConstraintState.CONFLICTING
    assert window.solve_label.text().startswith("Conflicting: ")
    assert set(status.conflicting) <= {"e2", "e3"}


def _status(
    state: ConstraintState,
    dof: int = 0,
    conflicting: tuple[str, ...] = (),
    redundant: tuple[str, ...] = (),
) -> SolveStatus:
    return SolveStatus(
        state=state, dof=dof, entity_dof={}, conflicting=conflicting, redundant=redundant
    )


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (_status(ConstraintState.UNDER, dof=1), "1 degree of freedom"),
        (_status(ConstraintState.UNDER, dof=1200), "1,200 degrees of freedom"),
        (_status(ConstraintState.FULLY), "Fully constrained"),
        (_status(ConstraintState.OVER, redundant=("e4",)), "Over-constrained: e4 repeat others"),
        (
            _status(ConstraintState.CONFLICTING, conflicting=("e1", "e2", "e3", "e4", "e5", "e6")),
            "Conflicting: e1, e2, e3, e4 and 2 more",
        ),
    ],
)
def test_status_wording(status: SolveStatus, text: str) -> None:
    assert describe(status) == text


def test_a_driving_dimension_alone_counts_as_constraining(window) -> None:
    line = _line(window)
    curve = Ref(entity=line, feature=Feature.CURVE)
    window.session.execute(CreateDimension(refs=(curve,), placement=P(x=30, y=10)))
    assert window.solve_label.isHidden()  # a driven dimension constrains nothing
    window.session.execute(CreateDimension(refs=(curve,), placement=P(x=30, y=30), value=60.0))
    assert window.solve_label.text() == "3 degrees of freedom"
