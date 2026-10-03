"""Picking a flat face of the part (ADR 0016, `Queries.face_at`): a point on the solid's
surface and the way it faces give the face's name, the latest feature first, with no kernel.
"""

import math

import pytest

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateCircle,
    CreateExtrude,
    CreateLine,
    CreateRectangle,
    CreateSketch,
)
from caliper.contracts.document import EntityId, ExtrudeOperation, FaceRef, Point2
from caliper.contracts.errors import Error
from caliper.contracts.queries import Frame, Point3
from caliper.engine import part
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry.fake_kernel import FakeKernel

ORIGIN = Point2(x=0.0, y=0.0)
UP, DOWN = Point3(x=0.0, y=0.0, z=1.0), Point3(x=0.0, y=0.0, z=-1.0)
TOLERANCE = 1e-6


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def created(bus: Bus, command: Command) -> EntityId:
    return applied(bus.execute(command)).created_ids[0]


def p3(x: float, y: float, z: float) -> Point3:
    return Point3(x=x, y=y, z=z)


def plate() -> tuple[Bus, EntityId, EntityId]:
    bus = Bus(kernel=FakeKernel())
    rectangle = created(bus, CreateRectangle(corner=ORIGIN, width=120.0, height=50.0))
    return bus, rectangle, created(bus, CreateExtrude(depth=10.0))


def picked(bus: Bus, point: Point3, normal: Point3, tolerance: float = TOLERANCE) -> str | None:
    found = bus.queries.face_at(point, normal, tolerance)
    return None if found is None else f"{found.feature} {found.face}"


def test_each_face_of_a_plate_is_picked_by_where_it_is_and_which_way_it_faces() -> None:
    bus, r, e = plate()
    assert picked(bus, p3(60, 25, 10), UP) == f"{e} end"
    assert picked(bus, p3(60, 25, 0), DOWN) == f"{e} start"
    assert picked(bus, p3(120, 25, 5), p3(1, 0, 0)) == f"{e} side {r}.right"
    assert picked(bus, p3(0, 25, 5), p3(-1, 0, 0)) == f"{e} side {r}.left"
    assert picked(bus, p3(60, 50, 5), p3(0, 1, 0)) == f"{e} side {r}.top"
    assert picked(bus, p3(60, 0, 5), p3(0, -1, 0)) == f"{e} side {r}.bottom"


def test_off_the_face_or_facing_another_way_is_nothing() -> None:
    bus, _, _ = plate()
    assert picked(bus, p3(130, 25, 10), UP) is None  # beyond the plate
    assert picked(bus, p3(60, 25, 10), DOWN) is None  # the top faces up
    assert picked(bus, p3(60, 25, 5), UP) is None  # inside the solid: no face there
    assert picked(bus, p3(60, 25, 10.001), UP) is None
    assert picked(bus, p3(60, 25, 10.001), UP, tolerance=0.01) is not None


def test_the_normal_may_stray_half_a_degree() -> None:
    bus, _, e = plate()
    for degrees, found in ((0.3, f"{e} end"), (1.0, None)):
        t = math.radians(degrees)
        assert picked(bus, p3(60, 25, 10), p3(math.sin(t), 0, math.cos(t))) == found
    assert picked(bus, p3(60, 25, 10), p3(0, 0, 7)) == f"{e} end"  # any length


def test_a_holes_walls_face_into_it_and_its_inside_is_no_face() -> None:
    bus = Bus(kernel=FakeKernel())
    created(bus, CreateRectangle(corner=ORIGIN, width=120.0, height=50.0))
    hole = created(bus, CreateRectangle(corner=Point2(x=50.0, y=20.0), width=20.0, height=10.0))
    e = created(bus, CreateExtrude(depth=10.0))
    assert picked(bus, p3(70, 25, 5), p3(-1, 0, 0)) == f"{e} side {hole}.right"
    assert picked(bus, p3(60, 25, 10), UP) is None  # over the hole
    assert picked(bus, p3(40, 25, 10), UP) == f"{e} end"


