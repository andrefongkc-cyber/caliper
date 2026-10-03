"""Decoding rebuilds dataclasses and containers from the shape of the data and leaves the rest
as it came, for validation to judge. Performance V2.2's Perf-7 made each class's decoder once
instead of reading its type hints for every object; these pin what decoding rebuilds, what it
leaves alone, and that the hints are read once.
"""

from types import MappingProxyType

import pytest

from caliper.contracts.commands import (
    Applied,
    CreateCircle,
    CreateConstraint,
    CreateDistanceDimension,
    CreateLine,
    CreateRectangle,
)
from caliper.contracts.document import (
    ConstraintType,
    DistanceOrientation,
    Feature,
    Point2,
    Ref,
)
from caliper.engine.commands.bus import Bus
from caliper.engine.io import codec, snapshot

REF = {"entity": "e1", "feature": "start"}


def test_containers_are_rebuilt_as_the_contracts_hold_them() -> None:
    constraint = codec.decode_command(
        {"kind": "create_constraint", "type": "coincident", "refs": [REF, REF]}, "c"
    )
    assert type(constraint.refs) is tuple  # type: ignore[union-attr]
    assert [type(r) for r in constraint.refs] == [Ref, Ref]  # type: ignore[union-attr]
    move = codec.decode_command({"kind": "move_entities", "ids": ["e1"], "dx": 1, "dy": 0}, "c")
    assert move.ids == ("e1",)  # type: ignore[union-attr]
    modify = codec.decode_command(
        {
            "kind": "modify_entity",
            "id": "e2",
            "changes": {"center": {"x": 1, "y": 2}, "a": REF, "refs": [REF], "radius": 3},
        },
        "c",
    )
    changes = modify.changes  # type: ignore[union-attr]
    assert type(changes) is MappingProxyType
    assert type(changes["center"]) is Point2
    assert type(changes["a"]) is Ref
    assert changes["refs"] == [REF]  # a list where a union is expected stays a list
    assert changes["radius"] == 3


@pytest.mark.parametrize(
    ("data", "field", "left"),
    [
        ({"kind": "create_circle", "center": [1, 2], "radius": 1}, "center", [1, 2]),
        (
            {"kind": "create_constraint", "type": "fixed", "refs": {"a": 1}},
            "refs",
            {"a": 1},
        ),
        ({"kind": "move_entities", "ids": "e1", "dx": 1, "dy": 0}, "ids", "e1"),
        ({"kind": "modify_entity", "id": "e1", "changes": [1, 2]}, "changes", [1, 2]),
    ],
)
def test_data_of_the_wrong_shape_is_left_as_it_came(
    data: dict[str, object], field: str, left: object
) -> None:
    decoded = getattr(codec.decode_command(data, "c"), field)
    assert decoded == left
    assert type(decoded) is type(left)


def test_a_dict_matching_no_member_of_a_union_is_left_as_it_came() -> None:
    modify = codec.decode_command(
        {"kind": "modify_entity", "id": "e1", "changes": {"center": {"x": 1, "y": 2, "z": 3}}},
        "c",
    )
    assert modify.changes["center"] == {"x": 1, "y": 2, "z": 3}  # type: ignore[union-attr]


def test_a_class_s_type_hints_are_read_once(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = Bus()
    for command in (
        CreateCircle(center=Point2(x=0.0, y=0.0), radius=5.0),
        CreateRectangle(corner=Point2(x=10.0, y=0.0), width=8.0, height=4.0),
        *(CreateLine(start=Point2(x=k, y=20.0), end=Point2(x=k + 1.0, y=25.0)) for k in range(20)),
    ):
        result = bus.execute(command)
        assert isinstance(result, Applied)
    (line,) = result.created_ids
    for command in (
        CreateConstraint(
            type=ConstraintType.HORIZONTAL, refs=(Ref(entity=line, feature=Feature.CURVE),)
        ),
        CreateDistanceDimension(
            a=Ref(entity=line, feature=Feature.START),
            b=Ref(entity=line, feature=Feature.END),
            orientation=DistanceOrientation.ALIGNED,
            offset=2.0,
        ),
    ):
        assert isinstance(bus.execute(command), Applied)
    text = snapshot.dumps(bus.document)
    looked_up: list[type] = []
    real = codec.get_type_hints
    monkeypatch.setattr(codec, "_SHAPES", {})
    monkeypatch.setattr(codec, "get_type_hints", lambda cls: (looked_up.append(cls), real(cls))[1])
    assert snapshot.loads(text) == bus.document
    assert len(looked_up) == len(set(looked_up)) < 10  # one per class, not per object
    looked_up.clear()
    snapshot.loads(text)
    assert looked_up == []
