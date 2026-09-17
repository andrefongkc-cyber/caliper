"""Document model: the entities that make up a Caliper project. Frozen for V1.

Conventions:
- Lengths are millimetres and angles are degrees, both float.
- Coordinates are Y-up. Angles are measured counter-clockwise from +X.
- Everything here is an input: what the user, a script, or the AI specified.
  Derived values (arc endpoints, a driven dimension's measured value, anything a
  kernel computes) are never stored. See ADR 0005.
- Entities are immutable. Commands produce new Documents; nothing mutates one.
- Selection, hover, and drawing previews are UI state and never appear here.
- Every dataclass is keyword-only, so adding a field with a default later does not
  break existing callers.
- Ids sort as strings wherever order matters, so "e10" comes before "e2".

Frozen as of V1: a new entity kind or field needs a joint `contracts/` PR, and a file
format change on top of that (ADR 0005).
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


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    kind: ClassVar[str] = "line"
    start: Point2
    end: Point2


@dataclass(frozen=True, slots=True, kw_only=True)
class Circle:
    kind: ClassVar[str] = "circle"
    center: Point2
    radius: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Arc:
    """Runs counter-clockwise from `start_angle` through `sweep_angle`, with 0 < sweep < 360.

    `start_angle` is stored exactly as given and is not normalized, so 0 and 360 describe
    the same arc while comparing unequal. Callers that need two equal-looking arcs to
    compare equal send [0, 360).
    """

    kind: ClassVar[str] = "arc"
    center: Point2
    radius: float
    start_angle: float
    sweep_angle: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Rectangle:
    """Axis-aligned. `corner` is the bottom-left (minimum x, minimum y) corner.

    A single entity rather than four lines, so it has a width to edit. Editing width or
    height keeps `corner` fixed.
    """

    kind: ClassVar[str] = "rectangle"
    corner: Point2
    width: float
    height: float


Geometry = Line | Circle | Arc | Rectangle


class Feature(StrEnum):
    """An addressable point on a geometry entity.

    References use features, never raw coordinates, so a dimension follows its geometry
    and V1.5 constraints can attach to the same targets. Edge features arrive with
    constraints; adding members is a non-breaking change.
    """

    START = "start"
    END = "end"
    MID = "mid"
    CENTER = "center"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    TOP_RIGHT = "top_right"
    TOP_LEFT = "top_left"


POINT_FEATURES: Mapping[type[Geometry], frozenset[Feature]] = MappingProxyType(
    {
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
"""Which features each geometry type exposes."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Ref:
    """A feature of a geometry entity, e.g. the midpoint of line e3."""

    entity: EntityId
    feature: Feature


# --- Annotations ------------------------------------------------------------------------


class DistanceOrientation(StrEnum):
    ALIGNED = "aligned"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


@dataclass(frozen=True, slots=True, kw_only=True)
class DistanceDimension:
    """Driven dimension between two point features.

    V1 dimensions display a measurement and never change geometry, so the value is
    computed, not stored. V1.5 promotes dimensions to driving constraints.
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


class RadialMeasure(StrEnum):
    RADIUS = "radius"
    DIAMETER = "diameter"


@dataclass(frozen=True, slots=True, kw_only=True)
class RadialDimension:
    """Driven radius or diameter of a Circle or Arc."""

    kind: ClassVar[str] = "radial_dimension"
    target: EntityId
    measure: RadialMeasure
    label_angle: float
    """Placement only: direction from the center to the label."""


Annotation = DistanceDimension | RadialDimension

Entity = Geometry | Annotation


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
