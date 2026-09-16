"""File menu: new, open, save, save as, unsaved changes, and load errors."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from caliper.app import main_window
from caliper.contracts.commands import CreateCircle
from caliper.contracts.document import Point2


@pytest.fixture
def dialogs(monkeypatch: pytest.MonkeyPatch) -> list[QMessageBox]:
    """Record message boxes instead of blocking on them."""
    shown: list[QMessageBox] = []

    def fake_exec(box: QMessageBox) -> int:
        shown.append(box)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return shown


def add_circle(window) -> None:
    window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))


def test_dirty_state_follows_undo_back_to_the_saved_document(window, tmp_path: Path) -> None:
    assert not window.isWindowModified()
    add_circle(window)
    assert window.isWindowModified()
    window.undo_action.trigger()
    assert not window.isWindowModified()
    window.redo_action.trigger()
    window._save_to(tmp_path / "a.caliper")
    assert not window.isWindowModified()
    window.undo_action.trigger()
    assert window.isWindowModified()


def test_save_as_adds_the_suffix(window, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    add_circle(window)
    chosen = str(tmp_path / "bracket")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (chosen, ""))
    assert window.save_action.trigger() is None
    assert window.session.path == tmp_path / "bracket.caliper"
    assert (tmp_path / "bracket.caliper").exists()


def test_cancelled_save_as_changes_nothing(window, monkeypatch: pytest.MonkeyPatch) -> None:
    add_circle(window)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert not window.save_document_as()
    assert window.session.path is None
    assert window.isWindowModified()


def test_new_clears_the_document_and_history(window) -> None:
    add_circle(window)
    window.new_action.trigger()
    assert window.session.document.entities == {}
    assert not window.undo_action.isEnabled()
    assert window.windowTitle().startswith("Untitled")


def test_unsaved_changes_block_new_when_the_user_cancels(window) -> None:
    add_circle(window)
    window.confirm_discard = lambda: False
    window.new_action.trigger()
    assert len(window.session.document.entities) == 1


def test_confirm_discard_offers_save_discard_cancel(window, monkeypatch) -> None:
    del window.confirm_discard  # use the real prompt
    add_circle(window)
    answers = iter([QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard])
    monkeypatch.setattr(QMessageBox, "exec", lambda box: next(answers))
    assert not main_window.MainWindow.confirm_discard(window)
    assert main_window.MainWindow.confirm_discard(window)
    window.confirm_discard = lambda: True  # so closing the window at teardown doesn't prompt


def test_a_corrupt_file_shows_each_validation_error(window, dialogs, tmp_path: Path) -> None:
    bad = tmp_path / "bad.caliper"
    bad.write_text(
        '{"document": {"entities": {"e1": {"kind": "circle", "center": {"x": 0, "y": 0},'
        ' "radius": -1}}, "next_id": 2}, "format": "caliper.document", "schema_version": 1,'
        ' "units": {"angle": "deg", "length": "mm"}}\n'
    )
    add_circle(window)
    before = window.session.document
    assert not window.open_document(bad)
    (box,) = dialogs
    assert box.text() == "Couldn't open bad.caliper"
    assert "radius" in box.detailedText()
    assert window.session.document == before  # a failed open leaves the document alone


def test_a_newer_schema_says_to_update(window, dialogs, tmp_path: Path) -> None:
    future = tmp_path / "future.caliper"
    future.write_text(
        '{"document": {"entities": {}, "next_id": 1}, "format": "caliper.document",'
        ' "schema_version": 99, "units": {"angle": "deg", "length": "mm"}}\n'
    )
    assert not window.open_document(future)
    assert "update Caliper" in dialogs[0].informativeText()


def test_a_missing_file_is_reported(window, dialogs, tmp_path: Path) -> None:
    assert not window.open_document(tmp_path / "nope.caliper")
    assert dialogs[0].text() == "Couldn't open nope.caliper"


def test_history_is_left_out_of_saved_files_by_default(window, tmp_path: Path) -> None:
    add_circle(window)
    path = tmp_path / "plain.caliper"
    window._save_to(path)
    assert "history" not in path.read_text()
    assert not window.history_action.isChecked()


def test_turning_history_on_records_how_the_drawing_was_built(window, tmp_path: Path) -> None:
    from caliper.engine.io import snapshot

    add_circle(window)
    window.session.execute(CreateCircle(center=Point2(x=50, y=0), radius=8))
    window.history_action.trigger()  # checkable: one trigger turns it on
    assert window.history_action.isChecked()
    path = tmp_path / "with-history.caliper"
    window._save_to(path)
    opened = snapshot.read_file(path)
    assert opened.history is not None
    assert [c.kind for c in opened.history] == ["create_circle", "create_circle"]
    assert opened.history[1].radius == 8.0  # the resolved command, with its id filled in
    assert opened.history[1].id is not None


def test_undone_steps_are_not_written(window, tmp_path: Path) -> None:
    from caliper.engine.io import snapshot

    add_circle(window)
    window.session.execute(CreateCircle(center=Point2(x=50, y=0), radius=8))
    window.undo_action.trigger()
    window.history_action.trigger()  # checkable: one trigger turns it on
    assert window.history_action.isChecked()
    path = tmp_path / "undone.caliper"
    window._save_to(path)
    assert len(snapshot.read_file(path).history or ()) == 1


def test_opening_a_file_with_history_says_so(window, tmp_path: Path) -> None:
    add_circle(window)
    window.history_action.trigger()  # checkable: one trigger turns it on
    assert window.history_action.isChecked()
    path = tmp_path / "recorded.caliper"
    window._save_to(path)
    window.session.new()
    assert window.open_document(path)
    assert window.statusBar().currentMessage() == "Opened recorded.caliper · 1 recorded step"
