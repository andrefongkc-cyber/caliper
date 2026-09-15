"""The Phase 0.5 milestone, end to end through real input events.

Draw a rectangle, change its width to 120, save, quit, reopen: it's still 120.
"""

from pathlib import Path

from PySide6.QtCore import Qt

from caliper.contracts.commands import CreateRectangle, ModifyEntity
from caliper.contracts.document import Point2, Rectangle
from caliper.engine.io import snapshot


def test_draw_resize_save_reopen(window, driver, bus, qtbot, new_window, tmp_path: Path) -> None:
    # Draw a 100 x 50 rectangle.
    driver.tool("Rectangle")
    driver.drag([(0, 0), (50, 25), (100, 50)])
    (rect_id,) = window.session.document.entities

    # Select it by clicking its right edge.
    driver.tool("Select")
    driver.click(100, 25)
    assert window.session.selection == {rect_id}
    assert window.properties.entity_id == rect_id

    # Type 120 into Width and press Return.
    width = window.properties.fields["width"]
    width.setFocus()
    width.selectAll()
    qtbot.keyClicks(width, "120")
    qtbot.keyClick(width, Qt.Key.Key_Return)

    assert bus.sent == [
        CreateRectangle(corner=Point2(x=0.0, y=0.0), width=100.0, height=50.0),
        ModifyEntity(id=rect_id, changes={"width": 120.0}),
    ]
    rect = window.session.document.entities[rect_id]
    assert isinstance(rect, Rectangle)
    assert (rect.corner, rect.width, rect.height) == (Point2(x=0.0, y=0.0), 120.0, 50.0)
    assert window.undo_action.text() == "Undo Change Width"
    assert window.isWindowModified()

    # Save, then quit.
    path = tmp_path / "plate.caliper"
    assert window._save_to(path)
    assert not window.isWindowModified()
    assert window.windowTitle().startswith("plate.caliper")
    saved = window.session.document
    assert window.close()

    # Reopen in a fresh window with a fresh bus.
    reopened = new_window()
    assert reopened.open_document(path)
    rect = reopened.session.document.entities[rect_id]
    assert isinstance(rect, Rectangle)
    assert rect.width == 120.0
    assert reopened.session.document == saved
    assert not reopened.isWindowModified()
    assert reopened.undo_action.text() == "Undo"  # history doesn't travel with the file
    assert path.read_text() == snapshot.dumps(saved)
