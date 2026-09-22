"""Document model: the entities that make up a Caliper project. Frozen for V1.

Conventions:
- Lengths are millimetres and angles are degrees, both float.
- Coordinates are Y-up. Angles are measured counter-clockwise from +X.
- Everything here is an input: what the user, a script, or the AI specified.
  Derived values (arc endpoints, a driven dimension's measured value, anything a
  kernel computes) are never stored. See ADR 0005.
- Solved positions are inputs too (ADR 0009): when constraints move geometry, the
  document stores where it ended up, and that is the starting point of the next solve.
- Entities are immutable. Commands produce new Documents; nothing mutates one.
- Selection, hover, drawing previews, and constraint suggestions are UI state and never
  appear here.
- Every dataclass is keyword-only, so adding a field with a default later does not
  break existing callers.
- Ids sort as strings wherever order matters, so "e10" comes before "e2".

Frozen as of V1: a new entity kind or field needs a joint `contracts/` PR, and a file
format change on top of that (ADR 0005). V1.5 (`contracts/sketch-constraints`) added
`Point`, the `construction` flag, curve features, `Constraint`, driving dimension values,
and `AngleDimension` (file schema 2).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, NewType, Self

EntityId = NewType("EntityId", str)
"""Opaque and unique within a document, stable for the life of the entity.

