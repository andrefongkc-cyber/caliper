"""The 3D tab's everyday handling (the 3D UX fixes, 2026-10-13; docs/workplan/shell.md).

What the audit of the 3D tab found: a trackpad that did one thing in a sketch and another in
3D, a camera left flat on the sketch plane, an Extrude typed blind, and a part that didn't
answer the pointer. CI's app job has no OCCT, so the engine gets the analytic kernel.
"""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPointingDevice,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QWidget

from caliper.app import theme
from caliper.app.main_window import MainWindow
from caliper.app.viewport.camera3d import Camera
from caliper.app.viewport.scene3d import DIM, Scene
from caliper.app.viewport.view3d import View3D
from caliper.contracts.commands import CreateCircle, CreateExtrude, CreateRectangle
from caliper.contracts.document import EntityId, FaceRef, Plane, Point2
from caliper.contracts.queries import Point3
from caliper.engine import features, geometry
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.app.parts import plate, sketch_on

NO_MODIFIER = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
COMMAND = Qt.KeyboardModifier.ControlModifier


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def scroll(
    widget: QWidget,
    at: QPoint,
    *,
    pixels: QPoint | None = None,
    angle: int = 0,
    modifiers: Qt.KeyboardModifier = NO_MODIFIER,
) -> None:
    """A trackpad's two-finger scroll (`pixels`), or a mouse wheel's notch (`angle` alone)."""
    position = QPointF(at)
    event = QWheelEvent(
        position,
        widget.mapToGlobal(position),
        pixels or QPoint(),
        QPoint(0, angle),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase if pixels is None else Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(widget, event)


def pinch(widget: QWidget, at: QPoint, value: float) -> None:
    position = QPointF(at)
    QApplication.sendEvent(
        widget,
        QNativeGestureEvent(
            Qt.NativeGestureType.ZoomNativeGesture,
            QPointingDevice.primaryPointingDevice(),
            2,
            position,
            position,
            widget.mapToGlobal(position),
            value,
            QPointF(),
            0,
        ),
    )


# --- F1: a trackpad moves the part as it moves a sketch --------------------------------------


def test_two_fingers_pan_the_part_as_they_pan_a_sketch(window: MainWindow) -> None:
    plate(window)
    view = window.view3d
    start = view.camera
    under = start.project(start.target, view.width(), view.height())
    scroll(view, QPoint(300, 300), pixels=QPoint(12, -8))
    assert view.camera.scale == start.scale  # not a zoom, as it was before
    assert (view.camera.yaw, view.camera.pitch) == (start.yaw, start.pitch)
    moved = view.camera.project(start.target, view.width(), view.height())
    assert (moved.x - under.x, moved.y - under.y) == pytest.approx((12, -8))  # with the fingers


def test_shift_with_two_fingers_orbits(window: MainWindow) -> None:
    plate(window)
    view = window.view3d
    start = view.camera
    scroll(view, QPoint(300, 300), pixels=QPoint(30, 10), modifiers=SHIFT)
    assert view.camera == start.orbited(30, 10)
    assert view.camera.target == start.target


def test_a_pinch_and_command_scroll_zoom_about_the_pointer(window: MainWindow) -> None:
    plate(window)
    view = window.view3d
    at = QPoint(120, 90)

    def under() -> tuple[float, float]:
        """Where on the screen's plane the pixel `at` looks, in mm from the target's."""
        camera = view.camera
        right, up, _ = camera.axes()
        u = (at.x() - view.width() / 2) / camera.scale
        v = -(at.y() - view.height() / 2) / camera.scale
        t = camera.target
        return (
            u + t.x * right.x + t.y * right.y + t.z * right.z,
            v + t.x * up.x + t.y * up.y + t.z * up.z,
        )

    start, held = view.camera, under()
    pinch(view, at, 0.25)
    assert view.camera.scale == pytest.approx(start.scale * 1.25)
    assert under() == pytest.approx(held)
    pinched = view.camera
    scroll(view, at, pixels=QPoint(0, 6), angle=60, modifiers=COMMAND)
    assert view.camera.scale > pinched.scale
    assert under() == pytest.approx(held)
    assert (view.camera.yaw, view.camera.pitch) == (start.yaw, start.pitch)


def test_a_mouse_wheel_still_zooms(window: MainWindow) -> None:
    plate(window)
    view = window.view3d
    start = view.camera
    scroll(view, QPoint(300, 300), angle=120)
    assert view.camera.scale == pytest.approx(start.scale * 1.15)
    scroll(view, QPoint(300, 300), angle=-120, modifiers=SHIFT)  # a wheel never orbits
    assert view.camera.scale == pytest.approx(start.scale)
    assert (view.camera.yaw, view.camera.pitch) == (start.yaw, start.pitch)


def test_a_sketch_turned_away_from_scrolls_as_the_part_does(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    sketch, _, _ = plate(window)
    window.edit_sketch(sketch)
    canvas = window.canvas
    qtbot.mousePress(canvas, Qt.MouseButton.RightButton, pos=QPoint(300, 300))
    qtbot.mouseMove(canvas, QPoint(340, 330))
    qtbot.mouseRelease(canvas, Qt.MouseButton.RightButton, pos=QPoint(340, 330))
    backdrop = canvas.backdrop
    assert backdrop is not None
    assert backdrop.free is not None
    free = backdrop.free
    scroll(canvas, QPoint(300, 300), pixels=QPoint(12, -8))
    assert backdrop.free == free.panned(12, -8)
    scroll(canvas, QPoint(300, 300), pixels=QPoint(20, 0), modifiers=SHIFT)
    assert backdrop.free == free.panned(12, -8).orbited(20, 0)


# --- F2: finishing a sketch turns the view back ----------------------------------------------


def test_finishing_a_sketch_leaves_the_view_where_it_was(window: MainWindow) -> None:
    sketch, _, _ = plate(window)
    view = window.view3d
    view.camera = view.camera.orbited(40, -25).panned(15, 5)
    before = view.camera
    window.edit_sketch(sketch)
    window.finish_sketch()
    assert view.camera == before  # not face on to the sketch, where depth can't be seen
    window.edit_sketch(sketch)
    window.cancel_sketch()
    assert view.camera == before


def test_a_sketch_panned_while_drawing_closes_looking_at_what_was_drawn(
    window: MainWindow,
) -> None:
    sketch, _, _ = plate(window)
    view = window.view3d
    before = view.camera
    window.edit_sketch(sketch)
    window.canvas.view.pan(80, -40)
    window.canvas.view.zoom_about(1.5, 300, 300)
    backdrop = window.canvas.backdrop
    assert backdrop is not None
    faced = backdrop.camera(window.canvas.view, window.views.width(), window.views.height())
    window.finish_sketch()
    assert (view.camera.yaw, view.camera.pitch) == (before.yaw, before.pitch)  # turned back
    assert view.camera.target == faced.target  # still looking at where the drawing was
    assert view.camera.scale == faced.scale


def test_a_view_orbited_to_while_sketching_is_kept(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    sketch, _, _ = plate(window)
    window.edit_sketch(sketch)
    canvas = window.canvas
    qtbot.mousePress(canvas, Qt.MouseButton.RightButton, pos=QPoint(300, 300))
    qtbot.mouseMove(canvas, QPoint(340, 330))
    qtbot.mouseRelease(canvas, Qt.MouseButton.RightButton, pos=QPoint(340, 330))
    backdrop = canvas.backdrop
    assert backdrop is not None
    chosen = backdrop.free
    window.finish_sketch()
    assert window.view3d.camera == chosen


def test_a_new_sketch_closes_on_the_view_it_opened_from(window: MainWindow) -> None:
    window.set_mode("3d")
    before = window.view3d.camera
    sketch_on(window)
    window.finish_sketch()
    assert window.view3d.camera == before


# --- Helpers for what the 3D view shows --------------------------------------------------------


def created(window: MainWindow, command: object) -> EntityId:
    (id,) = window.session.execute(command).created_ids  # type: ignore[arg-type, union-attr]
    return id


def holed_plate_with_a_boss(window: MainWindow) -> tuple[EntityId, EntityId]:
    """A 120 x 50 x 10 plate with a round hole through it, and a round boss 8 mm tall on its
    top: the plate's extrude and the boss's."""
    sketch = sketch_on(window)
    created(window, CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    created(window, CreateCircle(center=Point2(x=30, y=25), radius=8))
    window.finish_sketch()
    base = created(window, CreateExtrude(depth=10.0, sketch=sketch))
    window.new_sketch(FaceRef(feature=base, face="end"))
    top = window.sketch_open
    assert top is not None
    created(window, CreateCircle(center=Point2(x=90, y=25), radius=10, sketch=top))
    window.finish_sketch()
    boss = created(window, CreateExtrude(depth=8.0, sketch=top))
    window.view3d.fit()
    return base, boss


def pixel_of(view: View3D, p: Point3) -> tuple[float, float]:
    at = view.camera.project(p, view.width(), view.height())
    return at.x, at.y


def colour_at(view: View3D, p: Point3) -> QColor:
    image = view.grab().toImage()
    x, y = pixel_of(view, p)
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(x * ratio), round(y * ratio))


def point_at(view: View3D, p: Point3 | None) -> None:
    """Move the pointer over `p` (or off the view), and let the view work out what's there."""
    if p is None:
        QApplication.sendEvent(view, QEvent(QEvent.Type.Leave))
        return
    position = QPointF(*pixel_of(view, p))
    QApplication.sendEvent(
        view,
        QMouseEvent(
            QEvent.Type.MouseMove,
            position,
            view.mapToGlobal(position),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            NO_MODIFIER,
        ),
    )
    QApplication.processEvents()  # one pick for the moves since the last


def bluer(colour: QColor) -> int:
    """How far towards the accent or the hover colour a shade of the solid is."""
    return colour.blue() - colour.red()


TOP_OF_PLATE = Point3(x=60.0, y=40.0, z=10.0)
OFF_THE_PART = Point3(x=-30.0, y=-30.0, z=0.0)
"""Clear of the plate, where the origin planes are all that's under the pointer."""


# --- F4: a selected extrude shows in 3D --------------------------------------------------------


def test_selecting_an_extrude_tints_what_it_made(window: MainWindow) -> None:
    _, _, extrude = plate(window)
    view = window.view3d
    plain = colour_at(view, TOP_OF_PLATE)
    window.features.items[extrude].setSelected(True)  # as a click on its row
    assert window.session.selection == {extrude}
    mesh = view.scene.mesh
    assert mesh is not None
    assert view._tinted(None) == frozenset(range(len(mesh.triangles)))  # the whole plate
    assert bluer(colour_at(view, TOP_OF_PLATE)) > bluer(plain) + 40  # in the accent, on screen
    window.session.set_selection(frozenset())
    assert colour_at(view, TOP_OF_PLATE) == plain


def test_a_selected_extrude_is_told_from_the_rest_of_the_part(window: MainWindow) -> None:
    base, boss = holed_plate_with_a_boss(window)
    view = window.view3d
    mesh = view.scene.mesh
    assert mesh is not None

    def heights(triangles: frozenset[int]) -> set[float]:
        return {round(mesh.vertices[k].z, 6) for i in triangles for k in mesh.triangles[i]}

    window.session.set_selection(frozenset({boss}))
    of_boss = view._tinted(None)
    assert heights(of_boss) == {10.0, 18.0}  # its round wall and its top, not the plate
    sideways = [i for i in of_boss if abs(view.scene.normals[i].z) < 0.01]
    assert sideways  # the wall has no name to be found by: it's found by where it is
    window.session.set_selection(frozenset({base}))
    of_base = view._tinted(None)
    assert not of_base & of_boss
    hole = [
        i
        for i in of_base
        if abs(view.scene.normals[i].z) < 0.01
        and all(abs(mesh.vertices[k].x - 30.0) <= 8.001 for k in mesh.triangles[i])
        and all(abs(mesh.vertices[k].y - 25.0) <= 8.001 for k in mesh.triangles[i])
    ]
    assert hole  # the wall of the plate's own hole
    assert of_base | of_boss == frozenset(range(len(mesh.triangles)))  # nothing left over


def test_a_round_wall_is_an_extrudes_only_within_its_depth(window: MainWindow) -> None:
    """Two extrudes of the same circle, one up from Top and one down from it: each is shown
    by its own stretch of the one round wall."""
    up_sketch = sketch_on(window)
    created(window, CreateCircle(center=Point2(x=0, y=0), radius=10))
    window.finish_sketch()
    up = created(window, CreateExtrude(depth=10.0, sketch=up_sketch))
    down_sketch = sketch_on(window)
    created(window, CreateCircle(center=Point2(x=0, y=0), radius=10, sketch=down_sketch))
    window.finish_sketch()
    down = created(window, CreateExtrude(depth=5.0, sketch=down_sketch, reversed=True))
    view = window.view3d
    view.fit()
    mesh = view.scene.mesh
    assert mesh is not None
    walls = {i for i, n in enumerate(view.scene.normals) if abs(n.z) < 0.01}

    def reach(triangles: frozenset[int]) -> tuple[float, float]:
        z = [mesh.vertices[k].z for i in triangles & walls for k in mesh.triangles[i]]
        return (round(min(z), 6), round(max(z), 6))

    window.session.set_selection(frozenset({down}))
    assert reach(view._tinted(None)) == (-5.0, 0.0)
    window.session.set_selection(frozenset({up}))
    assert reach(view._tinted(None)) == (0.0, 10.0)


def test_a_proposed_solid_is_never_tinted_as_the_selection(window: MainWindow) -> None:
    _, _, extrude = plate(window)
    view = window.view3d
    window.session.set_selection(frozenset({extrude}))
    view.scene.proposed = True
    assert view._tinted(None) == frozenset()


# --- F5: what's under the pointer shows before the click ---------------------------------------


def test_the_face_under_the_pointer_is_shown_and_nothing_is_picked(window: MainWindow) -> None:
    _, _, extrude = plate(window)
    view = window.view3d
    plain = colour_at(view, TOP_OF_PLATE)
    point_at(view, TOP_OF_PLATE)
    assert view.hover == FaceRef(feature=extrude, face="end")
    mesh = view.scene.mesh
    assert mesh is not None
    top = {i for i, n in enumerate(view.scene.normals) if n.z > 0.99}
    assert view._lit == top  # the face, not the triangle under the pointer
    hovered = colour_at(view, TOP_OF_PLATE)
    assert hovered != plain
    assert bluer(hovered) > bluer(plain)
    assert window.session.picked_plane is None  # looking isn't picking
    assert window.session.selection == frozenset()
    point_at(view, None)
    assert view.hover is None
    assert colour_at(view, TOP_OF_PLATE) == plain


def test_a_plane_and_a_sketch_under_the_pointer_are_shown(window: MainWindow) -> None:
    sketch, _, _ = plate(window, plane=Plane.XZ)  # stands on Front: its sketch is its near side
    view = window.view3d
    point_at(view, OFF_THE_PART)
    assert isinstance(view.hover, Plane)
    window.session.execute(CreateRectangle(corner=Point2(x=-40, y=-60), width=20, height=10))
    edge = Point3(x=-30.0, y=0.0, z=-60.0)  # on the new rectangle's bottom edge
    assert bluer(colour_at(view, edge)) < 20  # a sketch's own ink
    point_at(view, edge)
    assert view.hover == sketch
    assert bluer(colour_at(view, edge)) > 40  # in the hover colour
    point_at(view, OFF_THE_PART)
    assert isinstance(view.hover, Plane)


def test_a_press_clears_the_hover_and_a_drag_never_sets_one(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    plate(window)
    view = window.view3d
    point_at(view, TOP_OF_PLATE)
    assert view.hover is not None
    at = QPoint(*(round(v) for v in pixel_of(view, TOP_OF_PLATE)))
    qtbot.mousePress(view, Qt.MouseButton.LeftButton, pos=at)
    assert view.hover is None
    qtbot.mouseMove(view, at + QPoint(30, 10))
    QApplication.processEvents()
    assert view.hover is None  # orbiting: nothing is about to be picked
    qtbot.mouseRelease(view, Qt.MouseButton.LeftButton, pos=at + QPoint(30, 10))


def test_a_row_of_the_part_panel_under_the_pointer_shows_in_3d(window: MainWindow) -> None:
    sketch, _, extrude = plate(window)
    view, tree = window.view3d, window.features
    tree.tree.itemEntered.emit(tree.planes[Plane.YZ], 0)
    assert view.hover is Plane.YZ
    tree.tree.itemEntered.emit(tree.items[sketch], 0)
    assert view.hover == sketch
    tree.tree.itemEntered.emit(tree.items[extrude], 0)
    mesh = view.scene.mesh
    assert mesh is not None
    assert view._lit == frozenset(range(len(mesh.triangles)))  # what the extrude made
    QApplication.sendEvent(tree.tree.viewport(), QEvent(QEvent.Type.Leave))
    assert view.hover is None
    assert view._lit == frozenset()
    assert window.session.selection == frozenset()


# --- F6: the origin planes step back once the part has a feature -------------------------------


def test_planes_are_filled_on_a_new_part_and_outlines_once_it_has_a_feature(
    window: MainWindow,
) -> None:
    window.set_mode("3d")
    view = window.view3d
    inside = Point3(x=-30.0, y=-20.0, z=0.0)  # inside the planes, off the plate to come
    assert not view.scene.quiet
    assert colour_at(view, inside) != theme.CANVAS  # filled, to be found and picked
    plate(window)
    assert view.scene.quiet
    assert colour_at(view, inside) == theme.CANVAS  # an outline: the part has the view
    assert isinstance(view.pick(*pixel_of(view, inside)), Plane)  # and still there to pick
    point_at(view, inside)
    assert colour_at(view, inside) != theme.CANVAS  # under the pointer: in full again
    point_at(view, None)
    window.session.set_picked_plane(Plane.XY)
    assert colour_at(view, inside) != theme.CANVAS  # picked: in full


def test_hidden_planes_are_not_drawn_or_picked_until_sketch_asks_for_one(
    window: MainWindow,
) -> None:
    window.set_mode("3d")
    view = window.view3d
    inside = Point3(x=-30.0, y=-20.0, z=0.0)
    assert window.planes_action in window.menus[2].actions()  # View
    window.planes_action.setChecked(False)
    assert colour_at(view, inside) == theme.CANVAS
    assert view.pick(*pixel_of(view, inside)) is None
    window.sketch_action.trigger()  # nothing picked to sketch on: the planes are offered
    assert colour_at(view, inside) != theme.CANVAS
    assert isinstance(view.pick(*pixel_of(view, inside)), Plane)
    view._choose(Plane.XZ)  # one is picked: the rest go back to hidden
    assert not view.offering
    assert view.pick(*pixel_of(view, inside)) is None
    window.planes_action.setChecked(True)
    assert isinstance(view.pick(*pixel_of(view, inside)), Plane)


def test_show_planes_is_remembered_between_launches(window: MainWindow, new_window) -> None:  # type: ignore[no-untyped-def]
    window.planes_action.setChecked(False)
    again = new_window()
    assert not again.planes_action.isChecked()
    assert not again.view3d.show_planes


# --- F7: the solid steps back behind a sketch being drawn on it ---------------------------------


def test_a_dimmed_solid_keeps_a_third_of_its_colour() -> None:
    """Looking straight down on a plate's top, lit head on: the solid's own colour, or with
    `dim`, a third of it over the canvas's, so a sketch's lines read against it."""
    document, mesh = _plate_mesh()
    scene = Scene.of(document, mesh)
    top_down = Camera(target=Point3(x=5.0, y=5.0, z=1.0), yaw=-90.0, pitch=89.99, scale=10.0)

    def middle(**options: object) -> QColor:
        image = QImage(200, 200, QImage.Format.Format_RGB32)
        image.fill(theme.CANVAS)
        painter = QPainter(image)
        scene.paint(painter, top_down, 200, 200, **options)  # type: ignore[arg-type]
        painter.end()
        return image.pixelColor(110, 90)  # on the top, off the origin's mark and the planes

    full, dimmed = middle(), middle(dim=True)
    for channel in ("red", "green", "blue"):
        solid, ground = getattr(theme.SOLID, channel)(), getattr(theme.CANVAS, channel)()
        assert getattr(full, channel)() == pytest.approx(solid, abs=2)
        expected = ground + DIM * (solid - ground)
        assert getattr(dimmed, channel)() == pytest.approx(expected, abs=2)


def test_the_part_behind_an_open_sketch_is_drawn_dimmed(window: MainWindow) -> None:
    sketch, _, _ = plate(window)
    window.edit_sketch(sketch)
    backdrop = window.canvas.backdrop
    assert backdrop is not None
    asked: list[dict[str, object]] = []
    real = backdrop.paint_scene
    backdrop.paint_scene = lambda *a, **k: (asked.append(k), real(*a, **k))[1]
    backdrop._image = None
    window.canvas.grab()
    assert asked
    assert all(options.get("dim") is True for options in asked)
    asked.clear()
    window.finish_sketch()
    window.view3d.grab()
    assert asked == []  # the 3D view itself draws the part in full


def _plate_mesh():  # type: ignore[no-untyped-def]
    """A 10 x 10 x 2 plate's document and mesh, from the engine, with no window."""
    from caliper.contracts.commands import CreateSketch
    from caliper.engine import part
    from caliper.engine.commands.bus import Bus

    bus = Bus(part.no_sketch())
    (sketch,) = bus.execute(CreateSketch(plane=Plane.XY)).created_ids  # type: ignore[union-attr]
    bus.execute(CreateRectangle(corner=Point2(x=0, y=0), width=10, height=10, sketch=sketch))
    bus.execute(CreateExtrude(depth=2.0, sketch=sketch))
    mesh = bus.queries.mesh()
    return bus.document, mesh
