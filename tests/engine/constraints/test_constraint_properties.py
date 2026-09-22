"""Random constrained sketches, checked against invariants that must hold for any of them.

Hypothesis builds sketches from random geometry, random constraints and dimensions on random
selections, and random edits and moves. Whatever the engine accepts or refuses:

- an applied command never leaves a constraint unsatisfied;
- a rejected command changes nothing;
- undo and redo are exact;
- the file round-trips exactly, and the solve status survives it;
- replaying the recorded commands rebuilds the same bytes.
"""

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateArc,
    CreateCircle,
    CreateConstraint,
    CreateDimension,
    CreateLine,
    CreatePoint,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    MoveEntities,
    Rejected,
)
from caliper.contracts.document import (
    CURVE_FEATURES,
    POINT_FEATURES,
    ConstraintType,
    Point2,
    Ref,
)
from caliper.contracts.queries import ConstraintState, DimensionType
from caliper.engine.commands.bus import Bus
from caliper.engine.constraints.sketch import GEOMETRY
from caliper.engine.io import codec, script, snapshot

CURVES = {f for features in CURVE_FEATURES.values() for f in features}

coordinate = st.floats(min_value=-100, max_value=100).map(lambda v: round(v, 3))
size = st.floats(min_value=1, max_value=100).map(lambda v: round(v, 3))
points = st.builds(Point2, x=coordinate, y=coordinate)


@st.composite
def geometry(draw: st.DrawFn) -> Command:
    match draw(st.sampled_from(["point", "line", "circle", "arc", "rectangle"])):
        case "point":
            return CreatePoint(position=draw(points))
        case "line":
            start = draw(points)
            return CreateLine(start=start, end=Point2(x=start.x + draw(size), y=draw(coordinate)))
        case "circle":
            return CreateCircle(center=draw(points), radius=draw(size))
        case "arc":
            return CreateArc(
                center=draw(points),
                radius=draw(size),
                start_angle=draw(st.floats(min_value=0, max_value=359).map(round)),
                sweep_angle=draw(st.floats(min_value=10, max_value=300).map(round)),
            )
        case _:
            return CreateRectangle(corner=draw(points), width=draw(size), height=draw(size))


def features(bus: Bus) -> list[Ref]:
    return [
        Ref(entity=id, feature=feature)
        for id, entity in sorted(bus.document.entities.items())
        if isinstance(entity, GEOMETRY)
        for feature in sorted(POINT_FEATURES[type(entity)] | CURVE_FEATURES[type(entity)])
    ]


@st.composite
def sessions(draw: st.DrawFn) -> tuple[Bus, list[Command]]:
    bus = Bus(kernel=None)
    history: list[Command] = []

    def attempt(command: Command) -> None:
        before = bus.document
        result = bus.execute(command)
        if isinstance(result, Rejected):
            assert bus.document == before, "a rejected command changed the document"
            return
        assert isinstance(result, Applied)
        history.append(result.command)
        state = bus.queries.solve_status()
        assert state.state is not ConstraintState.CONFLICTING, (command, state)

    for command in draw(st.lists(geometry(), min_size=2, max_size=5)):
        attempt(command)
    for _ in range(draw(st.integers(min_value=1, max_value=8))):
        refs = features(bus)
        geometry_ids = sorted({r.entity for r in refs})
        match draw(st.sampled_from(["constraint", "dimension", "edit", "move", "delete"])):
            case "constraint":
                # Ask the engine what fits the selection, as the shell or an agent would. Pairs,
                # often of curves, reach the most types; Fix fits anything, so it comes last.
                pool = draw(st.sampled_from([refs, [r for r in refs if r.feature in CURVES]]))
                count = draw(st.sampled_from([1, 2, 2, 2, 3]))
                if len(pool) < count:
                    continue
                chosen = draw(
                    st.lists(st.sampled_from(pool), min_size=count, max_size=count, unique=True)
                )
                fits = [
                    o.type
                    for o in bus.queries.applicable_constraints(chosen)
                    if o.error is None and isinstance(o.type, ConstraintType)
                ]
                others = [f for f in fits if f is not ConstraintType.FIX]
                if fits:
                    type_ = draw(st.sampled_from(others or fits))
                    attempt(CreateConstraint(type=type_, refs=tuple(chosen)))
            case "dimension":
                chosen = draw(st.lists(st.sampled_from(refs), min_size=1, max_size=2, unique=True))
                placement = draw(points)
                kind = bus.queries.infer_dimension(chosen, placement)
                if isinstance(kind, DimensionType):
                    scale = draw(st.one_of(st.none(), st.floats(min_value=0.5, max_value=1.5)))
                    value = None
                    if scale is not None:
                        limit = 179.0 if kind is DimensionType.ANGLE else float("inf")
                        value = min(limit, round(draw(size) * scale, 3))
                    attempt(CreateDimension(refs=tuple(chosen), placement=placement, value=value))
            case "edit":
                id = draw(st.sampled_from(geometry_ids))
                entity = bus.document.entities[id]
                field = draw(
                    st.sampled_from([f for f in type(entity).__slots__ if f != "construction"])
                )
                current = getattr(entity, field)
                if isinstance(current, Point2):
                    attempt(ModifyEntity(id=id, changes={field: draw(points)}))
                elif isinstance(current, float):
                    attempt(ModifyEntity(id=id, changes={field: draw(size)}))
            case "move":
                id = draw(st.sampled_from(geometry_ids))
                attempt(MoveEntities(ids=(id,), dx=draw(coordinate), dy=draw(coordinate)))
            case "delete" if len(geometry_ids) > 2:
                attempt(DeleteEntities(ids=(draw(st.sampled_from(sorted(bus.document.entities))),)))
            case _:
                pass
    return bus, history


PROPERTIES = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@PROPERTIES
@given(session=sessions())
def test_files_round_trip_with_their_constraints_and_status(
    session: tuple[Bus, list[Command]],
) -> None:
    bus, history = session
    text = snapshot.dumps(bus.document, history=history)
    read = snapshot.read(text)
    assert read.document == bus.document
    assert snapshot.dumps(read.document, history=read.history) == text
    assert Bus(read.document, kernel=None).queries.solve_status() == bus.queries.solve_status()


@PROPERTIES
@given(session=sessions())
def test_replaying_the_recorded_commands_rebuilds_the_same_bytes(
    session: tuple[Bus, list[Command]],
) -> None:
    bus, history = session
    text = json.dumps(
        {"format": script.FORMAT, "schema_version": 1, "commands": codec.encode(tuple(history))}
    )
    replayed = Bus(kernel=None)
    for command in script.loads(text):
        assert isinstance(replayed.execute(command), Applied), command
    assert snapshot.dumps(replayed.document) == snapshot.dumps(bus.document)


@PROPERTIES
@given(session=sessions())
def test_undoing_everything_and_redoing_it_is_exact(session: tuple[Bus, list[Command]]) -> None:
    bus, _ = session
    documents = [bus.document]
    while bus.undo() is not None:
        documents.append(bus.document)
    assert bus.document.entities == {}
    for expected in reversed(documents[:-1]):
        bus.redo()
        assert bus.document == expected
