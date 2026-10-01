"""V2's milestone, clicked and typed through the real window (F6, F8), on both kernels.

A 120 x 50 rectangle drawn on XY, extruded 10 mm: 60,000 mm³. The width typed as 140 in
Properties: 70,000. Undo: 60,000. 3D to 2D by the switch to keep sketching (a dimension), then
2D to 3D: the same solid. Saved, reopened: the same document and solid. And the commands the
window sent, replayed headlessly, give the saved file's bytes. Each volume is checked in the
engine's query and as the volume inside the mesh the 3D view draws, and the panels on the way.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QToolButton, QTreeWidget, QTreeWidgetItem

from caliper.app.main_window import MainWindow
from caliper.contracts.document import FIRST_SKETCH, Extrude, Rectangle
from caliper.contracts.errors import Error
from caliper.contracts.kernel import Kernel
from caliper.contracts.queries import Mesh
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


def enclosed(mesh: Mesh) -> float:
    """The volume inside a closed mesh whose triangles face outward (the divergence theorem):
    what the 3D view draws, measured, rather than what the engine says it is."""
    v = mesh.vertices
    total = 0.0
    for a, b, c in mesh.triangles:
        p, q, r = v[a], v[b], v[c]
        total += (
            p.x * (q.y * r.z - q.z * r.y)
            - p.y * (q.x * r.z - q.z * r.x)
            + p.z * (q.x * r.y - q.y * r.x)
        )
    return total / 6


def shown(window: MainWindow) -> tuple[float, float]:
    """The width and volume of the solid the 3D view is drawing."""
    QApplication.processEvents()
    mesh = window.view3d.mesh
    assert mesh is not None
    assert window.view3d.problem is None, window.view3d.problem
    xs = [p.x for p in mesh.vertices]
    return max(xs) - min(xs), enclosed(mesh)


def switch(window: MainWindow, qtbot, mode: str) -> None:  # type: ignore[no-untyped-def]
    """Click the toolbar's 2D/3D switch."""
    (button,) = [
        b for b in window.tool_bar.findChildren(QToolButton) if b.objectName() == f"mode-{mode}"
    ]
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert window.mode == mode


def click_row(qtbot, tree: QTreeWidget, item: QTreeWidgetItem) -> None:  # type: ignore[no-untyped-def]
    rect = tree.visualItemRect(item)
    qtbot.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def test_the_v2_milestone_through_the_window(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
    kernel: Kernel,
    tmp_path: Path,
) -> None:
    """The whole of V2's milestone, end to end (F8): each step clicked or typed, each result
    checked in the engine, the panels, and the pixels' source, the mesh the 3D view draws."""
    session = window.session
    # 1. A new part edits its sketch, on XY.
    assert window.sketch_label.text() == "Editing Sketch 1  ·  XY"
    assert window.mode == "2d"

    # 2. A 120 x 50 rectangle, its sizes typed.
    driver.canvas.setFocus()
    driver.tool("Rectangle")
    driver.click(0, 0)
    driver.move(30, 30)
    type_keys(qtbot, "120\t50\n")
    (plate,) = session.document.entities
    drawn = session.document.entities[plate]
    assert isinstance(drawn, Rectangle)
    assert (drawn.width, drawn.height, drawn.sketch) == (120.0, 50.0, FIRST_SKETCH)

    # 3. Extrude 10 mm, from the toolbar's Extrude, the depth typed, Return.
    qtbot.mouseClick(
        window.tool_bar.widgetForAction(window.extrude_action), Qt.MouseButton.LeftButton
    )
    assert window.extrude_form.isVisible()
    type_keys(qtbot, "10\n")
    assert not window.extrude_form.isVisible()
    (extrude,) = [f for f in session.document.features if isinstance(f, Extrude)]
    assert (extrude.depth, extrude.sketch) == (10.0, FIRST_SKETCH)
    assert session.history[-1].label == "Extrude"

    # 4. 60,000 mm³, in the 3D view it switched to, the Part panel, and the status bar.
    assert window.mode == "3d"
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    assert window.features.volume.text() == "60,000 mm³"
    assert window.solid_label.text() == "Solid 60,000 mm³"
    width, inside = shown(window)
    assert (width, inside) == (pytest.approx(120.0), pytest.approx(60_000.0, rel=1e-6))

    # 5, 6. Still in 3D: the plate picked in the browser, its width typed as 140 in
    # Properties. 70,000 mm³, and the 3D view redraws it.
    click_row(qtbot, window.browser, window.browser.items[plate])
    assert session.selection == {plate}
    field = window.properties.fields["width"]
    field.setFocus()
    field.selectAll()
    qtbot.keyClicks(field, "140")
    qtbot.keyClick(field, Qt.Key.Key_Return)
    assert volume(window) == pytest.approx(70_000.0, rel=1e-9)
    assert window.solid_label.text() == "Solid 70,000 mm³"
    width, inside = shown(window)
    assert (width, inside) == (pytest.approx(140.0), pytest.approx(70_000.0, rel=1e-6))

    # 7. Undo, by its key: 60,000 mm³ again, on screen too.
    qtbot.keyClick(window.view3d, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    assert window.features.volume.text() == "60,000 mm³"
    width, inside = shown(window)
    assert (width, inside) == (pytest.approx(120.0), pytest.approx(60_000.0, rel=1e-6))
    before_2d = session.document

    # 8. 3D to 2D by the switch, and keep sketching: a dimension on the plate's bottom edge.
    switch(window, qtbot, "2d")
    assert session.document is before_2d  # switching changed nothing
    assert window.tool_actions["Dimension"].isEnabled()
    driver.tool("Dimension")
    driver.click(60, 0)
    driver.click(60, -15)
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Return)  # its measured 120, accepted
    assert len(session.document.entities) == 2
    assert session.history[-1].label != "Extrude"

    # 9. 2D to 3D: the same solid (a dimension isn't part of the profile).
    switch(window, qtbot, "3d")
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    width, inside = shown(window)
    assert (width, inside) == (pytest.approx(120.0), pytest.approx(60_000.0, rel=1e-6))

    # 10. Saved; then a new part, and the file reopened: the same document and solid.
    path = tmp_path / "plate.caliper"
    assert window._save_to(path)
    saved, sent = session.document, session.recorded_commands
    window.new_action.trigger()
    assert len(session.document.features) == 1
    assert not any(isinstance(f, Extrude) for f in session.document.features)
    assert window.load(path)
    assert session.document == saved
    assert session.active_sketch == FIRST_SKETCH
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    assert window.features.volume.text() == "60,000 mm³"
    rows = [window.features.tree.topLevelItem(i).text(0) for i in range(2)]
    assert rows == ["Sketch 1", "Extrude 1"]
    window.set_mode("3d")
    width, inside = shown(window)
    assert (width, inside) == (pytest.approx(120.0), pytest.approx(60_000.0, rel=1e-6))

    # 11. What the window sent, replayed headlessly, twice: the saved file's bytes each time.
    assert [c.kind for c in sent] == ["create_rectangle", "create_extrude", "create_dimension"]
    script = tmp_path / "plate.script.json"
    script.write_text(
        json.dumps({"format": "caliper.script", "schema_version": 1, "commands": encode(sent)})
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
