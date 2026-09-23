"""Geometry and document invariants over random sketches of every entity kind.

Each test builds sketches only through commands, then checks a property that must hold for
any of them: round trips, replay, exact undo, and that measurements move with geometry.
"""

import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateArc,
    CreateCircle,
    CreateDistanceDimension,
    CreateLine,
    CreateRadialDimension,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    MoveEntities,
)
from caliper.contracts.document import (
    POINT_FEATURES,
    Arc,
    Circle,
    DistanceOrientation,
    EntityId,
    Feature,
    Line,
    Point2,
    RadialMeasure,
    Rectangle,
    Ref,
)
from caliper.contracts.queries import AreaProperties, BoundingBox, Distance, Expectation, Metric
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry.fake_kernel import FakeKernel
from caliper.engine.io import codec, script, snapshot

GEOMETRY = Line | Circle | Arc | Rectangle

coordinate = st.floats(min_value=-1e4, max_value=1e4)
size = st.floats(min_value=0.01, max_value=1e4)
points = st.builds(Point2, x=coordinate, y=coordinate)


@st.composite
def geometry(draw: st.DrawFn) -> Command:
    match draw(st.sampled_from(["line", "circle", "arc", "rectangle"])):
        case "line":
            start = draw(points)
            dx, dy = draw(size), draw(coordinate)
            return CreateLine(start=start, end=Point2(x=start.x + dx, y=start.y + dy))
        case "circle":
            return CreateCircle(center=draw(points), radius=draw(size))
        case "arc":
            return CreateArc(
                center=draw(points),
                radius=draw(size),
                start_angle=draw(st.floats(min_value=-360.0, max_value=360.0)),
                sweep_angle=draw(st.floats(min_value=0.01, max_value=359.99)),
            )
        case _:
            return CreateRectangle(corner=draw(points), width=draw(size), height=draw(size))


@st.composite
def sessions(draw: st.DrawFn) -> tuple[Bus, list[Command]]:
    """A bus after random creates, dimensions, edits, moves, and deletes, plus what ran."""
    bus = Bus(kernel=FakeKernel())
    resolved: list[Command] = []

    def run(command: Command) -> None:
        result = bus.execute(command)
        assert isinstance(result, Applied), result
        resolved.append(result.command)

    for command in draw(st.lists(geometry(), min_size=1, max_size=5)):
        run(command)
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        entities = bus.document.entities
        ids = sorted(entities)
        geometry_ids = [i for i in ids if isinstance(entities[i], GEOMETRY)]
        match draw(st.sampled_from(["distance", "radial", "modify", "move", "delete", "create"])):
            case "distance" if geometry_ids:
                a, b = draw(st.sampled_from(geometry_ids)), draw(st.sampled_from(geometry_ids))
                fa = draw(st.sampled_from(sorted(POINT_FEATURES[type(entities[a])])))  # type: ignore[index]
                fb = draw(st.sampled_from(sorted(POINT_FEATURES[type(entities[b])])))  # type: ignore[index]
                if (a, fa) != (b, fb):
                    run(
                        CreateDistanceDimension(
                            a=Ref(entity=a, feature=fa),
                            b=Ref(entity=b, feature=fb),
                            orientation=draw(st.sampled_from(list(DistanceOrientation))),
                            offset=draw(coordinate),
                        )
                    )
            case "radial":
                curves = [i for i in ids if isinstance(entities[i], Circle | Arc)]
                if curves:
                    run(
                        CreateRadialDimension(
                            target=draw(st.sampled_from(curves)),
                            measure=draw(st.sampled_from(list(RadialMeasure))),
                            label_angle=draw(st.floats(min_value=0.0, max_value=359.0)),
                        )
                    )
            case "modify" if geometry_ids:
                id = draw(st.sampled_from(geometry_ids))
                entity = entities[id]
                field = "radius" if isinstance(entity, Circle | Arc) else None
                field = "width" if isinstance(entity, Rectangle) else field
                if field is not None:
                    run(ModifyEntity(id=id, changes={field: draw(size)}))
            case "move" if geometry_ids:
                chosen = draw(st.lists(st.sampled_from(ids), min_size=1, unique=True))
                run(MoveEntities(ids=tuple(chosen), dx=draw(coordinate), dy=draw(coordinate)))
            case "delete" if len(geometry_ids) > 1:
                run(DeleteEntities(ids=(draw(st.sampled_from(ids)),)))
            case "create":
                run(draw(geometry()))
            case _:
                pass
    return bus, resolved


