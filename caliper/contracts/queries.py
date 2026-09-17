"""Query and assertion API: how the UI and an AI agent inspect geometry. Frozen for V1.

Designed alongside the commands, not after them (invariant 6). Queries are read-only and
headless, and return a value or an `Error`; invalid input never raises. A `Queries`
instance is bound to one immutable Document snapshot, so its answers never go stale
mid-computation.

Frozen as of V1: changing anything here needs a joint `contracts/` PR. Two conventions
hold throughout:

- **Ids sort as strings,** so "e10" comes before "e2". Every "lowest id" and "sorted by
  id" below means that order, not numeric order.
- **The pickers return no match rather than an error.** `entity_at_point`,
  `nearest_feature` and `entities_in_box` return None or an empty tuple for input they
  can't use (a non-finite point, a negative or non-finite tolerance, a box that isn't
  finite or has min above max), because their return types carry no `Error`.
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
        """Location of a feature, e.g. the center of a rectangle.

        A reference that names no entity, an annotation, or a feature the entity doesn't
        have is reported the way a dimension command reports it: `entity.not_found` or
        `entity.wrong_kind` on `ref.entity`, `reference.invalid_feature` on `ref.feature`.
        """
        ...

    def measure_distance(self, a: Ref, b: Ref) -> Distance | Error:
        """Distance from `a` to `b`, with the signed dx and dy. `a` and `b` may be equal.

        A bad reference is reported as in `feature_point`, with `a.` or `b.` as the field.
        """
        ...

    def bounding_box(self, ids: Sequence[EntityId] = ()) -> BoundingBox | Error:
        """Tight bounds of geometry entities. Empty `ids` means the whole document.

        Annotations are never included, even when named: what a dimension measures is the
        geometry, and where its label sits depends on rendered text size. A view that must
        not clip labels adds their extents itself.

        Repeating an id is allowed. Otherwise: `selection.empty` when the document holds no
        geometry at all, `entity.not_found` for an unknown id, `entity.wrong_kind` for an
        annotation, each with `field="ids"` and the offending id in the message.
        """
        ...

    def entity_at_point(self, point: Point2, tolerance: float) -> EntityId | None:
        """What a click at `point` picks, or None.

        An outline within `tolerance` (mm) wins first: nearest, then lowest id. Otherwise a
        closed shape containing the point wins, smallest first (by area), then lowest id, so
        clicking inside a rectangle or circle selects it. Lines and arcs enclose nothing.

        Geometry only: annotation hit-testing depends on rendered text size and belongs
        to the shell.
        """
        ...

    def entities_in_box(self, box: BoundingBox, *, crossing: bool) -> tuple[EntityId, ...]:
        """Geometry entities fully inside `box`, or also touching it if `crossing`. Sorted by id.

        "Fully inside" is measured on an entity's bounds; "touching" on its outline, plus the
        inside of a circle or rectangle, matching `entity_at_point`. Annotations are never
        returned.
        """
        ...

    def nearest_feature(self, point: Point2, tolerance: float) -> Ref | None:
        """Closest feature within `tolerance` (mm), for snapping and attaching dimensions.

        Distance is measured to the feature points themselves, not to outlines, so a point
        near an edge but far from its ends is no match. Ties go to the lowest id.
        """
        ...

    def dimension_value(self, id: EntityId) -> float | Error:
        """The measured value a driven dimension displays.

        A distance dimension measures between its two feature points: the full distance when
        ALIGNED, the absolute horizontal or vertical component otherwise. A radial dimension
        gives the radius or the diameter. `entity.not_found` or `entity.wrong_kind` on `id`
        when it isn't a dimension.
        """
        ...

    def area_properties(self, ids: Sequence[EntityId]) -> AreaProperties | Error:
        """Area properties of the region bounded by `ids`. V1: exactly one Rectangle or Circle.

        The only query that needs a geometry kernel, so it is the only one that can report
        `kernel.unavailable` — when no kernel is configured, or the optional OCCT extra is
        not installed. `selection.empty` for no ids, `profile.not_closed` for anything that
        isn't one closed profile. `ixx` and `iyy` are about the centroid, and `ixy` is the
        product of inertia in the usual engineering sense (∫xy dA).
        """
        ...

    def check(self, expectation: Expectation) -> CheckResult:
        """Evaluate one numeric claim. Never raises, and never reports an error separately.

        A claim that can't be evaluated comes back as a failed CheckResult with `actual`
        None and the reason in `error`: a metric that isn't a Metric, a non-finite
        `expected`, a negative or non-finite `tolerance`, the wrong number of `refs` or
        `ids`, or whatever the underlying query reported. Comparison is inclusive:
        |actual - expected| <= tolerance.
        """
        ...
