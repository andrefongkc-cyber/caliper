"""Sketches on a part's flat faces (ADR 0016): names, where each face is, which way it faces,
following edits, failing when the face is gone, and what may read what.

Every face is worked out from its extrude's inputs, never by a kernel, so most of this runs
with none; the solids are built by the analytic kernel, which cuts and joins exactly on
parallel planes.
"""

import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    CreateSketch,
    DeleteEntities,
    ModifyEntity,
    Rejected,
)
from caliper.contracts.document import (
    FIRST_SKETCH,
    EntityId,
    ExtrudeOperation,
    FaceRef,
    Plane,
    Point2,
    Sketch,
)
from caliper.contracts.errors import Error, ErrorCode, LoadError
from caliper.contracts.queries import Frame, Point3
from caliper.engine import faces, features, graph, part
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry.fake_kernel import FakeKernel
from caliper.engine.io import snapshot
from tests.engine.test_recompute import Counting

E0 = FIRST_SKETCH
ORIGIN = Point2(x=0.0, y=0.0)


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def refused(result: CommandResult) -> Error:
    assert isinstance(result, Rejected), result
    return result.errors[0]


def created(bus: Bus, command: Command) -> EntityId:
    return applied(bus.execute(command)).created_ids[0]


def plate(kernel: FakeKernel | None = None) -> tuple[Bus, EntityId, EntityId]:
    """The milestone plate: a 120 x 50 rectangle on Top, extruded 10 up. Its rectangle and
    extrude."""
    bus = Bus(kernel=kernel or FakeKernel())
    rectangle = created(bus, CreateRectangle(corner=ORIGIN, width=120.0, height=50.0))
    extrude = created(bus, CreateExtrude(depth=10.0))
    return bus, rectangle, extrude


def frame_of(bus: Bus, plane: Plane | FaceRef) -> Frame:
    found = bus.queries.plane_frame(plane)
    assert not isinstance(found, Error), found
    return found


def xyz(p: Point3) -> tuple[float, float, float]:
    return (p.x, p.y, p.z)


def placed(f: Frame) -> tuple[tuple[float, float, float], ...]:
    """Origin, x, y, and the normal, as tuples."""
    return (xyz(f.origin), xyz(f.x), xyz(f.y), xyz(part.normal(f)))


def approx(value: tuple[tuple[float, float, float], ...]) -> object:
    return pytest.approx([c for v in value for c in v], abs=1e-12)


def flat(value: tuple[tuple[float, float, float], ...]) -> list[float]:
    return [c for v in value for c in v]


def volume(bus: Bus) -> float:
    found = bus.queries.solid_properties()
    assert not isinstance(found, Error), found
    return found.volume


def face(extrude: EntityId, name: str) -> FaceRef:
    return FaceRef(feature=extrude, face=name)


# --- Names and axes -----------------------------------------------------------------------


def test_face_names_are_parsed_as_adr_0014_writes_them() -> None:
    assert faces.parse("start") == ("start", None, None)
    assert faces.parse("end") == ("end", None, None)
    assert faces.parse("side e3") == ("side", "e3", None)
    assert faces.parse("side e1.right") == ("side", "e1", "right")
    for wrong in ("top", "side", "side e1.middle", "Side e1", "end ", 3, None):
        assert faces.parse(wrong) is None


@pytest.mark.parametrize("plane", list(Plane))
def test_the_axes_rule_gives_each_planes_own_axes_exactly(plane: Plane) -> None:
    on = part.frame(plane)
    assert faces.canonical(part.normal(on), on.origin) == on


unit = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False)
coordinate = st.floats(min_value=-500.0, max_value=500.0, allow_nan=False)


@given(
    m=st.tuples(unit, unit, unit).filter(lambda v: math.hypot(*v) > 0.1),
    p=st.tuples(coordinate, coordinate, coordinate),
)
def test_a_faces_axes_are_level_up_the_face_and_right_handed(
    m: tuple[float, float, float], p: tuple[float, float, float]
) -> None:
    length = math.hypot(*m)
    normal = Point3(x=m[0] / length, y=m[1] / length, z=m[2] / length)
    through = Point3(x=p[0], y=p[1], z=p[2])
    f = faces.canonical(normal, through)
    dot = lambda a, b: a.x * b.x + a.y * b.y + a.z * b.z  # noqa: E731
    assert dot(f.x, f.x) == pytest.approx(1.0)
    assert dot(f.y, f.y) == pytest.approx(1.0)
    assert dot(f.x, f.y) == pytest.approx(0.0, abs=1e-12)
    assert f.x.z == 0.0  # level: the camera that faces it needs no roll
    assert f.y.z >= 0.0  # up the face
    assert xyz(part.normal(f)) == pytest.approx(xyz(normal), abs=1e-12)  # x cross y is out
    assert dot(f.origin, normal) == pytest.approx(dot(through, normal), abs=1e-9)
    nearest = dot(f.origin, normal)
    assert xyz(f.origin) == pytest.approx(
        (nearest * normal.x, nearest * normal.y, nearest * normal.z), abs=1e-9
    )


