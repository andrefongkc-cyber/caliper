"""Panels catch up with a change without redoing everything (Performance V2.2, Perf-3).

The History list adds and dims rows rather than being rebuilt per change; the Checks panel
refreshes once per change, not once more when a check changed; the Part panel skips a rebuild
that would show what it shows. Each shows exactly what it did.
"""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PySide6.QtWidgets import QApplication

from caliper.app.main_window import MainWindow
from caliper.app.panels.checks import ChecksPanel
from caliper.app.panels.history import (
    AUTHOR_ROLE,
    POSITION_ROLE,
    UNDONE_ROLE,
    WHEN_ROLE,
    HistoryList,
)
from caliper.contracts.commands import CreateCheck, CreateCircle, ModifyEntity
from caliper.contracts.document import Metric, Point2
from caliper.engine.commands.bus import Bus
from tests.app.conftest import make_window
from tests.app.parts import plate

Row = tuple[str, object, object, object, object]


def rows(history: HistoryList) -> list[Row]:
    found = []
    for i in range(history.count()):
        item = history.item(i)
        found.append(
            (
                item.text(),
                item.data(POSITION_ROLE),
                item.data(AUTHOR_ROLE),
                item.data(WHEN_ROLE),
                item.data(UNDONE_ROLE),
            )
        )
    return found


def change(window: MainWindow, choice: int, k: int) -> None:
    session = window.session
    match choice:
        case 0 | 1:
            session.execute(CreateCircle(center=Point2(x=float(k), y=0.0), radius=1.0))
        case 2:
            window.undo_action.trigger()
        case 3:
            window.redo_action.trigger()
        case 4:
            with session.transaction("Two Circles"):
                session.execute(CreateCircle(center=Point2(x=float(k), y=5.0), radius=1.0))
                session.execute(CreateCircle(center=Point2(x=float(k), y=9.0), radius=2.0))
        case 5:
            window.history.itemClicked.emit(window.history.item(window.history.count() - 1))


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(changes=st.lists(st.integers(0, 5), max_size=30))
def test_the_history_rows_after_any_changes_are_a_full_rebuild(qtbot, changes) -> None:  # type: ignore[no-untyped-def]
    window = make_window(qtbot, Bus())
    history = window.history
    for k, choice in enumerate(changes):
        change(window, choice, k)
        caught_up = rows(history)
        history.rebuild(now=history.now)
        assert caught_up == rows(history)
    window.close()


def test_a_change_adds_a_row_and_undo_dims_one_without_rebuilding(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    rebuilt: list[object] = []
    real = HistoryList.rebuild
    monkeypatch.setattr(
        HistoryList, "rebuild", lambda self, now=None: (rebuilt.append(now), real(self, now))[1]
    )
    window = make_window(qtbot, Bus())  # connected to the counting rebuild, if it is connected
    history = window.history
    rebuilt.clear()
    window.session.execute(CreateCircle(center=Point2(x=0.0, y=0.0), radius=1.0))
    window.session.execute(CreateCircle(center=Point2(x=5.0, y=0.0), radius=1.0))
    assert [history.item(i).text() for i in range(history.count())] == [
        "Create Circle",
        "Create Circle",
        "Start",
    ]
    window.undo_action.trigger()
    assert history.item(0).data(UNDONE_ROLE) is True
    assert history.item(1).data(UNDONE_ROLE) is False
    assert rebuilt == []


def test_the_ages_are_as_of_the_latest_change(window: MainWindow) -> None:
    history = window.history
    window.session.execute(CreateCircle(center=Point2(x=0.0, y=0.0), radius=1.0))
    first = history.now
    window.session.execute(CreateCircle(center=Point2(x=5.0, y=0.0), radius=1.0))
    assert history.now >= first
    assert history.item(1).data(WHEN_ROLE) == window.session.history[0].at


def test_the_checks_panel_refreshes_once_when_a_check_changes(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    refreshed: list[int] = []
    real = ChecksPanel.refresh
    monkeypatch.setattr(ChecksPanel, "refresh", lambda self: (refreshed.append(1), real(self))[1])
    window = make_window(qtbot, Bus())  # connected to the counting refresh
    (circle,) = window.session.execute(  # type: ignore[union-attr]
        CreateCircle(center=Point2(x=0.0, y=0.0), radius=4.0)
    ).created_ids
    refreshed.clear()
    window.session.execute(
        CreateCheck(metric=Metric.BBOX_WIDTH, expected=8.0, tolerance=1e-6, ids=(circle,))
    )
    QApplication.processEvents()
    assert refreshed == [1]
    assert len(window.session.checks) == 1


def test_the_part_panel_keeps_its_rows_when_nothing_it_shows_changed(window: MainWindow) -> None:
    plate(window)
    tree = window.features.tree
    before = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    window.features.rebuild()  # as a tab switch asks again
    assert [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())] == before
    window.session.execute(
        ModifyEntity(id=window.session.document.features[-1].id, changes={"depth": 12.0})
    )
    assert tree.topLevelItem(2).text(1) == "adds 12 mm"  # a real change is shown
