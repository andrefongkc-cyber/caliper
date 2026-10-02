"""V2's milestone, clicked and typed through the real window, on both kernels (F8, ADR 0015).

The 3D tab, the part: the Top plane picked, Sketch, a 120 x 50 rectangle typed, Finish (✓).
Extrude 10 mm: 60,000 mm³. The width typed as 140 in Properties: 70,000. Undo: 60,000. The
sketch edited again in 3D (a dimension), orbited away and faced again (N), finished: the same
solid. The 2D tab, a sketch to test on, edited meanwhile without touching the part. Each tab
saved to its own file; the part reopened: the same document and solid. And the commands the
window sent, replayed headlessly from a part with no sketch, give the saved file's bytes.

Each volume is checked in the engine's query and as the volume inside the mesh the 3D view
draws, and in the panels on the way.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QToolButton, QTreeWidget, QTreeWidgetItem

from caliper.app.main_window import MainWindow
from caliper.contracts.commands import CreateCircle
from caliper.contracts.document import Extrude, Plane, Point2, Rectangle, Sketch
from caliper.contracts.errors import Error
from caliper.contracts.kernel import Kernel
from caliper.contracts.queries import Mesh
from caliper.engine import features, geometry, part
from caliper.engine.geometry.fake_kernel import FakeKernel
from caliper.engine.io import script, snapshot


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
    assert window.views.currentWidget() is window.view3d
    window.view3d.refresh()
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
    QApplication.processEvents()
    rect = tree.visualItemRect(item)
    qtbot.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def toolbar(window: MainWindow, qtbot, name: str) -> None:  # type: ignore[no-untyped-def]
    (button,) = [b for b in window.tool_bar.findChildren(QToolButton) if b.objectName() == name]
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def sketch_a_plate(window: MainWindow, driver, qtbot) -> None:  # type: ignore[no-untyped-def]
    """In the 3D tab: Top picked in the Part panel, Sketch, 120 x 50 typed, Finish."""
    click_row(qtbot, window.features.tree, window.features.planes[Plane.XY])
    toolbar(window, qtbot, "sketch")
    assert window.sketch_title.text() == "Sketch 1  ·  Top"
    driver.canvas.setFocus()
    driver.tool("Rectangle")
    driver.click(0, 0)
    driver.move(30, 30)
    type_keys(qtbot, "120\t50\n")
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)


def replay(path: Path) -> list[subprocess.CompletedProcess[bytes]]:
    return [
        subprocess.run(
            [sys.executable, "-m", "caliper.engine", "replay", str(path)], capture_output=True
        )
        for _ in range(2)
    ]


def test_the_v2_milestone_through_the_window(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
    kernel: Kernel,
    tmp_path: Path,
) -> None:
    """The whole of V2's milestone, end to end: each step clicked or typed, each result
    checked in the engine, the panels, and the mesh the 3D view draws."""
    session = window.session
    # 1. The 3D tab: a part with its planes and no sketch.
    switch(window, qtbot, "3d")
    assert session.document == part.no_sketch()

    # 2. Top picked, Sketch: a 120 x 50 rectangle, its sizes typed; Finish.
    sketch_a_plate(window, driver, qtbot)
    assert window.sketch_open is None
    (sketch,) = [f.id for f in session.document.features if isinstance(f, Sketch)]
    (plate,) = session.document.entities
    drawn = session.document.entities[plate]
    assert isinstance(drawn, Rectangle)
    assert (drawn.width, drawn.height, drawn.sketch) == (120.0, 50.0, sketch)

    # 3. Extrude 10 mm, from the toolbar, the depth typed, Return.
    toolbar(window, qtbot, "extrude")
    assert window.extrude_form.isVisible()
    window.extrude_form.depth.selectAll()
    type_keys(qtbot, "10\n")
    assert not window.extrude_form.isVisible()
    (extrude,) = [f for f in session.document.features if isinstance(f, Extrude)]
    assert (extrude.depth, extrude.sketch) == (10.0, sketch)
    assert session.history[-1].label == "Extrude"

    # 4. 60,000 mm³, in the 3D view, the Part panel, and the status bar.
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    assert window.features.volume.text() == "60,000 mm³"
    assert window.solid_label.text() == "Solid 60,000 mm³"
    width, inside = shown(window)
    assert (width, inside) == (pytest.approx(120.0), pytest.approx(60_000.0, rel=1e-6))

    # 5, 6. The plate picked in the sketch browser, its width typed as 140 in Properties:
    # 70,000 mm³, and the 3D view redraws it.
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

    # 8. The sketch edited again in 3D: a dimension on the plate's bottom edge; a right drag
    # orbits away, N faces the sketch again; Finish. The same solid.
    click_row(qtbot, window.features.tree, window.features.items[sketch])
    toolbar(window, qtbot, "sketch")
    assert window.sketch_open == sketch
    driver.tool("Dimension")
    driver.click(60, 0)
    driver.click(60, -15)
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Return)  # its measured 120, accepted
    assert len(session.document.entities) == 2
    canvas = window.canvas
    qtbot.mousePress(canvas, Qt.MouseButton.RightButton, pos=QPoint(300, 300))
    qtbot.mouseMove(canvas, QPoint(360, 260))
    qtbot.mouseRelease(canvas, Qt.MouseButton.RightButton, pos=QPoint(360, 260))
    assert canvas.backdrop is not None
    assert not canvas.backdrop.facing
    qtbot.keyClick(canvas, Qt.Key.Key_N)
    assert canvas.backdrop.facing
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    width, inside = shown(window)
    assert (width, inside) == (pytest.approx(120.0), pytest.approx(60_000.0, rel=1e-6))
    part_document = session.document

    # 9. The 2D tab, a sketch to test on: edited, and the part doesn't change. Back to 3D: the
    # same part, the very same document, the same solid.
    switch(window, qtbot, "2d")
    assert plate not in session.document.entities
    session.execute(CreateCircle(center=Point2(x=0, y=0), radius=5))
    sketch_path = tmp_path / "test-sketch.caliper"
    assert window._save_to(sketch_path)  # Save in 2D saves the 2D tab's document
    switch(window, qtbot, "3d")
    assert session.document is part_document
    width, inside = shown(window)
    assert (width, inside) == (pytest.approx(120.0), pytest.approx(60_000.0, rel=1e-6))

    # 10. Saved; then a new part, and the file reopened: the same document and solid.
    path = tmp_path / "plate.caliper"
    assert window._save_to(path)
    assert snapshot.load(sketch_path) != snapshot.load(path)  # each tab, its own file
    saved, sent = session.document, session.recorded_commands
    window.new_action.trigger()
    assert session.document == part.no_sketch()
    assert window.load(path)
    assert session.document == saved
    assert session.active_sketch == sketch
    assert volume(window) == pytest.approx(60_000.0, rel=1e-9)
    assert window.features.volume.text() == "60,000 mm³"
    width, inside = shown(window)
    assert (width, inside) == (pytest.approx(120.0), pytest.approx(60_000.0, rel=1e-6))

    # 11. What the window sent, replayed headlessly from a part with no sketch, twice: the
    # saved file's bytes each time.
    assert [c.kind for c in sent] == [
        "create_sketch",
        "create_rectangle",
        "create_extrude",
        "create_dimension",
    ]
    written = tmp_path / "plate.script.json"
    written.write_text(script.dumps(sent, empty=True))
    first, second = replay(written)
    assert first.returncode == 0, first.stderr
    assert first.stdout == path.read_bytes()
    assert second.stdout == first.stdout


def test_what_the_window_sent_replays_headlessly_to_the_saved_bytes(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
    kernel: Kernel,
    tmp_path: Path,
) -> None:
    switch(window, qtbot, "3d")
    sketch_a_plate(window, driver, qtbot)
    window.extrude_action.trigger()
    window.extrude_form.confirm.click()  # the default depth, 10 mm
    path = tmp_path / "plate.caliper"
    assert window._save_to(path)
    written = tmp_path / "plate.script.json"
    written.write_text(script.dumps(window.session.recorded_commands, empty=True))
    first, second = replay(written)
    assert first.returncode == 0, first.stderr
    assert first.stdout == path.read_bytes()
    assert second.stdout == first.stdout
    assert snapshot.load(path).features[1] == window.session.document.features[1]
