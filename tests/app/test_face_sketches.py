"""Sketches on the part's faces in the window (ADR 0016): opened facing the face, drawn where
the face is, named for it, following it when it moves, and failing visibly when it's gone."""

import pytest

from caliper.app.main_window import MainWindow
from caliper.contracts.commands import (
    Applied,
    Command,
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
)
from caliper.contracts.document import EntityId, FaceRef, Plane, Point2
from caliper.contracts.queries import Point3
from caliper.engine import features, geometry
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.app.parts import plate


@pytest.fixture(autouse=True)
def analytic(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    monkeypatch.setattr(geometry, "default_kernel", lambda: kernel)
    features.forget()


def on_top(window: MainWindow) -> tuple[FaceRef, str]:
    """The plate, and a sketch opened on its top face."""
    _, _, extrude = plate(window)
    face = FaceRef(feature=extrude, face="end")
    window.new_sketch(face)
    assert window.sketch_open is not None
    return face, window.sketch_open


def test_a_sketch_on_a_face_opens_facing_it(window: MainWindow) -> None:
    face, sketch = on_top(window)
    backdrop = window.canvas.backdrop
    assert backdrop is not None
    assert backdrop.plane == face
    assert backdrop.facing
    assert (backdrop.frame.origin.z, backdrop.frame.x.x, backdrop.frame.y.y) == (10.0, 1.0, 1.0)
    view, w, h = window.canvas.view, window.canvas.width(), window.canvas.height()
    camera = backdrop.camera(view, w, h)
    for x, y in ((0.0, 0.0), (30.0, -12.5)):
        seen = camera.project(Point3(x=x, y=y, z=10.0), w, h)
        assert (seen.x, seen.y) == pytest.approx(view.to_widget(Point2(x=x, y=y)))
    assert window.features.items[sketch].text(1) == "end of Extrude 1  ·  editing"
    assert "end of Extrude 1" in window.sketch_title.text()


def test_drawing_in_it_lands_on_the_face_in_3d(window: MainWindow) -> None:
    _, sketch = on_top(window)
    result = window.session.execute(
        CreateCircle(center=Point2(x=30.0, y=25.0), radius=5.0, sketch=sketch)
    )
    assert isinstance(result, Applied)
    window.finish_sketch()
    window.view3d.refresh()
    drawn = [c for c in window.view3d.scene.curves if c.sketch == sketch]
    assert drawn
    assert all(p.z == pytest.approx(10.0) for c in drawn for p in c.points)


def test_the_sketch_follows_its_face_when_the_plate_gets_deeper(window: MainWindow) -> None:
    face, _ = on_top(window)
    window.session.execute(ModifyEntity(id=face.feature, changes={"depth": 15.0}))
    backdrop = window.canvas.backdrop
    assert backdrop is not None
    assert backdrop.frame.origin.z == pytest.approx(15.0)
    window.session.undo()
    assert backdrop.frame.origin.z == pytest.approx(10.0)


def test_a_face_that_isnt_there_is_refused_with_the_reason(window: MainWindow) -> None:
    _, _, extrude = plate(window)
    count = len(window.session.document.features)
    window.new_sketch(FaceRef(feature=extrude, face="side e99"))
    assert window.sketch_open is None
    assert len(window.session.document.features) == count
    assert "has no flat face" in window.statusBar().currentMessage()


def test_a_sketch_whose_face_is_gone_shows_failing_and_isnt_drawn(window: MainWindow) -> None:
    window.set_mode("3d")
    window.new_sketch(Plane.XY)
    base = window.sketch_open
    assert base is not None
    corners = [Point2(x=0.0, y=0.0), Point2(x=40.0, y=0.0), Point2(x=0.0, y=30.0)]
    lines = [
        created(window, CreateLine(start=a, end=b, sketch=base))
        for a, b in zip(corners, [*corners[1:], corners[0]], strict=True)
    ]
    window.finish_sketch()
    extrude = created(window, CreateExtrude(depth=5.0, sketch=base))
    window.new_sketch(FaceRef(feature=extrude, face=f"side {lines[1]}"))
    side = window.sketch_open
    assert side is not None
    created(
        window, CreateRectangle(corner=Point2(x=2.0, y=1.0), width=5.0, height=2.0, sketch=side)
    )
    window.finish_sketch()
    window.session.execute(DeleteEntities(ids=(lines[1],)))
    window.features.rebuild()
    assert "fails" in window.features.items[side].toolTip(0)
    window.view3d.refresh()
    assert not [c for c in window.view3d.scene.curves if c.sketch == side]
    window.edit_sketch(side)
    assert window.sketch_open is None
    assert "Can't face" in window.statusBar().currentMessage()


def created(window: MainWindow, command: Command) -> EntityId:
    result = window.session.execute(command)
    assert isinstance(result, Applied), result
    return result.created_ids[0]


def test_the_extrude_form_names_the_face(window: MainWindow) -> None:
    _, sketch = on_top(window)
    window.session.execute(
        CreateRectangle(corner=Point2(x=10.0, y=10.0), width=20.0, height=10.0, sketch=sketch)
    )
    window.start_extrude()
    texts = [label.text() for label in window.extrude_form.findChildren(type(window.sketch_title))]
    assert any("end of Extrude 1" in text for text in texts)
