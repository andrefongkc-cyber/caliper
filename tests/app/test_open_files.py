"""Opening files: File → Open and Open Recent. Settings are per test (conftest)."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from caliper.app.main_window import RECENT_FILES
from caliper.contracts.commands import CreateCircle
from caliper.contracts.document import Point2
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot


def drawing(path: Path, radius: float = 5) -> Path:
    bus = Bus()
    bus.execute(CreateCircle(center=Point2(x=0, y=0), radius=radius))
    snapshot.save(bus.document, path)
    return path


def entries(window) -> list[str]:
    window._fill_recent()
    return [a.text() for a in window.recent_menu.actions() if not a.isSeparator()]


# --- File → Open -------------------------------------------------------------------------


def test_file_open_opens_the_chosen_file(window, tmp_path: Path, monkeypatch) -> None:
    path = drawing(tmp_path / "part.caliper")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(path), ""))
    window.open_action.trigger()  # the menu's way in: it once passed False as the path
    assert window.session.path == path
    assert len(window.session.document.entities) == 1


def test_cancelling_file_open_changes_nothing(window, monkeypatch) -> None:
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: ("", ""))
    window.open_action.trigger()
    assert window.session.path is None


# --- Open Recent -------------------------------------------------------------------------


def test_opened_and_saved_files_are_listed_newest_first(window, tmp_path: Path) -> None:
    first = drawing(tmp_path / "first.caliper")
    assert entries(window) == ["Clear Menu"]
    assert not window.recent_menu.actions()[-1].isEnabled()
    assert window.open_document(first)
    saved = tmp_path / "second.caliper"
    assert window._save_to(saved)
    assert window.recent_files() == [saved, first]
    assert entries(window) == ["second.caliper", "first.caliper", "Clear Menu"]
    assert window.open_document(first)  # again: back to the top, not listed twice
    assert window.recent_files() == [first, saved]


def test_open_recent_keeps_the_last_ten(window, tmp_path: Path) -> None:
    paths = [drawing(tmp_path / f"part{i}.caliper") for i in range(RECENT_FILES + 2)]
    for path in paths:
        assert window.open_document(path)
    assert window.recent_files() == paths[::-1][:RECENT_FILES]


def test_choosing_a_recent_file_opens_it(window, tmp_path: Path) -> None:
    path = drawing(tmp_path / "bearing.caliper", radius=8)
    assert window.open_document(path)
    window.session.new()
    window._fill_recent()
    window.recent_menu.actions()[0].trigger()
    assert window.session.path == path
    assert window.recent_menu.actions()[0].toolTip() == str(path)


def test_files_with_the_same_name_show_their_folder(window, tmp_path: Path) -> None:
    for folder in ("001-ball-bearing", "002-plate"):
        (tmp_path / folder).mkdir()
        assert window.open_document(drawing(tmp_path / folder / "test.caliper"))
    assert entries(window) == [
        "test.caliper — 002-plate",
        "test.caliper — 001-ball-bearing",
        "Clear Menu",
    ]


def test_a_recent_file_that_has_gone_is_dropped(window, tmp_path: Path) -> None:
    gone = drawing(tmp_path / "gone.caliper")
    kept = drawing(tmp_path / "kept.caliper")
    assert window.open_document(gone)
    assert window.open_document(kept)
    gone.unlink()
    assert not window.open_recent(gone)
    assert window.recent_files() == [kept]
    assert window.statusBar().currentMessage() == f"gone.caliper isn't in {tmp_path} any more"


def test_clear_menu_empties_it(window, tmp_path: Path) -> None:
    assert window.open_document(drawing(tmp_path / "part.caliper"))
    window._fill_recent()
    window.recent_menu.actions()[-1].trigger()
    assert window.recent_files() == []
    assert entries(window) == ["Clear Menu"]


def test_every_window_shares_the_list(window, new_window, tmp_path: Path) -> None:
    path = drawing(tmp_path / "part.caliper")
    assert window.open_document(path)
    assert entries(new_window()) == ["part.caliper", "Clear Menu"]
