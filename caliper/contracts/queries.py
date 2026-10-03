"""Query and assertion API: how the UI and an AI agent inspect geometry. Frozen for V1.

Designed alongside the commands, not after them (invariant 6). Queries are read-only and
headless, and return a value or an `Error`; invalid input never raises. A `Queries`
instance is bound to one immutable Document snapshot, so its answers never go stale
mid-computation.

Frozen as of V1: changing anything here needs a joint `contracts/` PR. V1.5 added the
constraint queries (`solve_status`, `applicable_constraints`, `infer_dimension`,
`dimension_type`, `suggest_constraints`, `constraints_on`) and `reference_at_point`. The
post-V1 fixes added the position metrics (`Metric.POSITION_X`, `Metric.POSITION_Y`), and
C-1 moved `Metric` and `Expectation` to the document contract, since checks are stored now;
they are still importable from here. V2's F1 (ADR 0011) added `sketch_of`, and measurements
read one sketch: a 2D distance, box, or area across two planes means nothing, so mixing
sketches is `sketch.mixed`. The pickers and `solve_status` cover the whole part. V2's F3
(ADR 0013) added the part's solid: `solid_properties`, `mesh`, and `feature_error`. ADR 0016
moved `Frame` here from the kernel contract, and added `plane_frame` and `faces`.
Two conventions hold throughout:

- **Ids sort as strings,** so "e10" comes before "e2". Every "lowest id" and "sorted by
  id" below means that order, not numeric order.
- **The pickers return no match rather than an error.** `entity_at_point`,
  `nearest_feature` and `entities_in_box` return None or an empty tuple for input they
  can't use (a non-finite point, a negative or non-finite tolerance, a box that isn't
  finite or has min above max), because their return types carry no `Error`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from caliper.contracts.document import ConstraintType, EntityId, FaceRef, Plane, Point2, Ref
from caliper.contracts.document import Expectation as Expectation  # moved there (C-1)
from caliper.contracts.document import Metric as Metric
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


# --- 3D (V2) ----------------------------------------------------------------------------
# Solids are derived: worked out by a geometry kernel from the part's features and never stored
# (ADR 0005). These are what queries give back about them.


@dataclass(frozen=True, slots=True, kw_only=True)
class Point3:
    """A point or a direction in the part's 3D space, in mm, Z up from the XY plane."""

    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Frame:
    """Where a face drawn in 2D sits in 3D: a 2D point (u, v) is `origin + u * x + v * y`.

    `x` and `y` are unit directions at right angles; the normal, the way an extrusion goes, is
    x cross y. A sketch's plane gives its frame. It moved here from the kernel contract (ADR
    0016), which still exports it, because `Queries.plane_frame` gives one back.
    """

    origin: Point3
    x: Point3
    y: Point3


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundingBox3:
    x_min: float
    y_min: float
    z_min: float
    x_max: float
    y_max: float
    z_max: float


