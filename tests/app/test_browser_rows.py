"""The sketch browser's rows stay exactly what a fresh listing would show (Performance V2.2,
Perf-1): rows built in order and new ones placed by bisect against the browser's own keys,
with a document opened listed once, not twice.
"""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from caliper.app.main_window import MainWindow
from caliper.app.panels.browser import GROUPS, SketchBrowser
from caliper.contracts.commands import (
    CreateCircle,
    CreateConstraint,
    CreateDistanceDimension,
    CreateLine,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
)
from caliper.contracts.document import (
    ConstraintType,
    DistanceOrientation,
    Feature,
    Line,
    Point2,
    Rectangle,
    Ref,
)
from caliper.engine.commands.bus import Bus
from tests.app.conftest import make_window

Rows = list[tuple[str, bool, str, list[tuple[str, str, str, bool, str]]]]


def rows(browser: SketchBrowser) -> Rows:
    """Every group and row as the user sees it: order, text, value, italics, tooltip."""
    found: Rows = []
    for name in GROUPS:
        group = browser.groups[name]
        children = [group.child(i) for i in range(group.childCount())]
        found.append(
            (
                name,
                group.isHidden(),
                group.text(1),
                [
                    (
                        c.data(0, 0x0100),
                        c.text(0),
                        c.text(1),
                        c.font(0).italic(),
                        c.toolTip(0),
                    )
                    for c in children
                ],
            )
        )
    return found


def fresh(window: MainWindow) -> Rows:
    listing = SketchBrowser(window.session)
    try:
        return rows(listing)
    finally:
        listing.deleteLater()


def step(window: MainWindow, choice: int, pick: int) -> None:
    """One change to the sketch, chosen by `choice`, on the entity `pick` picks."""
    session = window.session
    entities = sorted(session.document.entities)
    target = entities[pick % len(entities)] if entities else None
    entity = session.document.entities.get(target) if target else None
    x = float(pick % 50)
    match choice:
        case 0:
            session.execute(CreateCircle(center=Point2(x=x, y=3.0), radius=1.0 + pick % 3))
        case 1:
            session.execute(CreateLine(start=Point2(x=x, y=0.0), end=Point2(x=x + 7, y=4.0)))
        case 2:
            session.execute(CreateRectangle(corner=Point2(x=x, y=9.0), width=5.0, height=2.0))
        case 3 if target is not None:
            session.execute(DeleteEntities(ids=(target,)))
        case 4 if isinstance(entity, Rectangle):
            session.execute(ModifyEntity(id=target, changes={"width": 6.0 + pick % 4}))
        case 5 if isinstance(entity, Line):
            session.execute(
                CreateDistanceDimension(
                    a=Ref(entity=target, feature=Feature.START),
                    b=Ref(entity=target, feature=Feature.END),
                    orientation=DistanceOrientation.ALIGNED,
                    offset=2.0,
                )
            )
        case 6 if isinstance(entity, Line):
            session.execute(
                CreateConstraint(
                    type=ConstraintType.HORIZONTAL,
                    refs=(Ref(entity=target, feature=Feature.CURVE),),
                )
            )
        case 7 if isinstance(entity, Line):
            session.execute(
                ModifyEntity(id=target, changes={"construction": not entity.construction})
            )
        case 8:
            window.undo_action.trigger()
        case 9:
            window.redo_action.trigger()
        case 10:
            with session.transaction("Two"):
                session.execute(CreateCircle(center=Point2(x=x, y=20.0), radius=2.0))
                session.execute(CreateLine(start=Point2(x=x, y=21.0), end=Point2(x=x + 1, y=25.0)))


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(steps=st.lists(st.tuples(st.integers(0, 10), st.integers(0, 10_000)), max_size=25))
def test_the_rows_after_any_changes_are_a_fresh_listing(qtbot, steps) -> None:  # type: ignore[no-untyped-def]
    window = make_window(qtbot, Bus())
    for choice, pick in steps:
        step(window, choice, pick)
        assert rows(window.browser) == fresh(window)
    window.close()


def test_a_document_opened_is_listed_once(window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    made: list[str] = []
    real = SketchBrowser._item
    monkeypatch.setattr(
        SketchBrowser, "_item", lambda self, id, q: (made.append(id), real(self, id, q))[1]
    )
    bus = Bus()
    for k in range(30):
        bus.execute(CreateCircle(center=Point2(x=float(k), y=0.0), radius=1.0))
    window.session.replace(bus, None)  # document_replaced, then active_sketch_changed
    assert len(made) == 30
    assert sorted(made) == sorted(window.browser.items)


def test_rows_are_in_natural_order_at_scale(window: MainWindow) -> None:
    bus = Bus()
    for k in range(2000):
        bus.execute(CreateCircle(center=Point2(x=float(k % 40), y=float(k // 40)), radius=1.0))
    window.session.replace(bus, None)
    geometry = window.browser.groups["Geometry"]
    ids = [geometry.child(i).data(0, 0x0100) for i in range(geometry.childCount())]
    assert ids == [f"e{n}" for n in range(1, 2001)]
    assert rows(window.browser) == fresh(window)