# --- Files and replay -------------------------------------------------------------------


@given(session=sessions())
def test_any_document_and_its_history_round_trip_exactly(
    session: tuple[Bus, list[Command]],
) -> None:
    bus, history = session
    text = snapshot.dumps(bus.document, history=history)
    read = snapshot.read(text)
    assert read.document == bus.document
    assert read.history == tuple(history)
    assert snapshot.dumps(read.document, history=read.history) == text


@given(session=sessions())
def test_replaying_the_resolved_commands_reproduces_the_file(
    session: tuple[Bus, list[Command]],
) -> None:
    """Invariant 4 for every command kind: the history is a script that rebuilds the file."""
    bus, history = session
    text = json.dumps(
        {"format": script.FORMAT, "schema_version": 1, "commands": codec.encode(tuple(history))}
    )
    replayed = Bus()
    for command in script.loads(text):
        assert isinstance(replayed.execute(command), Applied)
    assert snapshot.dumps(replayed.document) == snapshot.dumps(bus.document)


@given(session=sessions(), command=geometry(), data=st.data())
def test_undo_then_redo_of_any_command_is_exact(
    session: tuple[Bus, list[Command]], command: Command, data: st.DataObject
) -> None:
    bus, _ = session
    ids = sorted(bus.document.entities)
    command = data.draw(
        st.sampled_from(
            [
                command,
                MoveEntities(ids=tuple(ids), dx=1.25, dy=-3.5),
                DeleteEntities(ids=(ids[0],)),
                DeleteEntities(ids=tuple(ids)),
            ]
        )
    )
    before = bus.document
    result = bus.execute(command)
    assert isinstance(result, Applied), result
    after = bus.document
    if result.delta.before or result.delta.after:
        bus.undo()
        assert bus.document == before
        bus.redo()
        assert bus.document == after


# --- Features and measurements ----------------------------------------------------------


def refs(bus: Bus) -> list[Ref]:
    return [
        Ref(entity=id, feature=feature)
        for id, entity in sorted(bus.document.entities.items())
        if isinstance(entity, GEOMETRY)
        for feature in sorted(POINT_FEATURES[type(entity)])  # type: ignore[index]
    ]


@given(session=sessions())
def test_every_feature_snaps_back_to_its_own_location(session: tuple[Bus, list[Command]]) -> None:
    bus, _ = session
    queries = bus.queries
    for ref in refs(bus):
        point = queries.feature_point(ref)
        assert isinstance(point, Point2)
        snapped = queries.nearest_feature(point, 0.0)
        assert snapped is not None
        assert queries.feature_point(snapped) == point


@given(command=geometry())
def test_outline_features_lie_on_the_outline(command: Command) -> None:
    bus = Bus()
    assert isinstance(bus.execute(command), Applied)
    entity = bus.document.entities[EntityId("e1")]
    scale = max(1.0, *(abs(v) for v in _numbers(entity)))
    for ref in refs(bus):
        if ref.feature is Feature.CENTER:
            continue
        point = bus.queries.feature_point(ref)
        assert isinstance(point, Point2)
        assert bus.queries.entity_at_point(point, 1e-9 * scale) == EntityId("e1"), ref.feature


def _numbers(entity: object) -> list[float]:
    match entity:
        case Line(start=a, end=b):
            return [a.x, a.y, b.x, b.y]
        case Circle(center=c, radius=r) | Arc(center=c, radius=r):
            return [c.x, c.y, r]
        case Rectangle(corner=c, width=w, height=h):
            return [c.x, c.y, w, h]
    return []


@given(session=sessions(), data=st.data())
def test_distances_are_antisymmetric_and_agree_with_check(
    session: tuple[Bus, list[Command]], data: st.DataObject
) -> None:
    bus, _ = session
    a, b = data.draw(st.sampled_from(refs(bus))), data.draw(st.sampled_from(refs(bus)))
    forward, backward = bus.queries.measure_distance(a, b), bus.queries.measure_distance(b, a)
    assert isinstance(forward, Distance)
    assert isinstance(backward, Distance)
    assert (backward.dx, backward.dy) == (-forward.dx, -forward.dy)
    assert forward.value == backward.value == math.hypot(forward.dx, forward.dy)
    for metric, value in [
        (Metric.DISTANCE, forward.value),
        (Metric.DISTANCE_X, abs(forward.dx)),
        (Metric.DISTANCE_Y, abs(forward.dy)),
    ]:
        result = bus.queries.check(
            Expectation(metric=metric, expected=value, tolerance=0.0, refs=(a, b))
        )
        assert result.passed, (metric, result)


