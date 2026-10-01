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
    Feature,
    Plane,
    Point2,
    Ref,
)
from caliper.contracts.errors import Error
from caliper.contracts.kernel import Frame, Loop, Shape
from caliper.engine import features, graph
from caliper.engine.commands.bus import Bus
from caliper.engine.constraints import sketch
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
