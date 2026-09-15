"""`queries.check`: the verification loop in its smallest form."""

import math

import pytest

from caliper.contracts.commands import (
    Applied,
    Command,
    CreateCircle,
    CreateDistanceDimension,
    CreateLine,
    CreateRectangle,
    ModifyEntity,
)
from caliper.contracts.document import DistanceOrientation, EntityId, Feature, Point2, Ref
from caliper.contracts.errors import ErrorCode
from caliper.contracts.queries import CheckResult, Expectation, Metric
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry.fake_kernel import FakeKernel

E1, E2, E3 = EntityId("e1"), EntityId("e2"), EntityId("e3")
ORIGIN = Point2(x=0.0, y=0.0)


def bus_with(*commands: Command, kernel: bool = True) -> Bus:
    bus = Bus(kernel=FakeKernel() if kernel else None)
    for command in commands:
        assert isinstance(bus.execute(command), Applied)
    return bus


def milestone() -> Bus:
    """A 100 x 50 rectangle resized to 120 wide, with a dimension across its bottom edge."""
    bus = bus_with(
        CreateRectangle(corner=ORIGIN, width=100.0, height=50.0),
        CreateDistanceDimension(
            a=Ref(entity=E1, feature=Feature.BOTTOM_LEFT),
            b=Ref(entity=E1, feature=Feature.BOTTOM_RIGHT),
            orientation=DistanceOrientation.HORIZONTAL,
            offset=-10.0,
        ),
    )
    bus.execute(ModifyEntity(id=E1, changes={"width": 120.0}))
    return bus


def corner(feature: Feature) -> Ref:
    return Ref(entity=E1, feature=feature)


def check(bus: Bus, **fields: object) -> CheckResult:
    return bus.queries.check(Expectation(**fields))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("fields", "actual"),
    [
        ({"metric": Metric.BBOX_WIDTH, "ids": ()}, 120.0),
        ({"metric": Metric.BBOX_HEIGHT, "ids": (E1,)}, 50.0),
        (
            {
                "metric": Metric.DISTANCE,
                "refs": (corner(Feature.BOTTOM_LEFT), corner(Feature.TOP_RIGHT)),
            },
            130.0,
        ),
        (
            {
                "metric": Metric.DISTANCE_X,
                "refs": (corner(Feature.TOP_RIGHT), corner(Feature.BOTTOM_LEFT)),
            },
            120.0,
        ),
        (
            {
                "metric": Metric.DISTANCE_Y,
                "refs": (corner(Feature.TOP_RIGHT), corner(Feature.BOTTOM_LEFT)),
            },
            50.0,
        ),
        ({"metric": Metric.AREA, "ids": (E1,)}, 6000.0),
        ({"metric": Metric.DIMENSION_VALUE, "ids": (E2,)}, 120.0),
    ],
    ids=lambda v: v["metric"].value if isinstance(v, dict) else str(v),
)
def test_every_metric_measures_the_milestone(fields: dict[str, object], actual: float) -> None:
    bus = milestone()
    passing = check(bus, expected=actual, tolerance=1e-9, **fields)
    assert (passing.passed, passing.actual, passing.error) == (True, actual, None)
    failing = check(bus, expected=actual + 1.0, tolerance=0.5, **fields)
    assert (failing.passed, failing.actual, failing.error) == (False, actual, None)


def test_the_tolerance_is_inclusive() -> None:
    bus = milestone()
    assert check(bus, metric=Metric.BBOX_WIDTH, expected=119.0, tolerance=1.0).passed
    assert not check(bus, metric=Metric.BBOX_WIDTH, expected=118.9, tolerance=1.0).passed


def test_a_circle_area_passes_within_tolerance() -> None:
    bus = bus_with(CreateCircle(center=ORIGIN, radius=10.0))
    result = check(bus, metric=Metric.AREA, ids=(E1,), expected=314.159, tolerance=1e-3)
    assert result.passed
    assert result.actual == pytest.approx(100 * math.pi)


@pytest.mark.parametrize(
    ("bus", "fields", "code", "field"),
    [
        (milestone, {"metric": "volume"}, ErrorCode.VALUE_OUT_OF_RANGE, "metric"),
        (
            milestone,
            {"metric": Metric.BBOX_WIDTH, "expected": math.nan},
            ErrorCode.VALUE_NOT_FINITE,
            "expected",
        ),
        (
            milestone,
            {"metric": Metric.BBOX_WIDTH, "tolerance": -1.0},
            ErrorCode.VALUE_OUT_OF_RANGE,
            "tolerance",
        ),
        (
            milestone,
            {"metric": Metric.BBOX_WIDTH, "tolerance": "tight"},
            ErrorCode.VALUE_WRONG_TYPE,
            "tolerance",
        ),
        (
            milestone,
            {"metric": Metric.DISTANCE, "refs": (corner(Feature.CENTER),)},
            ErrorCode.VALUE_OUT_OF_RANGE,
            "refs",
        ),
        (
            milestone,
            {
                "metric": Metric.DISTANCE,
                "refs": (corner(Feature.CENTER), Ref(entity=E3, feature=Feature.CENTER)),
            },
            ErrorCode.ENTITY_NOT_FOUND,
            "refs[1].entity",
        ),
        (
            milestone,
            {"metric": Metric.BBOX_WIDTH, "ids": (E2,)},
            ErrorCode.ENTITY_WRONG_KIND,
            "ids",
        ),
        (
            milestone,
            {"metric": Metric.DIMENSION_VALUE, "ids": ()},
            ErrorCode.VALUE_OUT_OF_RANGE,
            "ids",
        ),
        (
            milestone,
            {"metric": Metric.DIMENSION_VALUE, "ids": (E3,)},
            ErrorCode.ENTITY_NOT_FOUND,
            "ids",
        ),
        (
            milestone,
            {"metric": Metric.DIMENSION_VALUE, "ids": (E1,)},
            ErrorCode.ENTITY_WRONG_KIND,
            "ids",
        ),
        (Bus, {"metric": Metric.BBOX_WIDTH}, ErrorCode.SELECTION_EMPTY, None),
        (
            lambda: bus_with(CreateLine(start=ORIGIN, end=Point2(x=1.0, y=1.0))),
            {"metric": Metric.AREA, "ids": (E1,)},
            ErrorCode.PROFILE_NOT_CLOSED,
            "ids",
        ),
        (
            lambda: bus_with(CreateCircle(center=ORIGIN, radius=1.0), kernel=False),
            {"metric": Metric.AREA, "ids": (E1,)},
            ErrorCode.KERNEL_UNAVAILABLE,
            None,
        ),
    ],
    ids=[
        "unknown metric",
        "nan expected",
        "negative tolerance",
        "text tolerance",
        "one ref",
        "missing ref",
        "annotation bbox",
        "no dimension id",
        "missing dimension",
        "not a dimension",
        "empty document",
        "open profile",
        "no kernel",
    ],
)
def test_a_metric_that_cannot_be_evaluated_fails_with_the_reason(
    bus: object, fields: dict[str, object], code: ErrorCode, field: str | None
) -> None:
    result = check(bus(), **({"expected": 1.0, "tolerance": 0.0} | fields))  # type: ignore[operator]
    assert result.passed is False
    assert result.actual is None
    assert result.error is not None
    assert (result.error.code, result.error.field) == (code, field)
