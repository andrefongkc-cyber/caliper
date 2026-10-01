"""V2's milestone, clicked and typed through the real window (F6, F8), on both kernels.

A 120 x 50 rectangle drawn on XY, extruded 10 mm: 60,000 mm³. The width typed as 140 in
Properties: 70,000. Undo: 60,000. Back to 2D to keep sketching (a dimension), then 3D again:
the same solid. Saved, reopened: the same document and solid. And the commands the window
sent, replayed headlessly, give the saved file's bytes.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.app.main_window import MainWindow
from caliper.contracts.document import Extrude, Rectangle
from caliper.contracts.errors import Error
from caliper.contracts.kernel import Kernel
from caliper.engine import features, geometry
from caliper.engine.geometry.fake_kernel import FakeKernel
from caliper.engine.io import snapshot
from caliper.engine.io.codec import encode


@pytest.fixture(params=["analytic", "occt"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    if request.param == "analytic":
        made: Kernel = FakeKernel()
    else:
        occt = pytest.importorskip(
            "caliper.engine.geometry.occt_kernel", reason="the occt extra isn't installed"
        )
        made = occt.OCCTKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: made)
    features.forget()
    return made


def type_keys(qtbot, text: str) -> None:  # type: ignore[no-untyped-def]
    for char in text:
        widget = QApplication.focusWidget()
        if char == "\t":
            qtbot.keyClick(widget, Qt.Key.Key_Tab)
        elif char == "\n":
            qtbot.keyClick(widget, Qt.Key.Key_Return)
        else:
            qtbot.keyClicks(widget, char)


def volume(window: MainWindow) -> float:
    found = window.session.queries.solid_properties()
    assert not isinstance(found, Error), found
    return found.volume


def shown_width(window: MainWindow) -> float:
    mesh = window.view3d.mesh
    assert mesh is not None
    return max(p.x for p in mesh.vertices) - min(p.x for p in mesh.vertices)


def test_the_v2_milestone_through_the_window(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
    kernel: Kernel,
    tmp_path: Path,
) -> None:
    session = window.session
    # 1, 2. The new part's sketch, on XY: a 120 x 50 rectangle, its sizes typed.
    assert window.sketch_label.text() == "Editing Sketch 1  ·  XY"
    driver.canvas.setFocus()
    driver.tool("Rectangle")
    driver.click(0, 0)
    driver.move(30, 30)
    type_keys(qtbot, "120\t50\n")
    (plate,) = session.document.entities
    assert session.document.entities[plate] == Rectangle(
        corner=session.document.entities[plate].corner,  # type: ignore[union-attr]
        width=120.0,
        height=50.0,
    )

    # 3, 4. Extrude 10 mm: the 3D view shows a 60,000 mm³ solid.
    window.extrude_action.trigger()
    window.extrude_form.depth.setText("10")
    window.extrude_form.confirm.click()
    QApplication.processEvents()
    assert window.mode == "3d"
    assert isinstance(session.document.features[1], Extrude)
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    assert window.features.volume.text() == "60,000 mm³"
    assert shown_width(window) == pytest.approx(120.0)

    # 5, 6. The width typed as 140 in Properties: 70,000 mm³.
    window.set_mode("2d")
    driver.tool("Select")
    session.set_selection(frozenset({plate}))
    width = window.properties.fields["width"]
    width.setFocus()
    width.selectAll()
    qtbot.keyClicks(width, "140")
    qtbot.keyClick(width, Qt.Key.Key_Return)
    assert volume(window) == pytest.approx(70_000.0, rel=1e-9)
    assert window.solid_label.text() == "Solid 70,000 mm³"

    # 7, 8. Undo: 60,000 mm³, and the 3D view shows it.
    window.undo_action.trigger()
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    window.set_mode("3d")
    QApplication.processEvents()
    assert shown_width(window) == pytest.approx(120.0)
    solid_document = session.document

    # Back to 2D to keep sketching: a dimension on the plate's bottom edge.
    window.set_mode("2d")
    driver.tool("Dimension")
    driver.click(60, 0)
    driver.click(60, -15)
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Return)  # its measured 120, accepted
    assert len(session.document.entities) == 2
    # And to 3D: the same solid (a dimension isn't part of the profile).
    window.set_mode("3d")
    QApplication.processEvents()
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    assert shown_width(window) == pytest.approx(120.0)
    assert session.document is not solid_document  # the dimension is there

    # 9. Saved and reopened: the same document, the same solid.
    path = tmp_path / "plate.caliper"
    assert window._save_to(path)
    saved = session.document
    window.new_action.trigger()
    assert len(session.document.features) == 1
    assert window.load(path)
    assert session.document == saved
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    window.set_mode("3d")
    QApplication.processEvents()
    assert shown_width(window) == pytest.approx(120.0)


def test_what_the_window_sent_replays_headlessly_to_the_saved_bytes(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
    kernel: Kernel,
    tmp_path: Path,
) -> None:
    driver.canvas.setFocus()
    driver.tool("Rectangle")
    driver.click(0, 0)
    driver.move(30, 30)
    type_keys(qtbot, "120\t50\n")
    window.extrude_action.trigger()
    window.extrude_form.confirm.click()  # the default depth, 10 mm
    path = tmp_path / "plate.caliper"
    assert window._save_to(path)
    script = tmp_path / "plate.script.json"
    script.write_text(
        json.dumps(
            {
                "format": "caliper.script",
                "schema_version": 1,
                "commands": encode(window.session.recorded_commands),
            }
        )
    )
    replayed = [
        subprocess.run(
            [sys.executable, "-m", "caliper.engine", "replay", str(script)], capture_output=True
        )
        for _ in range(2)
    ]
    assert replayed[0].returncode == 0, replayed[0].stderr
    assert replayed[0].stdout == path.read_bytes()
    assert replayed[1].stdout == replayed[0].stdout
    assert snapshot.load(path).features[1] == window.session.document.features[1]
