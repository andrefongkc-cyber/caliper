"""A proposal that changes the part's solid is seen in 3D before it's accepted (C-16).

An extrude changes no geometry, so the canvas has nothing dashed to draw for it. The 3D view
draws the solid the proposal would leave, in the agent's colour, and says it isn't the part
yet. The document is untouched until Accept, and the part's own solid comes back on Reject.
CI's app job has no OCCT, so these tests give the engine the analytic kernel.
"""

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from caliper.app import theme
from caliper.app.agent.proposal import Plan
from caliper.app.main_window import MainWindow
from caliper.contracts.commands import (
    CreateCheck,
    CreateExtrude,
    CreateRectangle,
    CreateSketch,
    DeleteEntities,
    ModifyEntity,
)
from caliper.contracts.document import EntityId, Metric, Plane, Point2
from caliper.contracts.errors import Error
from caliper.contracts.queries import Mesh
from caliper.engine import features, geometry
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.app.parts import plate, sketch_on


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def profile(window: MainWindow) -> EntityId:
    """A 120 x 50 rectangle on Top, its sketch finished, and nothing extruded yet."""
    sketch = sketch_on(window)
    window.session.execute(CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50))
    window.finish_sketch()
    return sketch


def propose(window: MainWindow, *commands: object, label: str = "Extrude") -> None:
    window.agent.propose(Plan(label, "What the agent would do.", commands), window.session.document)  # type: ignore[arg-type]
    QApplication.processEvents()


def width(mesh: Mesh | None) -> float:
    assert mesh is not None
    return max(p.x for p in mesh.vertices) - min(p.x for p in mesh.vertices)


def tint(window: MainWindow) -> QColor:
    """The colour in the middle of the solid's top face, as the 3D view draws it now."""
    view = window.view3d
    view.fit()
    at = view.camera.project(_top_middle(view.scene.mesh), view.width(), view.height())
    return view.grab().toImage().pixelColor(round(at.x), round(at.y))


def _top_middle(mesh: Mesh | None):  # type: ignore[no-untyped-def]
    from caliper.contracts.queries import Point3

    assert mesh is not None
    top = max(p.z for p in mesh.vertices)
    xs, ys = [p.x for p in mesh.vertices], [p.y for p in mesh.vertices]
    return Point3(x=(min(xs) + max(xs)) / 2, y=(min(ys) + max(ys)) / 2, z=top)


def nearer(colour: QColor, to: QColor, than: QColor) -> bool:
    """Whether `colour` has the hue of `to` rather than of `than` (shading changes the rest)."""

    def gap(a: QColor, b: QColor) -> float:
        d = abs(a.hueF() - b.hueF())
        return min(d, 1 - d)

    return gap(colour, to) < gap(colour, than)


def test_a_proposed_extrude_is_drawn_in_3d_before_it_is_accepted(window: MainWindow) -> None:
    sketch = profile(window)
    view = window.view3d
    assert view.mesh is None
    propose(window, CreateExtrude(depth=10.0, sketch=sketch))
    assert window.views.currentWidget() is view
    assert view.scene.proposed
    assert view.scene.mesh is not None
    assert len(view.scene.mesh.triangles) == 12
    assert "Accept" in (view.note or "")
    # The part itself is as it was: no extrude, no solid.
    assert len(window.session.document.features) == 1
    assert view.mesh is None
    assert isinstance(window.session.queries.solid_properties(), Error)


def test_the_proposed_solid_is_in_the_agents_colour_and_the_parts_own_after_accept(
    window: MainWindow,
) -> None:
    sketch = profile(window)
    propose(window, CreateExtrude(depth=10.0, sketch=sketch))
    assert nearer(tint(window), to=theme.AGENT, than=theme.SOLID)
    window.proposal_card.accept_button.click()
    QApplication.processEvents()
    view = window.view3d
    assert not view.scene.proposed
    assert view.note is None
    assert view.scene.mesh is view.mesh
    assert nearer(tint(window), to=theme.SOLID, than=theme.AGENT)


def test_rejecting_puts_the_parts_own_solid_back(window: MainWindow) -> None:
    _, rectangle, _ = plate(window)
    QApplication.processEvents()
    view = window.view3d
    own = view.mesh
    propose(window, ModifyEntity(id=rectangle, changes={"width": 140.0}), label="Widen")
    view.refresh()
    assert view.scene.proposed
    assert width(view.scene.mesh) == pytest.approx(140.0)
    assert view.mesh is own  # what the part is, kept for when the proposal goes
    window.agent.reject()
    QApplication.processEvents()
    view.refresh()
    assert not view.scene.proposed
    assert view.scene.mesh is own
    assert view.note is None


def test_a_proposed_change_to_a_sketch_shows_its_solid_behind_the_canvas(
    window: MainWindow,
) -> None:
    """The canvas faces the sketch the proposal changes, over the part: the part behind it is
    the proposed one, so the dashed outline and the solid it makes agree."""
    sketch, rectangle, _ = plate(window)
    QApplication.processEvents()
    propose(window, ModifyEntity(id=rectangle, changes={"width": 140.0}), label="Widen")
    assert window.views.currentWidget() is window.canvas
    backdrop = window.canvas.backdrop
    assert backdrop is not None
    before = backdrop.shows(sketch)
    window.canvas.grab()  # paints the backdrop
    assert window.view3d.scene.proposed
    assert width(window.view3d.scene.mesh) == pytest.approx(140.0)
    window.agent.reject()
    QApplication.processEvents()
    assert backdrop.shows(sketch) != before  # the kept image is drawn again, the part's own