The engine allocates ids like "e12". Callers may choose their own (e.g. "mounting_plate")
if they match ID_PATTERN.
"""

ID_PATTERN = r"[a-z][a-z0-9_]{0,63}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Point2:
    x: float
    y: float


# --- Geometry ---------------------------------------------------------------------------
# `construction` geometry is real geometry: it is solved, constrained, dimensioned, and
# picked like anything else. It is only left out of profiles (areas now, extrusions later).


@dataclass(frozen=True, slots=True, kw_only=True)
class Point:
    """A sketch point on its own, e.g. a hole centre or a symmetry target."""

    kind: ClassVar[str] = "point"
    position: Point2
    construction: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    kind: ClassVar[str] = "line"
    start: Point2
    end: Point2
    construction: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Circle:
    kind: ClassVar[str] = "circle"
    center: Point2
    radius: float
    construction: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Arc:
    """Runs counter-clockwise from `start_angle` through `sweep_angle`, with 0 < sweep < 360.

    `start_angle` is stored exactly as given and is not normalized, so 0 and 360 describe
    the same arc while comparing unequal. Callers that need two equal-looking arcs to
    compare equal send [0, 360). An angle the solver changes is written in [0, 360).
    """

    kind: ClassVar[str] = "arc"
    center: Point2
    radius: float
    start_angle: float
    sweep_angle: float
    construction: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Rectangle:
    """Axis-aligned. `corner` is the bottom-left (minimum x, minimum y) corner.

    A single entity rather than four lines, so it has a width to edit. Editing width or
    height keeps `corner` fixed. To the solver it is four unknowns (corner, width, height),
    so its sides are always horizontal and vertical.
    """

    kind: ClassVar[str] = "rectangle"
    corner: Point2
    width: float
    height: float
    construction: bool = False


Geometry = Point | Line | Circle | Arc | Rectangle


class Feature(StrEnum):
    """An addressable point or curve of a geometry entity.

    References use features, never raw coordinates, so a dimension or constraint follows
    its geometry. Point features name a location (a line's START); curve features name a
    whole curve (a line's CURVE, a rectangle's BOTTOM side). Adding members is a
    non-breaking change.
    """

    START = "start"
    END = "end"
    MID = "mid"
    CENTER = "center"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    TOP_RIGHT = "top_right"
    TOP_LEFT = "top_left"
    POINT = "point"
    """A Point entity's location."""
    CURVE = "curve"
    """The whole of a line, circle, or arc."""
    BOTTOM = "bottom"
    """A rectangle's sides. Each runs left to right (BOTTOM, TOP) or upward (LEFT, RIGHT)."""
    RIGHT = "right"
    TOP = "top"
    LEFT = "left"


POINT_FEATURES: Mapping[type[Geometry], frozenset[Feature]] = MappingProxyType(
    {
        Point: frozenset({Feature.POINT}),
        Line: frozenset({Feature.START, Feature.END, Feature.MID}),
        Circle: frozenset({Feature.CENTER}),
        Arc: frozenset({Feature.CENTER, Feature.START, Feature.END, Feature.MID}),
        Rectangle: frozenset(
            {
                Feature.CENTER,
                Feature.BOTTOM_LEFT,
                Feature.BOTTOM_RIGHT,
                Feature.TOP_RIGHT,
                Feature.TOP_LEFT,
            }
        ),
    }
)
"""Which point features each geometry type exposes."""

CURVE_FEATURES: Mapping[type[Geometry], frozenset[Feature]] = MappingProxyType(
    {
        Point: frozenset(),
        Line: frozenset({Feature.CURVE}),
        Circle: frozenset({Feature.CURVE}),
        Arc: frozenset({Feature.CURVE}),
        Rectangle: frozenset({Feature.BOTTOM, Feature.RIGHT, Feature.TOP, Feature.LEFT}),
    }
)
"""Which curve features each geometry type exposes. A Point has none."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Ref:
    """A feature of a geometry entity, e.g. the midpoint of line e3 or the curve of circle e4."""

    entity: EntityId
    feature: Feature


# --- Dimensions -------------------------------------------------------------------------
# A dimension with `value` None is driven: it displays what it measures. With a number it
# is driving: the solver moves geometry until the measurement equals it. Changing `value`
# with ModifyEntity is how a dimension edit resizes geometry. The seven kinds a user sees
# (length, distance, horizontal, vertical, radius, diameter, angle) are these three types;
# `Queries.dimension_type` names the kind, and `CreateDimension` infers it from a selection.


class DistanceOrientation(StrEnum):
    ALIGNED = "aligned"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


@dataclass(frozen=True, slots=True, kw_only=True)
class DistanceDimension:
    """Distance between two references: a line's length, or a distance between things.

    Each of `a` and `b` is a point feature or a straight curve (a line's CURVE or a
    rectangle side). Between two points it measures the full distance when ALIGNED, and the
    absolute horizontal or vertical component otherwise. A point and a straight curve give
    the perpendicular distance from the point to the curve's infinite line; two straight
    curves give the distance from `b`'s midpoint to `a`'s line (meant for parallel lines).
    Curve references are ALIGNED only.
    """

    kind: ClassVar[str] = "distance_dimension"
    a: Ref
    b: Ref
    orientation: DistanceOrientation
    offset: float
    """Placement only: how far the dimension line sits from the two points, in mm.

    Measured perpendicular to a→b, positive to the left of that direction, whatever the
    orientation. Nothing measured depends on it: `dimension_value` ignores it entirely.
    """
    value: float | None = None
    """None: driven. A number greater than 0: driving, in mm."""


class RadialMeasure(StrEnum):
    RADIUS = "radius"
    DIAMETER = "diameter"


@dataclass(frozen=True, slots=True, kw_only=True)
class RadialDimension:
    """Radius or diameter of a Circle or Arc."""

    kind: ClassVar[str] = "radial_dimension"
    target: EntityId
    measure: RadialMeasure
    label_angle: float
    """Placement only: direction from the center to the label."""
    value: float | None = None
    """None: driven. A number greater than 0: driving, in mm."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AngleDimension:
    """Angle between two straight curves (a line's CURVE or a rectangle side).

    Measures the angle between the two curves' directions (start to end, or as documented
    on `Feature` for rectangle sides), from 0 to 180. `supplementary` measures 180 minus
    that instead, which is how a dimension placed in the other pair of sectors between
    two crossing lines reads.
    """

    kind: ClassVar[str] = "angle_dimension"
    a: Ref
    b: Ref
    supplementary: bool = False
    offset: float
    """Placement only: distance from where the lines cross to the dimension arc, in mm.

    Negative places it in the opposite sector of the same angle. Nothing measured
    depends on it.
    """
    value: float | None = None
    """None: driven. A number strictly between 0 and 180: driving, in degrees."""


Annotation = DistanceDimension | RadialDimension | AngleDimension


# --- Constraints ------------------------------------------------------------------------


class ConstraintType(StrEnum):
    """A geometric relationship the solver keeps true. See `Constraint` for the references.

    Which references each type accepts is answered by `Queries.applicable_constraints`,
    which also says why a type doesn't apply to a selection.
    """

    COINCIDENT = "coincident"
    """Two points meet; a point lies on a curve; two lines are collinear; two circles or
    arcs share their circle."""
    CONCENTRIC = "concentric"
    """Circles, arcs, or a point share a centre."""
    HORIZONTAL = "horizontal"
    """A line, or two points, level with each other."""
    VERTICAL = "vertical"
    PARALLEL = "parallel"
    PERPENDICULAR = "perpendicular"
    TANGENT = "tangent"
    """A straight curve and a circle or arc, or two circles or arcs, touch without crossing."""
    EQUAL = "equal"
    """Two straight curves of equal length, or two circles or arcs of equal radius."""
    MIDPOINT = "midpoint"
    """A point at the middle of a line or arc."""
    SYMMETRIC = "symmetric"
    """Two points, or two circles or arcs, mirrored about a straight curve."""
    FIX = "fix"
    """Points or curves stay where they are."""
    NORMAL = "normal"
    """A line at right angles to a circle or arc: its infinite extension passes through the
    centre."""
    PIERCE = "pierce"
    """A point where a 3D curve crosses the sketch plane. Needs 3D geometry referenced from
    outside the sketch, which V1.5 doesn't have: always reported as unsupported."""
    CURVATURE = "curvature"
    """Two curves joined end to end with matching tangent and curvature (G2). Two lines
    become collinear and joined; two arcs share one circle and join. A line and an arc
    never can; splines, where it matters most, don't exist yet."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Constraint:
    """A geometric relationship between features, kept true by every command.

    The engine stores `refs` in its canonical order (what `applicable_constraints`
    returns). Where the order matters, the last reference is the one that moves when the
    constraint is added. There is no placement: where a glyph is drawn is UI state.
    """

    kind: ClassVar[str] = "constraint"
    type: ConstraintType
    refs: tuple[Ref, ...]


Entity = Geometry | Annotation | Constraint


# --- Document ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Document:
    """An immutable snapshot of a project.

    Iteration order of `entities` is unspecified. Anything needing an order
    (serialization, tie-breaking) sorts by id. `next_id` drives id allocation and is part
    of the document so replay allocates the same ids.
    """

    entities: Mapping[EntityId, Entity]
    next_id: int

    @classmethod
    def empty(cls) -> Self:
        return cls(entities=MappingProxyType({}), next_id=1)
