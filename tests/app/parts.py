"""Helpers for the 3D tab's tests (ADR 0015): a part sketched and extruded as the window does it.

The 3D tab is its own document, a part that starts with no sketch. A sketch is started on a
plane and edited facing it, then finished; an extrude reads it.
"""

from caliper.app.main_window import MainWindow
from caliper.contracts.commands import CreateExtrude, CreateRectangle
from caliper.contracts.document import EntityId, Plane, Point2


def sketch_on(window: MainWindow, plane: Plane = Plane.XY) -> EntityId:
    """A new sketch on `plane`, open for editing in 3D."""
    window.set_mode("3d")
    window.new_sketch(plane)
    assert window.sketch_open is not None
    return window.sketch_open


def plate(
    window: MainWindow, width: float = 120.0, height: float = 50.0, plane: Plane = Plane.XY
) -> tuple[EntityId, EntityId, EntityId]:
    """A width x height rectangle sketched on `plane`, finished, and extruded 10 mm: its
    sketch, rectangle, and extrude."""
    sketch = sketch_on(window, plane)
    (rectangle,) = window.session.execute(  # type: ignore[union-attr]
        CreateRectangle(corner=Point2(x=0, y=0), width=width, height=height)
    ).created_ids
    window.finish_sketch()
    (extrude,) = window.session.execute(  # type: ignore[union-attr]
        CreateExtrude(depth=10.0, sketch=sketch)
    ).created_ids
    return sketch, rectangle, extrude
