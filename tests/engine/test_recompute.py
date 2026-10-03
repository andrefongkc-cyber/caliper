"""Recomputing only what a change reaches (V2's F3; core.md's graph, items 1 to 3; ADR 0013).

A kernel that counts its own work shows exactly what each change made it build again: the
features a change reaches, and nothing else. The graph's 2D layer is shown to be the solver's
own index under its new names, over random sessions.
"""

from collections import Counter
from collections.abc import Sequence

import pytest
from hypothesis import given

from caliper.contracts.commands import (
    Applied,
    Command,
    CommandResult,
    CreateCheck,
    CreateCircle,
    CreateDistanceDimension,
    CreateExtrude,
    CreateRectangle,
    CreateSketch,
    ModifyEntity,
)
from caliper.contracts.document import (
    FIRST_SKETCH,
    DistanceOrientation,
    EntityId,
    ExtrudeOperation,
    Feature,
    Metric,
    Plane,
    Point2,
    Ref,
)
from caliper.contracts.errors import Error
from caliper.contracts.kernel import Frame, Loop, Shape
from caliper.contracts.queries import BoundingBox3
from caliper.engine import features, graph
from caliper.engine.commands.bus import Bus
from caliper.engine.constraints import sketch
from caliper.engine.document.recent import ByIdentity
from caliper.engine.geometry.fake_kernel import FakeKernel
from tests.engine.constraints.test_constraint_properties import PROPERTIES, sessions

E0 = FIRST_SKETCH


