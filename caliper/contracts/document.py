"""Document model: a part, its features in order, and the entities its sketches hold.

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
and `AngleDimension` (file schema 2). C-1 (`contracts/checks-authors-labels`) moved
`Expectation` here from the queries: a check is stored in the document (file schema 3).
V2's F1 (ADR 0011) made the document a part: `Document.features` in order, `Sketch` on a
`Plane`, and each geometry entity's `sketch` (file schema 4). V2's F3 (ADR 0013) added the
second feature, `Extrude`, and `Metric.VOLUME`. ADR 0016 (`contracts/sketch-on-faces`) added
`FaceRef`, a sketch on a face, and `Extrude.reversed` (file schema 5).
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


# --- Features ---------------------------------------------------------------------------
# A document is one part: its features, in the order they are recomputed, each able to read
# only the features before it (ADR 0011). A sketch is the first kind; an extrude comes next
# (V2, F3). Features live in `Document.features`, not in `Document.entities`.


class Plane(StrEnum):
    """One of the part's three origin planes, with a sketch's x and y axes fixed on it.

    | Plane | Sketch x | Sketch y | Normal (x cross y) |
    | XY    | +X       | +Y       | +Z                 |
    | XZ    | +X       | +Z       | -Y                 |
    | YZ    | +Y       | +Z       | +X                 |

    Every plane passes through the part's origin. A sketch on a face of the part names it with
    `FaceRef` instead (ADR 0016); offset planes come later.
    """

    XY = "xy"
    XZ = "xz"
    YZ = "yz"


FACE_PATTERN = rf"start|end|side {ID_PATTERN}(\.(bottom|right|top|left))?"
"""A face's name (ADR 0014): `start`, the cap on its extrude's sketch plane; `end`, the cap at
its depth; `side <id>`, the side swept from line <id>; `side <id>.<side>`, one side of
rectangle <id> (`bottom`, `right`, `top`, or `left`)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FaceRef:
    """A flat face of an extrude, by what made it, for a sketch to sit on (ADR 0016).

    `face` is one of `FACE_PATTERN`'s names. Where it is comes from the extrude's inputs, never
    from a kernel, so a sketch on it follows edits to them: change the extrude's depth and the
    sketch on its `end` moves with it. The sketch's normal points out of the part there, and
    its x is level (+X on a level face), its y up the face. A value, like `Ref`: it has no
    `kind`."""

    feature: EntityId
    """An extrude before the sketch."""
    face: str


FIRST_SKETCH = EntityId("e0")
"""The sketch a new part starts with, on XY. The engine allocates ids from 1, so `e0` is never
handed out to anything else, and a V1 file migrates into it with its ids unchanged."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Sketch:
    """A sketch placed on a plane, or on a flat face of the part (ADR 0016). Its geometry names
    it (`sketch`); its dimensions and constraints are in it through the geometry they refer
    to; its coordinates are 2D, along the plane's or face's axes."""

    kind: ClassVar[str] = "sketch"
    id: EntityId
    """Unique across the document's features and entities."""
    plane: Plane | FaceRef


