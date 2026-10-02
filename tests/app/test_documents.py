"""The session's two documents (ADR 0015): the 3D tab's part and the 2D tab's test sketch.

One is shown at a time. Each keeps its own file, undo history, selection, and sketch while
the other shows, and everything that reads the session reads the one shown.
"""

from pathlib import Path

from caliper.app.session import DocumentSession, Space
from caliper.contracts.commands import CreateCircle, CreateSketch
from caliper.contracts.document import Document, Plane, Point2
from caliper.engine import part


def test_the_part_starts_with_no_sketch_and_the_sketch_tab_as_v1_did() -> None:
    session = DocumentSession()
    assert session.space is Space.SKETCH
    assert session.document == Document.empty()
    session.use(Space.PART)
    assert session.space is Space.PART
    assert session.document == part.no_sketch()
    session.new()
    assert session.document == part.no_sketch()  # New in the part: planes only


def test_switching_keeps_each_documents_history_selection_and_sketch() -> None:
    session = DocumentSession()
    switched: list[Space] = []
    session.space_changed.connect(lambda: switched.append(session.space))
    (circle,) = session.execute(  # type: ignore[union-attr]
        CreateCircle(center=Point2(x=0, y=0), radius=1)
    ).created_ids
    session.set_selection(frozenset({circle}))
    sketch_tab = session.document
    session.use(Space.PART)
    assert session.history == ()
    assert session.selection == frozenset()
    assert session.active_sketch is None
    (sketch,) = session.execute(CreateSketch(plane=Plane.XZ)).created_ids  # type: ignore[union-attr]
    session.set_picked_plane(Plane.YZ)
    part_tab = session.document
    session.use(Space.SKETCH)
    assert session.document is sketch_tab
    assert [h.label for h in session.history] == ["Create Circle"]
    assert session.selection == {circle}
    assert session.picked_plane is None
    session.undo()  # the sketch tab's own undo
    assert dict(session.document.entities) == {}
    session.use(Space.PART)
    assert session.document is part_tab
    assert session.active_sketch == sketch
    assert session.picked_plane is Plane.YZ
    assert session.bus.undo_label == "Create Sketch"
    assert switched == [Space.PART, Space.SKETCH, Space.PART]


def test_each_document_has_its_own_file_and_unsaved_changes(tmp_path: Path) -> None:
    session = DocumentSession()
    session.execute(CreateCircle(center=Point2(x=0, y=0), radius=1))
    sketch_file = tmp_path / "sketch.caliper"
    session.save(sketch_file)
    session.use(Space.PART)
    assert session.path is None
    assert session.other_path == sketch_file
    assert session.unsaved() == []
    session.execute(CreateSketch(plane=Plane.XY))
    assert session.unsaved() == [(Space.PART, None)]
    session.use(Space.SKETCH)
    assert session.path == sketch_file
    assert not session.is_dirty
    assert session.unsaved() == [(Space.PART, None)]  # the part's, while the sketch shows
    session.execute(CreateCircle(center=Point2(x=5, y=0), radius=1))
    assert session.unsaved() == [(Space.SKETCH, sketch_file), (Space.PART, None)]


def test_a_picked_plane_and_a_selection_are_one_or_the_other() -> None:
    session = DocumentSession()
    session.use(Space.PART)
    (sketch,) = session.execute(CreateSketch(plane=Plane.XY)).created_ids  # type: ignore[union-attr]
    session.set_picked_plane(Plane.XZ)
    session.set_selection(frozenset({sketch}))
    assert session.picked_plane is None
    session.set_picked_plane(Plane.YZ)
    assert session.selection == frozenset()