@given(session=sessions(), dx=coordinate, dy=coordinate)
def test_moving_a_whole_sketch_moves_positions_and_keeps_sizes(
    session: tuple[Bus, list[Command]], dx: float, dy: float
) -> None:
    bus, _ = session
    entities = bus.document.entities
    geometry_ids = sorted(i for i, e in entities.items() if isinstance(e, GEOMETRY))
    dimension_ids = sorted(set(entities) - set(geometry_ids))
    closed = [i for i in geometry_ids if isinstance(entities[i], Circle | Rectangle)]

    before_box = bus.queries.bounding_box()
    before_values = [bus.queries.dimension_value(i) for i in dimension_ids]
    before_areas = [bus.queries.area_properties([i]) for i in closed]
    assert isinstance(bus.execute(MoveEntities(ids=tuple(geometry_ids), dx=dx, dy=dy)), Applied)

    after_box = bus.queries.bounding_box()
    assert isinstance(before_box, BoundingBox)
    assert isinstance(after_box, BoundingBox)
    edges = (before_box.x_min, before_box.x_max, before_box.y_min, before_box.y_max)
    tol = 1e-9 * max(1.0, abs(dx), abs(dy), *(abs(v) for v in edges))
    assert after_box.x_min == pytest.approx(before_box.x_min + dx, abs=tol)
    assert after_box.y_min == pytest.approx(before_box.y_min + dy, abs=tol)
    assert after_box.width == pytest.approx(before_box.width, abs=2 * tol)
    assert after_box.height == pytest.approx(before_box.height, abs=2 * tol)
    for id, value in zip(dimension_ids, before_values, strict=True):
        assert bus.queries.dimension_value(id) == pytest.approx(value, abs=2 * tol)
    for id, props in zip(closed, before_areas, strict=True):
        moved = bus.queries.area_properties([id])
        assert isinstance(props, AreaProperties)
        assert isinstance(moved, AreaProperties)
        assert moved.area == props.area
        assert moved.ixx == props.ixx
        assert moved.centroid.x == pytest.approx(props.centroid.x + dx, abs=tol)


# --- Arcs -------------------------------------------------------------------------------


@given(session=sessions())
def test_every_stored_arc_starts_in_0_to_360(session: tuple[Bus, list[Command]]) -> None:
    bus, _ = session
    for entity in bus.document.entities.values():
        if isinstance(entity, Arc):
            assert 0.0 <= entity.start_angle < 360.0, entity


@given(
    start=st.integers(min_value=0, max_value=359),
    turns=st.integers(min_value=-3, max_value=3),
    sweep=st.integers(min_value=1, max_value=359),
)
def test_an_arc_whole_turns_apart_saves_the_same_file(start: int, turns: int, sweep: int) -> None:
    files = set()
    for degrees in (start, start + 360 * turns):
        bus = Bus()
        arc = CreateArc(
            center=Point2(x=1.0, y=2.0),
            radius=3.0,
            start_angle=float(degrees),
            sweep_angle=float(sweep),
        )
        assert isinstance(bus.execute(arc), Applied)
        files.add(snapshot.dumps(bus.document))
    assert len(files) == 1


@given(
    center=points,
    radius=size,
    start=st.floats(min_value=-1e4, max_value=1e4),
    sweep=st.floats(min_value=0.01, max_value=359.99),
)
def test_storing_the_start_angle_in_range_keeps_the_arc_where_it_was(
    center: Point2, radius: float, start: float, sweep: float
) -> None:
    bus = Bus()
    result = bus.execute(
        CreateArc(center=center, radius=radius, start_angle=start, sweep_angle=sweep)
    )
    assert isinstance(result, Applied)
    (id,) = result.created_ids
    stored = bus.document.entities[id]
    assert isinstance(stored, Arc)
    assert stored.sweep_angle == sweep
    tol = 1e-9 * max(1.0, radius, abs(center.x), abs(center.y))
    for feature, degrees in (
        (Feature.START, start),
        (Feature.MID, start + sweep / 2),
        (Feature.END, start + sweep),
    ):
        at = bus.queries.feature_point(Ref(entity=id, feature=feature))
        assert isinstance(at, Point2)
        assert at.x == pytest.approx(center.x + radius * math.cos(math.radians(degrees)), abs=tol)
        assert at.y == pytest.approx(center.y + radius * math.sin(math.radians(degrees)), abs=tol)