def test_a_proposal_that_leaves_the_solid_alone_draws_the_part_as_it_is(
    window: MainWindow,
) -> None:
    _, rectangle, _ = plate(window)
    QApplication.processEvents()
    view = window.view3d
    own = view.mesh
    check = CreateCheck(metric=Metric.BBOX_WIDTH, expected=120.0, tolerance=0.001, ids=(rectangle,))
    propose(window, check, label="Check Width")
    view.refresh()
    assert not view.scene.proposed
    assert view.scene.mesh is own
    assert view.note is None


def test_a_proposal_that_would_break_the_solid_keeps_the_part_and_says_why(
    window: MainWindow,
) -> None:
    _, rectangle, _ = plate(window)
    QApplication.processEvents()
    view = window.view3d
    own = view.mesh
    propose(window, DeleteEntities(ids=(rectangle,)), label="Delete Rectangle")
    view.refresh()
    assert not view.scene.proposed
    assert view.scene.mesh is own
    assert "Accepting" in (view.note or "")


def test_a_proposal_updated_without_a_change_to_the_part_keeps_the_sketches_drawn(
    window: MainWindow,
) -> None:
    """An agent adds to its proposal call by call. The sketches the 3D view draws are the
    document's, which a proposal doesn't change, so they aren't worked out again each time."""
    sketch = profile(window)
    view = window.view3d
    view.refresh()
    curves = view.scene.curves
    propose(window, CreateExtrude(depth=10.0, sketch=sketch))
    view.refresh()
    assert view.scene.curves is curves
    propose(window, CreateExtrude(depth=20.0, sketch=sketch))
    view.refresh()
    assert view.scene.curves is curves
    assert max(p.z for p in view.scene.mesh.vertices) == pytest.approx(20.0)  # type: ignore[union-attr]


def test_the_proposed_solid_is_back_with_its_proposal_after_a_look_at_the_2d_tab(
    window: MainWindow,
) -> None:
    sketch = profile(window)
    propose(window, CreateExtrude(depth=10.0, sketch=sketch))
    proposed = window.view3d.scene.mesh
    assert window.view3d.scene.proposed
    window.set_mode("2d")  # the proposal waits on the 3D tab (C-18)
    assert window.agent.proposal is None
    window.set_mode("3d")
    QApplication.processEvents()
    assert window.agent.proposal is not None
    assert window.view3d.scene.proposed
    assert window.view3d.scene.mesh is proposed
    assert window.view3d.mesh is None  # the part itself still has no solid


def test_a_proposal_the_part_has_moved_on_from_is_not_drawn(window: MainWindow) -> None:
    """Edit the part while a proposal waits and the proposal is out of date (Accept says so):
    what it would have made isn't shown as if it still could be."""
    _, rectangle, _ = plate(window)
    QApplication.processEvents()
    view = window.view3d
    propose(window, ModifyEntity(id=rectangle, changes={"height": 80.0}), label="Deepen")
    view.refresh()
    assert view.scene.proposed
    window.session.execute(ModifyEntity(id=rectangle, changes={"width": 140.0}))
    QApplication.processEvents()
    view.refresh()
    assert not view.scene.proposed
    assert view.scene.mesh is view.mesh
    assert width(view.mesh) == pytest.approx(140.0)
    assert view.note is None


def test_the_proposed_solid_is_worked_out_when_the_view_is_painted_not_when_proposed(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Desktop's call is answered before the 3D view builds what it proposed, and
    several changes to a proposal between two frames cost one solid, not one each."""
    from caliper.app.viewport import view3d

    sketch = profile(window)
    view = window.view3d
    QApplication.processEvents()
    built: list[object] = []
    real = view3d._mesh
    monkeypatch.setattr(view3d, "_mesh", lambda queries: (built.append(queries), real(queries))[1])
    for depth in (10.0, 20.0, 30.0):
        plan = Plan("Extrude", "Deeper.", (CreateExtrude(depth=depth, sketch=sketch),))
        window.agent.propose(plan, window.session.document)
    assert window.views.currentWidget() is view  # on screen, and still:
    assert built == []  # nothing yet: the calls are done, the frame is to come
    view.repaint()
    assert len(built) == 1
    assert max(p.z for p in view.scene.mesh.vertices) == pytest.approx(30.0)  # type: ignore[union-attr]


def test_a_proposal_that_grows_while_the_canvas_faces_its_sketch_shows_its_solid_there(
    window: MainWindow,
) -> None:
    """Claude Desktop builds a proposal call by call: the first calls draw a sketch, which the
    canvas turns to face over the part; a later one extrudes it. The part behind the sketch is
    drawn again then, with the solid the proposal would make (found in test run 006: the
    canvas kept the picture it had of the part, so the solid never showed)."""
    window.set_mode("3d")
    drawing = (
        CreateSketch(plane=Plane.XY, id="e1"),
        CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50, sketch="e1"),
    )
    propose(window, *drawing, label="Draw Plate")
    canvas, view = window.canvas, window.view3d
    assert window.views.currentWidget() is canvas  # facing the sketch the proposal makes
    canvas.grab()
    before = canvas._layer
    assert not view.scene.proposed  # no solid proposed yet
    propose(window, *drawing, CreateExtrude(depth=10.0, sketch="e1"), label="Plate")
    middle = canvas.view.to_widget(Point2(x=60, y=25))
    shown = canvas.grab().toImage()
    assert canvas._layer is not before  # the part behind the sketch was drawn again
    assert view.scene.proposed
    ratio = shown.devicePixelRatio()
    inside = shown.pixelColor(round(middle[0] * ratio), round(middle[1] * ratio))
    assert nearer(inside, to=theme.AGENT, than=theme.CANVAS)  # the proposed plate's top
