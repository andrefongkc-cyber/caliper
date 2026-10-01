"""The saved-check workflow (N10), walked through the real window: the Checks panel, Undo and
Redo, deleting what a check measures, Save, File → New, Open, and a file from before checks
were saved. Offscreen and automated: it drives the same actions a person would click, but it
is not a manual pass, and the docs say so. Step 11, an older Caliper opening a newer file, is
checked against the real older build by hand (docs/workplan/core.md, N10)."""

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from caliper.contracts.commands import CreateRectangle, DeleteEntities
from caliper.contracts.document import Point2
from caliper.engine.io import snapshot

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_2 = ROOT / "bench" / "cases" / "constrained-plate-width-120" / "start.caliper"
"""A file written before checks were stored (schema 2)."""


def test_a_check_from_adding_it_to_opening_it_again(window, tmp_path, monkeypatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: shown.append(box.text()) or 0)
    session, panel = window.session, window.checks
    (plate,) = session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50)
    ).created_ids

    # 1. Add a check, from the selection, in the Checks panel.
    session.set_selection(frozenset({plate}))
    panel.add_button.click()
    assert panel.metric_box.currentText() == f"Width of {plate}"
    panel.expected.setText("120")
    panel.confirm.click()
    assert panel.summary.text() == "1 of 1 pass"
    assert session.history[-1].label == "Create Check"

    # 2, 3. Undo takes it away, and Redo brings it back.
    window.undo_action.trigger()
    assert session.checks == ()
    assert panel.summary.text() == "Checks"
    window.redo_action.trigger()
    assert len(session.checks) == 1

    # 4, 5. Deleting what it measures leaves the check, failing: it can't be measured.
    session.execute(DeleteEntities(ids=(plate,)))
    assert len(session.checks) == 1
    assert panel.summary.text() == "0 of 1 pass"
    assert panel.list.item(0).toolTip()  # why it can't be measured

    # 6, 7. Save, and open the file again: the check is there, still failing.
    path = tmp_path / "plate.caliper"
    window._save_to(path)
    assert '"kind": "check"' in path.read_text()
    assert window.load(path)
    assert len(session.checks) == 1
    assert panel.summary.text() == "0 of 1 pass"

    # 8. File → New: no checks.
    window.new_action.trigger()
    assert session.checks == ()
    assert panel.summary.text() == "Checks"

    # 9. Open: the check comes back with the file.
    assert window.open_document(path)
    assert len(session.checks) == 1

    # 10. A file from before checks were saved opens, and takes a check from then on.
    assert snapshot.read_file(SCHEMA_2).schema_version == 2
    assert window.open_document(SCHEMA_2)
    assert session.checks == ()
    (edge,) = sorted(session.document.entities)[:1]
    session.set_selection(frozenset({edge}))
    panel.add_button.click()
    panel.confirm.click()
    assert len(session.checks) == 1
    upgraded = tmp_path / "upgraded.caliper"
    window._save_to(upgraded)
    assert snapshot.read_file(upgraded).schema_version == snapshot.SCHEMA_VERSION
    assert shown == []  # nothing went wrong on the way