class ExtrudeOperation(StrEnum):
    """What an extrude does to the part's solid."""

    ADD = "add"
    """Join it on. The part's first extrude makes the solid."""
    REMOVE = "remove"
    """Cut it away, as a pocket or a hole through."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Extrude:
    """A profile drawn in `sketch`, swept `depth` mm along the sketch plane's normal, and added
    to or cut from the part's solid (ADR 0013).

    The profile is the geometry in `ids`, or with none given, every geometry entity in the
    sketch that isn't construction geometry. It must be one closed profile with any holes
    (`caliper.engine.profiles`) when the extrude is made. A later edit to the sketch that
    breaks it makes the extrude fail, with the reason, rather than being refused; what the
    extrude made is never stored, only recomputed (ADR 0005). `sketch` must come before the
    extrude in the part's features.
    """

    kind: ClassVar[str] = "extrude"
    id: EntityId
    sketch: EntityId
    depth: float
    """Greater than 0, in mm, along the normal of the sketch's plane, or against it when
    `reversed`."""
    operation: ExtrudeOperation = ExtrudeOperation.ADD
    ids: tuple[EntityId, ...] = ()
    """The profile's geometry; empty means all of the sketch's non-construction geometry."""
    reversed: bool = False
    """Swept against the sketch plane's normal (ADR 0016): from a face, a cut goes into the
    part this way."""


PartFeature = Sketch | Extrude
"""Every kind of part feature, in `Document.features`."""


# --- Geometry ---------------------------------------------------------------------------
# `construction` geometry is real geometry: it is solved, constrained, dimensioned, and
# picked like anything else. It is only left out of profiles (areas now, extrusions later).
# `sketch` is the sketch the geometry is drawn in, and its coordinates are in that sketch's
# plane. It can't be changed once the geometry exists (ADR 0011).


@dataclass(frozen=True, slots=True, kw_only=True)
class Point:
    """A sketch point on its own, e.g. a hole centre or a symmetry target."""

    kind: ClassVar[str] = "point"
    position: Point2
    construction: bool = False
    sketch: EntityId = FIRST_SKETCH


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    kind: ClassVar[str] = "line"
    start: Point2
    end: Point2
    construction: bool = False
    sketch: EntityId = FIRST_SKETCH


@dataclass(frozen=True, slots=True, kw_only=True)
class Circle:
    kind: ClassVar[str] = "circle"
    center: Point2
    radius: float
    construction: bool = False
    sketch: EntityId = FIRST_SKETCH


@dataclass(frozen=True, slots=True, kw_only=True)
class Arc:
    """Runs counter-clockwise from `start_angle` through `sweep_angle`, with 0 < sweep < 360.

    The engine stores `start_angle` in [0, 360). A command or file may give any finite
    angle, and it is reduced to the same direction in that range (-90 becomes 270, 360
    becomes 0), so two arcs that look the same compare equal. The resolved command carries
    the stored angle. `sweep_angle` is never reduced.
    """

    kind: ClassVar[str] = "arc"
    center: Point2
    radius: float
    start_angle: float
    sweep_angle: float
    construction: bool = False
    sketch: EntityId = FIRST_SKETCH


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
    sketch: EntityId = FIRST_SKETCH


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

    The measured direction is a→b for ALIGNED (+X if the two points coincide), +X for
    HORIZONTAL, and +Y for VERTICAL. The offset is measured from the midpoint of a and b,
    along that direction turned 90° counter-clockwise: a positive offset puts a horizontal
    dimension above its points, a vertical one to their left, and an aligned one on the left
    of a→b. Nothing measured depends on it: `dimension_value` ignores it entirely.

    (Until 2026-09-29 this said perpendicular to a→b whatever the orientation, which the app
    never drew by and which loses a horizontal or vertical label's placement: C-3, #28.)
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


# --- Checks -----------------------------------------------------------------------------
# A check is a requirement the user or the AI wrote down: a numeric claim about the sketch,
# checked headlessly by `Queries.check`. It is stored like any other entity, so it is saved
# with the project and added, edited, removed, and undone by commands (ADR 0010). It never
# moves geometry, and nothing refers to it. It refers to what it measures, but deleting that
# leaves the check in place, failing, rather than deleting the requirement with it.


class Metric(StrEnum):
    """What an Expectation measures, and which inputs it reads."""

    DISTANCE = "distance"
    """refs=(a, b)"""
    DISTANCE_X = "distance_x"
    """refs=(a, b); absolute horizontal distance"""
    DISTANCE_Y = "distance_y"
    """refs=(a, b); absolute vertical distance"""
    POSITION_X = "position_x"
    """refs=(point,); the point feature's x coordinate, signed, from the origin"""
    POSITION_Y = "position_y"
    """refs=(point,); the point feature's y coordinate, signed, from the origin"""
    BBOX_WIDTH = "bbox_width"
    """ids; empty means the whole document"""
    BBOX_HEIGHT = "bbox_height"
    """ids; empty means the whole document"""
    AREA = "area"
    """ids forming one closed profile"""
    DIMENSION_VALUE = "dimension_value"
    """ids=(dimension,)"""
    VOLUME = "volume"
    """mm³ of the part's solid: ids empty for the whole part, or one feature's id for the part
    as it stands after that feature (V2)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Expectation:
    """A numeric claim about the document that can be checked headlessly: a check.

    Plain data, so bench cases, tests, and the AI loop can all write them. Stored in the
    document as a "check" entity (`CreateCheck`); one that isn't stored is just as valid an
    argument to `Queries.check`.
    """

    kind: ClassVar[str] = "check"
    metric: Metric
    expected: float
    tolerance: float
    """Passes when |actual - expected| <= tolerance. 0 or more."""
    refs: tuple[Ref, ...] = ()
    ids: tuple[EntityId, ...] = ()


Entity = Geometry | Annotation | Constraint | Expectation


# --- Document ---------------------------------------------------------------------------


ONE_SKETCH: tuple[PartFeature, ...] = (Sketch(id=FIRST_SKETCH, plane=Plane.XY),)
"""The features of a new part: one sketch on XY."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Document:
    """An immutable snapshot of a project: one part (ADR 0011).

    `entities` holds what the part's sketches hold (geometry, dimensions, constraints) and
    the part's checks. Its iteration order is unspecified; anything needing an order
    (serialization, tie-breaking) sorts by id. `features` holds the part's features in the
    order they are recomputed. Ids are unique across both. `next_id` drives id allocation
    and is part of the document so replay allocates the same ids.

    `features` defaults to one sketch on XY, `FIRST_SKETCH`, which is what a V1 document
    is. Code that builds a document from another one keeps the other's features
    (`dataclasses.replace`).
    """

    entities: Mapping[EntityId, Entity]
    next_id: int
    features: tuple[PartFeature, ...] = ONE_SKETCH

    @classmethod
    def empty(cls) -> Self:
        """A new part: one sketch on XY, nothing drawn in it."""
        return cls(entities=MappingProxyType({}), next_id=1)