def test_equal_frames_are_one_object() -> None:
    a = faces.canonical(Point3(x=0.0, y=0.0, z=1.0), Point3(x=3.0, y=4.0, z=10.0))
    b = faces.canonical(Point3(x=0.0, y=0.0, z=2.0), Point3(x=-7.0, y=1.0, z=10.0))
    assert a is b


# --- Where each face is, and which way it faces -----------------------------------------


def test_a_plates_faces_are_its_caps_and_its_rectangles_four_sides() -> None:
    bus, rectangle, extrude = plate()
    names = bus.queries.faces(extrude)
    assert names == tuple(
        face(extrude, n)
        for n in ("start", "end", *(f"side {rectangle}.{s}" for s in faces.RECTANGLE_SIDES))
    )
    expected = {
        "end": ((0, 0, 10), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
        "start": ((0, 0, 0), (1, 0, 0), (0, -1, 0), (0, 0, -1)),
        f"side {rectangle}.bottom": ((0, 0, 0), (1, 0, 0), (0, 0, 1), (0, -1, 0)),
        f"side {rectangle}.right": ((120, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0)),
        f"side {rectangle}.top": ((0, 50, 0), (-1, 0, 0), (0, 0, 1), (0, 1, 0)),
        f"side {rectangle}.left": ((0, 0, 0), (0, -1, 0), (0, 0, 1), (-1, 0, 0)),
    }
    for name, want in expected.items():
        assert flat(placed(frame_of(bus, face(extrude, name)))) == approx(want), name


@pytest.mark.parametrize("clockwise", [False, True])
def test_a_line_profiles_sides_face_away_from_it_whichever_way_it_was_drawn(
    clockwise: bool,
) -> None:
    """A right triangle of three lines on Top: each side faces away from the inside, drawn
    counter-clockwise or clockwise."""
    bus = Bus(kernel=FakeKernel())
    corners = [Point2(x=0.0, y=0.0), Point2(x=40.0, y=0.0), Point2(x=0.0, y=30.0)]
    if clockwise:
        corners.reverse()
    lines = [
        created(bus, CreateLine(start=a, end=b))
        for a, b in zip(corners, [*corners[1:], corners[0]], strict=True)
    ]
    extrude = created(bus, CreateExtrude(depth=5.0))
    centre = Point3(x=40.0 / 3, y=10.0, z=2.5)
    for line in lines:
        f = frame_of(bus, face(extrude, f"side {line}"))
        n = part.normal(f)
        towards = (centre.x - f.origin.x, centre.y - f.origin.y, centre.z - f.origin.z)
        assert n.z == pytest.approx(0.0, abs=1e-12)
        assert towards[0] * n.x + towards[1] * n.y + towards[2] * n.z < 0  # the inside is behind


def test_a_holes_sides_face_into_the_hole_and_a_circles_side_is_curved() -> None:
    bus = Bus(kernel=FakeKernel())
    created(bus, CreateRectangle(corner=ORIGIN, width=120.0, height=50.0))
    hole = created(bus, CreateRectangle(corner=Point2(x=50.0, y=20.0), width=20.0, height=10.0))
    round_hole = created(bus, CreateCircle(center=Point2(x=20.0, y=25.0), radius=5.0))
    extrude = created(bus, CreateExtrude(depth=10.0))
    right = frame_of(bus, face(extrude, f"side {hole}.right"))
    assert flat(placed(right)) == approx(((70, 0, 0), (0, -1, 0), (0, 0, 1), (-1, 0, 0)))
    curved = bus.queries.plane_frame(face(extrude, f"side {round_hole}"))
    assert isinstance(curved, Error)
    assert curved.code is ErrorCode.FACE_NOT_PLANAR
    assert face(extrude, f"side {round_hole}") not in bus.queries.faces(extrude)  # type: ignore[operator]


def test_a_reversed_extrudes_caps_are_below_its_sketch() -> None:
    bus = Bus(kernel=FakeKernel())
    created(bus, CreateRectangle(corner=ORIGIN, width=120.0, height=50.0))
    extrude = created(bus, CreateExtrude(depth=10.0, reversed=True))
    assert flat(placed(frame_of(bus, face(extrude, "end")))) == approx(
        ((0, 0, -10), (1, 0, 0), (0, -1, 0), (0, 0, -1))
    )
    assert flat(placed(frame_of(bus, face(extrude, "start")))) == approx(
        ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    )


def test_a_cuts_faces_face_the_other_way_into_what_it_removed() -> None:
    """A pocket from the top: its floor is its `end`, facing up out of the part, and its
    walls face into the pocket."""
    bus, _, extrude = plate()
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    pocket = created(
        bus,
        CreateRectangle(corner=Point2(x=50.0, y=20.0), width=20.0, height=10.0, sketch=on_top),
    )
    cut = created(bus, CreateExtrude(depth=4.0, sketch=on_top, operation=ExtrudeOperation.REMOVE))
    assert flat(placed(frame_of(bus, face(cut, "end")))) == approx(
        ((0, 0, 6), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    )
    wall = frame_of(bus, face(cut, f"side {pocket}.right"))
    assert xyz(part.normal(wall)) == pytest.approx((-1.0, 0.0, 0.0))
    assert wall.origin.x == pytest.approx(70.0)


# --- Sketching on a face, and building from it ---------------------------------------------


def test_a_pocket_from_the_top_goes_into_the_part_and_the_command_says_so() -> None:
    bus, _, extrude = plate()
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    created(
        bus,
        CreateRectangle(corner=Point2(x=50.0, y=20.0), width=20.0, height=10.0, sketch=on_top),
    )
    made = applied(
        bus.execute(CreateExtrude(depth=4.0, sketch=on_top, operation=ExtrudeOperation.REMOVE))
    )
    assert made.command.reversed is True  # type: ignore[union-attr]
    assert volume(bus) == pytest.approx(60_000.0 - 800.0, rel=1e-12)


def test_a_boss_on_the_top_goes_up_and_an_explicit_direction_is_kept() -> None:
    bus, _, extrude = plate()
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    created(bus, CreateCircle(center=Point2(x=30.0, y=25.0), radius=5.0, sketch=on_top))
    made = applied(bus.execute(CreateExtrude(depth=6.0, sketch=on_top)))
    assert made.command.reversed is False  # type: ignore[union-attr]
    box = bus.queries.solid_properties().bounding_box  # type: ignore[union-attr]
    assert box.z_max == pytest.approx(16.0)
    assert volume(bus) == pytest.approx(60_000.0 + math.pi * 25.0 * 6.0, rel=1e-12)
    # A cut on a plane goes along it, as before ADR 0016.
    other = Bus(kernel=FakeKernel())
    created(other, CreateRectangle(corner=ORIGIN, width=10.0, height=10.0))
    created(other, CreateExtrude(depth=10.0))
    assert faces.default_reversed(other.document, E0, ExtrudeOperation.REMOVE) is False


def test_a_pocket_from_the_bottom_goes_up_into_the_part() -> None:
    bus, _, extrude = plate()
    below = created(bus, CreateSketch(plane=face(extrude, "start")))
    # On the bottom, seen from below, y runs the other way: (10, -40) is (10, 40) on Top.
    created(
        bus,
        CreateRectangle(corner=Point2(x=10.0, y=-40.0), width=20.0, height=15.0, sketch=below),
    )
    created(bus, CreateExtrude(depth=3.0, sketch=below, operation=ExtrudeOperation.REMOVE))
    assert volume(bus) == pytest.approx(60_000.0 - 900.0, rel=1e-12)
    box = bus.queries.solid_properties().bounding_box  # type: ignore[union-attr]
    assert (box.z_min, box.z_max) == pytest.approx((0.0, 10.0))


def test_a_boss_on_a_side_face_builds_outward() -> None:
    bus, rectangle, extrude = plate()
    side = created(bus, CreateSketch(plane=face(extrude, f"side {rectangle}.right")))
    created(bus, CreateRectangle(corner=Point2(x=10.0, y=2.0), width=20.0, height=6.0, sketch=side))
    created(bus, CreateExtrude(depth=5.0, sketch=side))
    assert volume(bus) == pytest.approx(60_000.0 + 600.0, rel=1e-12)
    box = bus.queries.solid_properties().bounding_box  # type: ignore[union-attr]
    assert box.x_max == pytest.approx(125.0)


def test_a_sketch_follows_its_face_when_the_depth_changes_and_undo_puts_it_back() -> None:
    bus, _, extrude = plate()
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    created(bus, CreateCircle(center=Point2(x=30.0, y=25.0), radius=5.0, sketch=on_top))
    created(bus, CreateExtrude(depth=6.0, sketch=on_top))
    applied(bus.execute(ModifyEntity(id=extrude, changes={"depth": 15.0})))
    assert frame_of(bus, face(extrude, "end")).origin.z == pytest.approx(15.0)
    box = bus.queries.solid_properties().bounding_box  # type: ignore[union-attr]
    assert box.z_max == pytest.approx(21.0)
    bus.undo()
    box = bus.queries.solid_properties().bounding_box  # type: ignore[union-attr]
    assert box.z_max == pytest.approx(16.0)


def test_a_side_follows_its_line() -> None:
    """Move the triangle's upright side 5 mm left, with the ends that join it: the sketch on
    that side moves with it."""
    bus = Bus(kernel=FakeKernel())
    corners = [Point2(x=0.0, y=0.0), Point2(x=40.0, y=0.0), Point2(x=0.0, y=30.0)]
    lines = [
        created(bus, CreateLine(start=a, end=b))
        for a, b in zip(corners, [*corners[1:], corners[0]], strict=True)
    ]
    extrude = created(bus, CreateExtrude(depth=5.0))
    created(bus, CreateSketch(plane=face(extrude, f"side {lines[2]}")))
    assert frame_of(bus, face(extrude, f"side {lines[2]}")).origin.x == pytest.approx(0.0)
    with bus.transaction("Move the side"):
        for line, changes in (
            (lines[2], {"start": Point2(x=-5.0, y=30.0), "end": Point2(x=-5.0, y=0.0)}),
            (lines[1], {"end": Point2(x=-5.0, y=30.0)}),
            (lines[0], {"start": Point2(x=-5.0, y=0.0)}),
        ):
            applied(bus.execute(ModifyEntity(id=line, changes=changes)))
    assert frame_of(bus, face(extrude, f"side {lines[2]}")).origin.x == pytest.approx(-5.0)


def test_sketches_on_faces_of_sketches_on_faces_three_deep() -> None:
    bus, _, extrude = plate()
    tops = [extrude]
    for level in range(3):
        on = created(bus, CreateSketch(plane=face(tops[-1], "end")))
        created(
            bus,
            CreateRectangle(
                corner=Point2(x=10.0 * level, y=10.0), width=20.0, height=10.0, sketch=on
            ),
        )
        tops.append(created(bus, CreateExtrude(depth=2.0, sketch=on)))
    assert frame_of(bus, face(tops[-1], "end")).origin.z == pytest.approx(16.0)
    applied(bus.execute(ModifyEntity(id=extrude, changes={"depth": 20.0})))
    assert frame_of(bus, face(tops[-1], "end")).origin.z == pytest.approx(26.0)
    assert volume(bus) == pytest.approx(120_000.0 + 3 * 400.0, rel=1e-12)


def test_a_sketch_moves_between_a_plane_and_a_face_its_drawing_unchanged() -> None:
    bus, _, extrude = plate()
    other = created(bus, CreateSketch(plane=Plane.XZ))
    circle = created(bus, CreateCircle(center=Point2(x=30.0, y=25.0), radius=5.0, sketch=other))
    drawn = bus.document.entities[circle]
    applied(bus.execute(ModifyEntity(id=other, changes={"plane": face(extrude, "end")})))
    assert bus.document.features[-1] == Sketch(id=other, plane=face(extrude, "end"))
    applied(bus.execute(ModifyEntity(id=other, changes={"plane": Plane.YZ})))
    assert bus.document.entities[circle] == drawn
    bus.undo()
    assert bus.document.features[-1] == Sketch(id=other, plane=face(extrude, "end"))


# --- Refused, and failing -----------------------------------------------------------------


def test_a_sketch_is_refused_on_a_face_that_isnt_there() -> None:
    bus, rectangle, extrude = plate()
    cases = {
        face(extrude, "side e99"): ErrorCode.FACE_NOT_FOUND,
        face(extrude, f"side {rectangle}"): ErrorCode.FACE_NOT_FOUND,  # a rectangle's side is named
        face(extrude, "top"): ErrorCode.FACE_NOT_FOUND,  # not a face's name at all
        face(EntityId("e99"), "end"): ErrorCode.ENTITY_NOT_FOUND,
        face(E0, "end"): ErrorCode.ENTITY_WRONG_KIND,  # a sketch has no faces
        face(rectangle, "end"): ErrorCode.ENTITY_WRONG_KIND,
    }
    for ref, code in cases.items():
        error = refused(bus.execute(CreateSketch(plane=ref)))
        assert error.code is code, (ref, error)
        assert (error.field or "").startswith("plane")
    error = refused(bus.execute(CreateSketch(plane=face(extrude, "side e99"))))
    assert f"side {rectangle}.right" in error.message  # it says which faces there are


def test_a_sketch_cant_sit_on_a_face_of_itself_or_of_what_is_built_from_it() -> None:
    bus, _, extrude = plate()
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    created(bus, CreateCircle(center=Point2(x=30.0, y=25.0), radius=5.0, sketch=on_top))
    boss = created(bus, CreateExtrude(depth=6.0, sketch=on_top))
    for ref in (face(boss, "end"), face(on_top, "end")):
        error = refused(bus.execute(ModifyEntity(id=on_top, changes={"plane": ref})))
        assert error.code in (ErrorCode.DEPENDENCY_CYCLE, ErrorCode.ENTITY_WRONG_KIND)
    error = refused(bus.execute(ModifyEntity(id=E0, changes={"plane": face(extrude, "end")})))
    assert error.code is ErrorCode.DEPENDENCY_CYCLE  # the plate's own sketch, under it


def test_moving_a_sketch_onto_a_face_checks_the_face_is_there() -> None:
    bus, _, extrude = plate()
    other = created(bus, CreateSketch(plane=Plane.XZ))
    error = refused(
        bus.execute(ModifyEntity(id=other, changes={"plane": face(extrude, "side e9")}))
    )
    assert error.code is ErrorCode.FACE_NOT_FOUND


def test_a_file_whose_sketch_reads_a_later_extrude_is_refused_on_load() -> None:
    bus, _, extrude = plate()
    created(bus, CreateSketch(plane=face(extrude, "end")))
    data = json.loads(snapshot.dumps(bus.document))
    features_data = data["document"]["features"]
    features_data.insert(0, features_data.pop())  # the face sketch first
    with pytest.raises(LoadError) as raised:
        snapshot.loads(json.dumps(data))
    assert ErrorCode.DEPENDENCY_CYCLE in {e.code for e in raised.value.errors}


def test_a_sketch_whose_side_is_gone_fails_and_only_what_reads_it_with_it() -> None:
    """Delete the line a side sketch sits on: the file and the part stay, the sketch fails
    with the reason, the extrude on it fails naming the sketch, and undo mends both."""
    bus = Bus(kernel=FakeKernel())
    corners = [
        Point2(x=0.0, y=0.0),
        Point2(x=40.0, y=0.0),
        Point2(x=40.0, y=30.0),
        Point2(x=0.0, y=30.0),
    ]
    lines = [
        created(bus, CreateLine(start=a, end=b))
        for a, b in zip(corners, [*corners[1:], corners[0]], strict=True)
    ]
    extrude = created(bus, CreateExtrude(depth=5.0))
    side = created(bus, CreateSketch(plane=face(extrude, f"side {lines[1]}")))
    created(bus, CreateRectangle(corner=Point2(x=5.0, y=1.0), width=10.0, height=3.0, sketch=side))
    boss = created(bus, CreateExtrude(depth=2.0, sketch=side))
    assert bus.queries.feature_error(side) is None
    applied(bus.execute(DeleteEntities(ids=(lines[1],))))
    failed = bus.queries.feature_error(side)
    assert failed is not None
    assert failed.code is ErrorCode.FACE_NOT_FOUND
    boss_error = bus.queries.feature_error(boss)
    assert boss_error is not None
    assert boss_error.code is ErrorCode.FEATURE_FAILED
    assert snapshot.loads(snapshot.dumps(bus.document)) == bus.document  # the file opens
    bus.undo()
    assert bus.queries.feature_error(side) is None
    assert volume(bus) == pytest.approx(6_000.0 + 60.0, rel=1e-12)


def test_deleting_an_extrude_deletes_the_sketches_on_its_faces_and_undo_restores_them() -> None:
    bus, _, extrude = plate()
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    circle = created(bus, CreateCircle(center=Point2(x=30.0, y=25.0), radius=5.0, sketch=on_top))
    boss = created(bus, CreateExtrude(depth=6.0, sketch=on_top))
    before = bus.document
    applied(bus.execute(DeleteEntities(ids=(extrude,))))
    ids = {f.id for f in bus.document.features}
    assert ids == {E0}
    assert circle not in bus.document.entities
    bus.undo()
    assert bus.document == before
    assert {extrude, on_top, boss} <= {f.id for f in bus.document.features}


# --- What reads what, and what's worked out again -----------------------------------------


def test_a_face_sketch_reads_its_extrude_in_the_graph() -> None:
    bus, _, extrude = plate()
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    sketch_feature = part.feature(bus.document, on_top)
    assert sketch_feature is not None
    assert graph.reads(sketch_feature) == {extrude}
    assert extrude in graph.inputs(bus.document, on_top)
    assert on_top in graph.affected(bus.document, {extrude})
    assert graph.order(bus.document) == tuple(f.id for f in bus.document.features)


def test_only_what_a_moved_face_carries_is_built_again() -> None:
    kernel = Counting()
    bus, _, extrude = plate(kernel)
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    created(bus, CreateCircle(center=Point2(x=30.0, y=25.0), radius=5.0, sketch=on_top))
    created(bus, CreateExtrude(depth=6.0, sketch=on_top))
    other = created(bus, CreateSketch(plane=Plane.XZ))
    volume(bus)
    kernel.calls.clear()
    created(bus, CreateCircle(center=Point2(x=0.0, y=0.0), radius=1.0, sketch=other))
    volume(bus)
    assert kernel.calls["extrude"] == 0  # a sketch nothing reads: nothing rebuilt
    applied(bus.execute(ModifyEntity(id=extrude, changes={"depth": 12.0})))
    volume(bus)
    assert kernel.calls["extrude"] == 2  # the plate, and the boss on its moved top
    bus.undo()
    kernel.calls.clear()
    volume(bus)
    assert kernel.calls["extrude"] == 0  # undo finds both waiting


def test_faces_are_placed_with_no_kernel() -> None:
    bus, rectangle, extrude = plate()
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    bare = Bus(bus.document, kernel=None)
    assert bare.queries.plane_frame(face(extrude, "end")) == frame_of(bus, face(extrude, "end"))
    assert bare.queries.faces(extrude) == bus.queries.faces(extrude)
    assert bare.queries.feature_error(on_top) is None
    applied(bare.execute(DeleteEntities(ids=(rectangle,))))
    assert bare.queries.feature_error(on_top) is None  # a cap is still where the depth puts it


def test_the_faces_query_refuses_what_isnt_an_extrude() -> None:
    bus, rectangle, _ = plate()
    for id, code in (
        (EntityId("e99"), ErrorCode.ENTITY_NOT_FOUND),
        (E0, ErrorCode.ENTITY_WRONG_KIND),
        (rectangle, ErrorCode.ENTITY_WRONG_KIND),
    ):
        found = bus.queries.faces(id)
        assert isinstance(found, Error)
        assert found.code is code
    wrong = bus.queries.plane_frame("up")  # type: ignore[arg-type]
    assert isinstance(wrong, Error)


def test_a_part_sketched_on_its_faces_saves_and_opens_exactly() -> None:
    bus, rectangle, extrude = plate()
    side = created(bus, CreateSketch(plane=face(extrude, f"side {rectangle}.top")))
    created(bus, CreateCircle(center=Point2(x=60.0, y=5.0), radius=2.0, sketch=side))
    text = snapshot.dumps(bus.document)
    assert '"plane": {' in text
    assert snapshot.loads(text) == bus.document
    assert snapshot.dumps(snapshot.loads(text)) == text


def test_the_solids_of_a_face_sketch_and_its_plane_agree() -> None:
    """The boss on the top face and the same boss drawn on a plane lifted to z = 10 by hand
    are one solid."""
    bus, _, extrude = plate()
    on_top = created(bus, CreateSketch(plane=face(extrude, "end")))
    created(
        bus, CreateRectangle(corner=Point2(x=10.0, y=10.0), width=20.0, height=5.0, sketch=on_top)
    )
    created(bus, CreateExtrude(depth=3.0, sketch=on_top))
    assert volume(bus) == pytest.approx(60_000.0 + 300.0, rel=1e-12)
    assert features.solids(bus.document, bus._kernel)[on_top].error is None  # type: ignore[attr-defined]