def test_a_pockets_floor_and_walls_are_the_cuts_and_the_top_beside_it_the_plates() -> None:
    bus, _, e = plate()
    top = created(bus, CreateSketch(plane=FaceRef(feature=e, face="end")))
    pocket = created(
        bus,
        CreateRectangle(corner=Point2(x=50.0, y=20.0), width=20.0, height=10.0, sketch=top),
    )
    cut = created(bus, CreateExtrude(depth=4.0, sketch=top, operation=ExtrudeOperation.REMOVE))
    assert picked(bus, p3(60, 25, 6), UP) == f"{cut} end"
    assert picked(bus, p3(70, 25, 8), p3(-1, 0, 0)) == f"{cut} side {pocket}.right"
    assert picked(bus, p3(20, 25, 10), UP) == f"{e} end"


def test_the_latest_feature_wins_where_two_made_the_same_surface() -> None:
    """A boss on top, then a wider plate drawn on Top again up to the boss's height: the
    boss's top and the new plate's top are one surface, and it's the new plate's."""
    bus, _, e = plate()
    top = created(bus, CreateSketch(plane=FaceRef(feature=e, face="end")))
    created(bus, CreateCircle(center=Point2(x=30.0, y=25.0), radius=5.0, sketch=top))
    boss = created(bus, CreateExtrude(depth=6.0, sketch=top))
    assert picked(bus, p3(30, 25, 16), UP) == f"{boss} end"
    assert picked(bus, p3(35, 25, 13), p3(1, 0, 0)) is None  # an arc's side is curved
    second = created(bus, CreateSketch(plane=FaceRef(feature=e, face="start")))
    created(
        bus,
        CreateRectangle(corner=Point2(x=0.0, y=-50.0), width=120.0, height=50.0, sketch=second),
    )
    refill = created(
        bus,
        CreateExtrude(depth=16.0, sketch=second, operation=ExtrudeOperation.ADD, reversed=True),
    )
    assert picked(bus, p3(30, 25, 16), UP) == f"{refill} end"


def test_every_named_face_is_picked_at_its_own_middle() -> None:
    """Each face's frame, walked a little way in along its axes, is that face."""
    bus = Bus(kernel=FakeKernel())
    corners = [Point2(x=0.0, y=0.0), Point2(x=40.0, y=0.0), Point2(x=10.0, y=30.0)]
    for a, b in zip(corners, [*corners[1:], corners[0]], strict=True):
        created(bus, CreateLine(start=a, end=b))
    e = created(bus, CreateExtrude(depth=5.0))
    names = bus.queries.faces(e)
    assert not isinstance(names, Error)
    for ref in names:
        f = bus.queries.plane_frame(ref)
        assert isinstance(f, Frame)
        n = part.normal(f)
        inward = _inside_point(bus, ref, f)
        found = bus.queries.face_at(inward, n, 1e-6)
        assert found == ref, (ref, found)


def _inside_point(bus: Bus, ref: FaceRef, f: Frame) -> Point3:
    """A point on the face: for the caps, the triangle's centroid; for a side, the middle of
    the line half-way up."""
    if ref.face in ("start", "end"):
        z = 0.0 if ref.face == "start" else 5.0
        return p3(50 / 3, 10, z)
    entity = bus.document.entities[EntityId(ref.face.removeprefix("side "))]
    a, b = entity.start, entity.end  # type: ignore[attr-defined]
    return p3((a.x + b.x) / 2, (a.y + b.y) / 2, 2.5)


@pytest.mark.parametrize(
    ("point", "normal", "tolerance"),
    [
        (p3(math.nan, 25, 10), UP, TOLERANCE),
        (p3(60, 25, 10), p3(0, 0, 0), TOLERANCE),
        (p3(60, 25, 10), UP, -1.0),
        (p3(60, 25, 10), UP, math.inf),
        ("up", UP, TOLERANCE),
        (p3(60, 25, 10), (0, 0, 1), TOLERANCE),
    ],
)
def test_input_it_cant_use_picks_nothing(point: object, normal: object, tolerance: float) -> None:
    bus, _, _ = plate()
    assert bus.queries.face_at(point, normal, tolerance) is None  # type: ignore[arg-type]
