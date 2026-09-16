"""Query and assertion API: how the UI and an AI agent inspect geometry. Freezes hard.

Designed alongside the commands, not after them (invariant 6). Queries are read-only and
headless, and return a value or an `Error`; invalid input never raises. A `Queries`
instance is bound to one immutable Document snapshot, so its answers never go stale
mid-computation.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from caliper.contracts.document import EntityId, Point2, Ref
from caliper.contracts.errors import Error

# --- Result values ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundingBox:
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def center(self) -> Point2:
        return Point2(x=(self.x_min + self.x_max) / 2, y=(self.y_min + self.y_max) / 2)


@dataclass(frozen=True, slots=True, kw_only=True)
class Distance:
    value: float
    """Euclidean distance."""
    dx: float
    """b.x - a.x"""
    dy: float
    """b.y - a.y"""


@dataclass(frozen=True, slots=True, kw_only=True)
class AreaProperties:
    area: float
    """mm²"""
    centroid: Point2
    ixx: float
    """Second moment of area about the centroid's x axis, mm⁴."""
    iyy: float
    """Second moment of area about the centroid's y axis, mm⁴."""
    ixy: float
    """Product of inertia about the centroid, mm⁴."""


# --- Assertions -------------------------------------------------------------------------


class Metric(StrEnum):
    """What an Expectation measures, and which inputs it reads."""

    DISTANCE = "distance"
    """refs=(a, b)"""
    DISTANCE_X = "distance_x"
    """refs=(a, b); absolute horizontal distance"""
    DISTANCE_Y = "distance_y"
    """refs=(a, b); absolute vertical distance"""
    BBOX_WIDTH = "bbox_width"
    """ids; empty means the whole document"""
    BBOX_HEIGHT = "bbox_height"
    """ids; empty means the whole document"""
    AREA = "area"
    """ids forming one closed profile"""
    DIMENSION_VALUE = "dimension_value"
    """ids=(dimension,)"""


@dataclass(frozen=True, slots=True, kw_only=True)
class Expectation:
    """A numeric claim about the document that can be checked headlessly.

    Plain data, so bench cases, tests, and the AI loop can all write and store them.
    """

    metric: Metric
    expected: float
    tolerance: float
    refs: tuple[Ref, ...] = ()
    ids: tuple[EntityId, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckResult:
    expectation: Expectation
    passed: bool
    actual: float | None
    """None if the metric could not be evaluated; see `error`."""
    error: Error | None = None


# --- Protocol ---------------------------------------------------------------------------


class Queries(Protocol):
    def feature_point(self, ref: Ref) -> Point2 | Error:
        """Location of a feature, e.g. the center of a rectangle."""
        ...

    def measure_distance(self, a: Ref, b: Ref) -> Distance | Error: ...

    def bounding_box(self, ids: Sequence[EntityId] = ()) -> BoundingBox | Error:
        """Tight bounds of geometry entities. Empty `ids` means the whole document."""
        ...

    def entity_at_point(self, point: Point2, tolerance: float) -> EntityId | None:
        """What a click at `point` picks, or None.

        An outline within `tolerance` (mm) wins first: nearest, then lowest id. Otherwise a
        closed shape containing the point wins, smallest first, then lowest id, so clicking
        inside a rectangle or circle selects it. Lines and arcs enclose nothing.

        Geometry only: annotation hit-testing depends on rendered text size and belongs
        to the shell.
        """
        ...

    def entities_in_box(self, box: BoundingBox, *, crossing: bool) -> tuple[EntityId, ...]:
        """Geometry entities fully inside `box`, or also touching it if `crossing`. Sorted by id.

        A crossing box also takes a closed shape whose inside it touches, matching
        `entity_at_point`.
        """
        ...

    def nearest_feature(self, point: Point2, tolerance: float) -> Ref | None:
        """Closest feature within `tolerance` (mm), for snapping and attaching dimensions."""
        ...

    def dimension_value(self, id: EntityId) -> float | Error:
        """The measured value a driven dimension displays."""
        ...

    def area_properties(self, ids: Sequence[EntityId]) -> AreaProperties | Error:
        """Area properties of the region bounded by `ids`. V1: exactly one Rectangle or Circle."""
        ...

    def check(self, expectation: Expectation) -> CheckResult: ...