class Counting(FakeKernel):
    """The analytic kernel, counting each solid it builds."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def make_face(self, outer: Loop, holes: Sequence[Loop] = ()) -> Shape:
        self.calls["face"] += 1
        return super().make_face(outer, holes)

    def extrude(self, face: Shape, frame: Frame, depth: float) -> Shape:
        self.calls["extrude"] += 1
        return super().extrude(face, frame, depth)

    def union(self, a: Shape, b: Shape) -> Shape:
        self.calls["union"] += 1
        return super().union(a, b)

    def cut(self, a: Shape, b: Shape) -> Shape:
        self.calls["cut"] += 1
        return super().cut(a, b)

    def taken(self) -> Counter[str]:
        """What was built since last asked."""
        found, self.calls = self.calls, Counter()
        return found


@pytest.fixture(autouse=True)
def fresh() -> None:
    features.forget()  # count from nothing


def applied(result: CommandResult) -> Applied:
    assert isinstance(result, Applied), result
    return result


def created(bus: Bus, command: Command) -> EntityId:
    (id,) = applied(bus.execute(command)).created_ids
    return id


def volume(bus: Bus) -> float:
    found = bus.queries.solid_properties()
    assert not isinstance(found, Error), found
    return found.volume


def milestone(kernel: Counting) -> tuple[Bus, EntityId, EntityId, EntityId]:
    """A 120 x 50 plate held by a driving width dimension, extruded 10 mm."""
    bus = Bus(kernel=kernel)
    plate = created(bus, CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0))
    width = created(
        bus,
        CreateDistanceDimension(
            a=Ref(entity=plate, feature=Feature.BOTTOM_LEFT),
            b=Ref(entity=plate, feature=Feature.BOTTOM_RIGHT),
            orientation=DistanceOrientation.HORIZONTAL,
            offset=-10.0,
            value=120.0,
        ),
    )
    extrude = created(bus, CreateExtrude(depth=10.0))
    return bus, plate, width, extrude


def test_the_width_recomputes_the_one_prism_a_label_nothing_and_undo_nothing() -> None:
    kernel = Counting()
    bus, plate, width, extrude = milestone(kernel)
    assert volume(bus) == pytest.approx(60_000.0)
    assert kernel.taken() == {"face": 1, "extrude": 1}
    assert volume(bus) == pytest.approx(60_000.0)
    assert kernel.taken() == {}  # the same document: nothing to do

    applied(bus.execute(ModifyEntity(id=width, changes={"value": 140.0})))
    assert graph.affected(bus.document, [plate]) >= {plate, E0, extrude}
    assert volume(bus) == pytest.approx(70_000.0)
    assert kernel.taken() == {"face": 1, "extrude": 1}  # the sketch and the extrude, once

    applied(bus.execute(ModifyEntity(id=width, changes={"offset": -20.0})))  # its label
    assert extrude not in graph.affected(bus.document, [width])
    assert volume(bus) == pytest.approx(70_000.0)
    assert kernel.taken() == {}  # the plate is the same object: the prism stands

    bus.undo()  # the label
    bus.undo()  # the width
    assert volume(bus) == pytest.approx(60_000.0)
    assert kernel.taken() == {}  # undo put the old plate back, and its prism was kept
    bus.redo()
    assert volume(bus) == pytest.approx(70_000.0)
    assert kernel.taken() == {}


def test_a_change_to_one_sketch_recomputes_its_extrude_and_what_builds_on_it() -> None:
    """Two plates from two sketches, the second joined to the first. A change to the second
    sketch builds its prism and the join again; the first prism stands. A change to the first
    builds its prism and the join; the second prism stands."""
    kernel = Counting()
    bus = Bus(kernel=kernel)
    first = created(bus, CreateRectangle(corner=Point2(x=0.0, y=0.0), width=10.0, height=10.0))
    created(bus, CreateExtrude(depth=10.0))
    other = created(bus, CreateSketch(plane=Plane.XY))
    second = created(bus, CreateCircle(center=Point2(x=50.0, y=5.0), radius=5.0, sketch=other))
    created(bus, CreateExtrude(depth=10.0, sketch=other))
    volume(bus)
    assert kernel.taken() == {"face": 2, "extrude": 2, "union": 1}

    applied(bus.execute(ModifyEntity(id=second, changes={"radius": 4.0})))
    volume(bus)
    assert kernel.taken() == {"face": 1, "extrude": 1, "union": 1}

    applied(bus.execute(ModifyEntity(id=first, changes={"height": 20.0})))
    volume(bus)
    assert kernel.taken() == {"face": 1, "extrude": 1, "union": 1}


def test_what_a_change_reaches_is_its_dependents_in_turn() -> None:
    kernel = Counting()
    bus, plate, width, extrude = milestone(kernel)
    document = bus.document
    assert graph.inputs(document, extrude) == {E0}
    assert graph.inputs(document, E0) == {plate}  # a sketch reads what's drawn in it
    assert graph.inputs(document, width) == {plate}
    assert graph.dependents(document)[E0] == {extrude}
    assert graph.affected(document, [plate]) == {plate, width, E0, extrude}
    assert graph.affected(document, [width]) == {width}
    assert graph.order(document) == (E0, extrude)


def test_a_cut_reads_the_solid_before_it_and_a_check_what_it_measures() -> None:
    """The graph's other edges (F8): a second extrude reads its sketch, its profile, and the
    extrude it builds on; a check reads what it measures. A change reaches the cut by two
    paths, and is looked at once."""
    bus = Bus(kernel=FakeKernel())
    (plate,) = bus.execute(  # type: ignore[union-attr]
        CreateRectangle(corner=Point2(x=0.0, y=0.0), width=120.0, height=50.0)
    ).created_ids
    (hole,) = bus.execute(  # type: ignore[union-attr]
        CreateCircle(center=Point2(x=60.0, y=25.0), radius=5.0)
    ).created_ids
    (boss,) = bus.execute(CreateExtrude(depth=10.0, ids=(plate,))).created_ids  # type: ignore[union-attr]
    (cut,) = bus.execute(  # type: ignore[union-attr]
        CreateExtrude(depth=10.0, ids=(hole,), operation=ExtrudeOperation.REMOVE)
    ).created_ids
    measured = CreateCheck(metric=Metric.BBOX_WIDTH, expected=120.0, tolerance=1e-6, ids=(plate,))
    (check,) = bus.execute(measured).created_ids  # type: ignore[union-attr]
    document = bus.document
    assert graph.inputs(document, boss) == {E0, plate}
    assert graph.inputs(document, cut) == {E0, hole, boss}
    assert graph.inputs(document, check) == {plate}
    assert graph.inputs(document, EntityId("e99")) == frozenset()
    assert graph.dependents(document)[plate] >= {E0, boss, check}
    assert graph.affected(document, [plate]) == {plate, E0, boss, cut, check}
    assert graph.affected(document, [hole]) == {hole, E0, boss, cut}  # the sketch reads it
    assert graph.order(document) == (E0, boss, cut)


# --- The 2D layer is the solver's own -----------------------------------------------------


@PROPERTIES
@given(session=sessions())
def test_the_graph_names_the_solvers_references_and_referrers(
    session: tuple[Bus, list[Command]],
) -> None:
    bus, _ = session
    document = bus.document
    index = graph.dependents(document)
    referrers = sketch.referrers(document)
    for id, entity in document.entities.items():
        assert graph.inputs(document, id) == {r.entity for r in sketch.references(entity)}
        # Everything the solver says refers to an entity depends on it in the graph, and the
        # only other dependent of geometry is the sketch it's drawn in.
        extra = index.get(id, frozenset()) - referrers.get(id, frozenset())
        assert extra <= {E0}


def test_the_caches_keep_only_their_last_few_entries() -> None:
    """Recompute keeps prisms, solids, and meshes by the objects they came from, but only the
    last few: a long session's edits mustn't hold every solid ever built (F8)."""
    cache: ByIdentity[int] = ByIdentity(2)
    a, b, c = object(), object(), object()
    cache.put((a,), 1)
    cache.put((b,), 2)
    assert cache.get((a,)) == 1  # used: now the most recent
    cache.put((c,), 3)
    assert cache.get((b,)) is None  # the least recently used went
    assert (cache.get((a,)), cache.get((c,))) == (1, 3)
    assert cache.get((object(),)) is None


def test_a_solids_volume_and_box_are_worked_out_once_per_solid() -> None:
    """The Part panel, the status bar, and the 3D view each ask after a change; a change that
    leaves the solid alone (a label) asks the kernel nothing (Performance V2.2, Perf-3)."""

    class Measuring(Counting):
        def volume(self, solid: Shape) -> float:
            self.calls["volume"] += 1
            return super().volume(solid)

        def bounding_box_3d(self, solid: Shape) -> BoundingBox3:
            self.calls["box"] += 1
            return super().bounding_box_3d(solid)

    kernel = Measuring()
    bus, _, width, _ = milestone(kernel)
    first = bus.queries.solid_properties()
    assert bus.queries.solid_properties() == first
    assert kernel.taken()["volume"] == 1
    bus.execute(ModifyEntity(id=width, changes={"offset": -20.0}))  # the label moves
    assert bus.queries.solid_properties() == first
    assert kernel.taken()["volume"] == 0
    bus.execute(ModifyEntity(id=width, changes={"value": 140.0}))  # the solid changes
    changed = bus.queries.solid_properties()
    assert not isinstance(changed, Error)
    assert changed.volume == pytest.approx(70_000.0)
    assert kernel.taken()["volume"] == 1