@dataclass(frozen=True, slots=True, kw_only=True)
class SolidProperties:
    volume: float
    """mm³. 0 for a solid that is nothing, such as one cut away entirely."""
    bounding_box: BoundingBox3 | None
    """None when there's nothing to bound."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Mesh:
    """Triangles approximating a solid's surface, for drawing it. Never stored.

    Each triangle is three indexes into `vertices`, counter-clockwise seen from outside the
    solid, so its normal (b - a) x (c - a) points out. Faces don't share vertices, so each
    face can be lit flat or smooth on its own.
    """

    vertices: tuple[Point3, ...]
    triangles: tuple[tuple[int, int, int], ...]


# --- Assertions -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckResult:
    expectation: Expectation
    passed: bool
    actual: float | None
    """None if the metric could not be evaluated; see `error`."""
    error: Error | None = None


# --- Constraints and dimensions ---------------------------------------------------------


class DimensionType(StrEnum):
    """The seven dimension kinds a user sees, whichever entity type stores them."""

    LENGTH = "length"
    """A line or rectangle side, end to end."""
    DISTANCE = "distance"
    """Point to point (aligned), point to line, or between parallel lines."""
    HORIZONTAL_DISTANCE = "horizontal_distance"
    VERTICAL_DISTANCE = "vertical_distance"
    RADIUS = "radius"
    DIAMETER = "diameter"
    ANGLE = "angle"


class ConstraintState(StrEnum):
    UNDER = "under"
    """Some geometry can still move: `dof` > 0."""
    FULLY = "fully"
    """Nothing can move without breaking a constraint."""
    OVER = "over"
    """Every constraint holds, but some repeat what others already say."""
    CONFLICTING = "conflicting"
    """Some constraints don't hold. Commands never leave a document like this; a file edited
    by hand, or solved elsewhere beyond tolerance, can arrive like it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SolveStatus:
    """Degrees of freedom and constraint health of the whole document."""

    state: ConstraintState
    dof: int
    """Remaining degrees of freedom: unknowns minus independent constraint equations."""
    entity_dof: Mapping[EntityId, int]
    """Every geometry entity's remaining degrees of freedom; 0 means fully constrained."""
    conflicting: tuple[EntityId, ...]
    """Constraints and driving dimensions that don't hold, sorted."""
    redundant: tuple[EntityId, ...]
    """Constraints and driving dimensions implied by others, sorted."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ConstraintOption:
    """Whether one constraint or dimension type applies to a selection, and how."""

    type: ConstraintType | DimensionType
    refs: tuple[Ref, ...]
    """The selection in the order the engine stores it, ready for `CreateConstraint`."""
    error: Error | None = None
    """None when it applies; otherwise why not."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Suggestion:
    """A constraint the geometry nearly satisfies. Not a constraint until someone adds it.

    Accepting means sending `CreateConstraint(type=..., refs=...)`. Rejecting, ignoring,
    or switching suggestions off is UI or agent state; the document never records it.
    """

    type: ConstraintType
    refs: tuple[Ref, ...]
    deviation: float
    """How far from exact: mm for positions, degrees for directions."""


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

        A bad reference is reported as in `feature_point`, with `a.` or `b.` as the field;
        references in two sketches as `sketch.mixed`.
        """
        ...

    def bounding_box(self, ids: Sequence[EntityId] = ()) -> BoundingBox | Error:
        """Tight bounds of geometry entities, in their sketch's plane. Empty `ids` means all
        the geometry, which must then be in one sketch (`sketch.mixed` otherwise).

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
        """What a dimension measures on the stored geometry, driven or driving.

        A distance dimension measures as documented on `DistanceDimension`, a radial one the
        radius or diameter, an angle one in degrees. For a driving dimension this equals its
        `value` once solved. `entity.not_found` or `entity.wrong_kind` on `id` when it
        isn't a dimension.
        """
        ...

    def area_properties(self, ids: Sequence[EntityId]) -> AreaProperties | Error:
        """Area properties of the region bounded by `ids`: one closed profile.

        A profile is one outline and any holes inside it. Each is a Rectangle or Circle, or
        lines and arcs joined end to end (in any order and direction; ends join within
        `tolerance.BROKEN` of the profile's size). Boundaries may meet only end to end, and an
        island inside a hole would be a second profile (N4; V1 took one Rectangle or Circle).

        The only query that needs a geometry kernel, so it is the only one that can report
        `kernel.unavailable` — when no kernel is configured, or the optional OCCT extra is
        not installed. `selection.empty` for no ids, `profile.not_closed` for anything that
        isn't one closed profile (an open end, a branch, crossing boundaries, two regions),
        with the reason and the ids involved, `geometry.degenerate` for a zero-length edge,
        `profile.construction` for construction geometry. `ixx` and `iyy` are about the
        centroid, and `ixy` is the product of inertia in the usual engineering sense (∫xy dA).
        """
        ...

    def reference_at_point(self, point: Point2, tolerance: float) -> Ref | None:
        """The point or curve feature a click at `point` picks, for building a selection.

        A point feature within `tolerance` (mm) wins, as in `nearest_feature`. Otherwise
        the nearest curve within `tolerance`: a line, circle, or arc's CURVE, or a
        rectangle's side; ties go to the lowest id. Invalid input matches nothing.
        """
        ...

    def solve_status(self) -> SolveStatus:
        """Degrees of freedom and constraint health, computed from the stored geometry."""
        ...

    def applicable_constraints(self, refs: Sequence[Ref]) -> tuple[ConstraintOption, ...]:
        """One option per ConstraintType, then one per DimensionType, in enum order.

        An option with `error` None can be created from `refs` as given. Otherwise `error`
        says why not: `constraint.not_applicable` for the wrong kind or number of
        references, `constraint.unsupported` for Pierce, or the reference error
        (`entity.not_found`, `reference.invalid_feature`) for a bad reference.
        """
        ...

    def infer_dimension(self, refs: Sequence[Ref], placement: Point2) -> DimensionType | Error:
        """The dimension `CreateDimension(refs, placement)` would make, for a live preview.

        Decided by what is selected and where the label goes, as in Onshape: one line is a
        length, or its horizontal or vertical extent when placed above/below or beside it;
        two points likewise; a point and a line, or two parallel lines, a distance; two
        other lines an angle; a circle a diameter, or a radius when placed inside it; an
        arc a radius. Circles and arcs paired with anything measure from their centres.
        """
        ...

    def dimension_type(self, id: EntityId) -> DimensionType | Error:
        """Which of the seven kinds a dimension entity is."""
        ...

    def suggest_constraints(
        self, ids: Sequence[EntityId] = (), *, tolerance: float, angle_tolerance: float = 1.0
    ) -> tuple[Suggestion, ...]:
        """Constraints the geometry nearly satisfies but doesn't have yet.

        Considers horizontal, vertical, coincident, midpoint, parallel, perpendicular, and
        tangent. Only relationships involving `ids` (all geometry when empty), within
        `tolerance` mm or `angle_tolerance` degrees. Skips what existing constraints
        already say or imply. Sorted by deviation, then type, then references.
        """
        ...

    def solid_properties(self, ids: Sequence[EntityId] = ()) -> SolidProperties | Error:
        """The part's solid: empty `ids` for the whole part after its last feature, or one
        feature's id for the part as it stands after that feature.

        Worked out by the geometry kernel and never stored; recomputed only where what a
        feature reads has changed. `kernel.unavailable` without one. `selection.empty` when no
        feature has made a solid yet. A feature that fails gives its own error (the profile's
        `profile.not_closed`, a kernel's `kernel.unsupported`), and every feature after it
        `feature.failed`.
        """
        ...

    def mesh(self, ids: Sequence[EntityId] = (), tolerance: float = 0.05) -> Mesh | Error:
        """Triangles for drawing the part's solid, within `tolerance` mm of its surface. `ids`
        and the errors as for `solid_properties`; a solid that is nothing has no triangles."""
        ...

    def feature_error(self, id: EntityId) -> Error | None:
        """Why a feature fails, or None when it works or `id` isn't a feature. A sketch fails
        when its face can't be found (ADR 0016), with no kernel needed to say so; an extrude
        fails when its profile isn't one closed profile any more, its sketch geometry is gone,
        its sketch fails, or the kernel can't build it."""
        ...

    def plane_frame(self, plane: Plane | FaceRef) -> Frame | Error:
        """Where a plane or a face is in the part: the frame a sketch on it draws in (ADR
        0016). A face's is worked out from its extrude's inputs, with no kernel; it is
        `face.not_found` or `face.not_planar` when the extrude has no such flat face now, and
        the error of the extrude's own sketch when that fails."""
        ...

    def faces(self, id: EntityId) -> tuple[FaceRef, ...] | Error:
        """The flat faces of extrude `id` a sketch can sit on: `start`, `end`, then each line's
        and rectangle's sides in the profile's order. `entity.not_found` for an id the part
        doesn't have, `entity.wrong_kind` for one that isn't an extrude, and the profile's
        error when it isn't one closed profile now."""
        ...

    def sketch_of(self, id: EntityId) -> EntityId | None:
        """The sketch an entity is in: geometry's own `sketch`, and a dimension's or
        constraint's, the sketch of the geometry it refers to. None for a check (it belongs
        to the part), a feature, or an id the document doesn't have."""
        ...

    def constraints_on(self, ids: Sequence[EntityId]) -> tuple[EntityId, ...]:
        """Constraints and dimensions referring to any of `ids`, sorted. Unknown ids match
        nothing."""
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
