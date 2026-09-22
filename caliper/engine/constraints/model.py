"""The solver's view of geometry: unknowns per entity, and features as expressions of them.

Each geometry entity contributes its stored numbers as unknowns (a line's four endpoint
coordinates, a rectangle's corner, width and height). A `Frame` evaluates features at given
values as Duals, so equations get exact derivatives. Feature formulas match
`caliper/engine/queries.py` operation for operation, so a solved position is exactly where
queries say it is.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace

from caliper.contracts.document import (
    Arc,
    Circle,
    EntityId,
    Feature,
    Geometry,
    Line,
    Point,
    Point2,
    Rectangle,
    Ref,
)
from caliper.engine.constraints.ad import Dual, unit

PARAMS: Mapping[type[Geometry], tuple[str, ...]] = {
    Point: ("position.x", "position.y"),
    Line: ("start.x", "start.y", "end.x", "end.y"),
    Circle: ("center.x", "center.y", "radius"),
    Arc: ("center.x", "center.y", "radius", "start_angle", "sweep_angle"),
    Rectangle: ("corner.x", "corner.y", "width", "height"),
}
"""The unknowns of each geometry type, as field paths. Their count is its degrees of freedom."""

type P = tuple[Dual, Dual]


@dataclass(frozen=True, slots=True)
class Straight:
    """A line segment, or a rectangle side, from `a` to `b`."""

    a: P
    b: P


@dataclass(frozen=True, slots=True)
class Round:
    """A circle, or an arc when `start` and `sweep` (degrees) are set."""

    center: P
    radius: Dual
    start: Dual | None = None
    sweep: Dual | None = None


type Curve = Straight | Round


def read(entity: Geometry, path: str) -> float:
    name, _, axis = path.partition(".")
    value = getattr(entity, name)
    return float(getattr(value, axis) if axis else value)


def write(entity: Geometry, values: Mapping[str, float]) -> Geometry:
    """`entity` with the given unknowns replaced."""
    changes: dict[str, object] = {}
    for path, value in values.items():
        name, _, axis = path.partition(".")
        if axis:
            point = changes.get(name, getattr(entity, name))
            assert isinstance(point, Point2)
            changes[name] = replace(point, **{axis: value})
        else:
            changes[name] = value
    build: Callable[..., Geometry] = type(entity)
    return build(**({f.name: getattr(entity, f.name) for f in fields(entity)} | changes))


class Frame:
    """Geometry evaluated at one set of unknown values.

    `slots` maps each entity to the indices of its unknowns in `values`. Unknowns listed in
    `variables` carry a derivative; the rest are constants.
    """

    def __init__(
        self,
        kinds: Mapping[EntityId, type[Geometry]],
        slots: Mapping[EntityId, Sequence[int]],
        values: Sequence[float],
        variables: frozenset[int] | set[int] = frozenset(),
    ) -> None:
        self._kinds = kinds
        self._slots = slots
        self._duals = [
            Dual.variable(v, i) if i in variables else Dual(v) for i, v in enumerate(values)
        ]

    def kind(self, entity: EntityId) -> type[Geometry]:
        return self._kinds[entity]

    def _p(self, entity: EntityId) -> list[Dual]:
        return [self._duals[i] for i in self._slots[entity]]

    def point(self, ref: Ref) -> P:
        """A point feature. The reference must already be valid."""
        p = self._p(ref.entity)
        kind, feature = self._kinds[ref.entity], ref.feature
        if kind is Rectangle:
            x, y, w, h = p
            return {
                Feature.BOTTOM_LEFT: (x, y),
                Feature.BOTTOM_RIGHT: (x + w, y),
                Feature.TOP_RIGHT: (x + w, y + h),
                Feature.TOP_LEFT: (x, y + h),
                Feature.CENTER: (x + w / 2, y + h / 2),
            }[feature]
        if kind is Line:
            if feature is Feature.MID:
                return (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
            return (p[0], p[1]) if feature is Feature.START else (p[2], p[3])
        if kind is Arc and feature is not Feature.CENTER:
            angle = {
                Feature.START: p[3],
                Feature.END: p[3] + p[4],
                Feature.MID: p[3] + p[4] / 2,
            }[feature]
            u, v = unit(angle)
            return p[0] + p[2] * u, p[1] + p[2] * v
        return p[0], p[1]  # a Point's POINT, or a circle's or arc's CENTER

    def curve(self, ref: Ref) -> Curve:
        """A curve feature. The reference must already be valid."""
        p = self._p(ref.entity)
        kind = self._kinds[ref.entity]
        if kind is Circle:
            return Round((p[0], p[1]), p[2])
        if kind is Arc:
            return Round((p[0], p[1]), p[2], p[3], p[4])
        return self.straight(ref)

    def straight(self, ref: Ref) -> Straight:
        """A line's CURVE or a rectangle side. The reference must already be valid."""
        if self._kinds[ref.entity] is Line:
            p = self._p(ref.entity)
            return Straight((p[0], p[1]), (p[2], p[3]))
        first, second = _SIDES[ref.feature]
        return Straight(self.point(_at(ref.entity, first)), self.point(_at(ref.entity, second)))

    def defining(self, ref: Ref) -> list[Dual]:
        """Independent quantities that pin `ref` down, for Fix: one per degree of freedom."""
        if ref.feature is Feature.CURVE:
            return self._p(ref.entity)
        if ref.feature in _SIDES:
            side = self.straight(ref)
            if ref.feature in (Feature.BOTTOM, Feature.TOP):
                return [side.a[0], side.b[0], side.a[1]]
            return [side.a[1], side.b[1], side.a[0]]
        return list(self.point(ref))

    def slots(self, entity: EntityId) -> Sequence[int]:
        return self._slots[entity]


_SIDES: Mapping[Feature, tuple[Feature, Feature]] = {
    Feature.BOTTOM: (Feature.BOTTOM_LEFT, Feature.BOTTOM_RIGHT),
    Feature.TOP: (Feature.TOP_LEFT, Feature.TOP_RIGHT),
    Feature.LEFT: (Feature.BOTTOM_LEFT, Feature.TOP_LEFT),
    Feature.RIGHT: (Feature.BOTTOM_RIGHT, Feature.TOP_RIGHT),
}
"""Rectangle sides and their ends, in the direction documented on `Feature`."""


def _at(entity: EntityId, feature: Feature) -> Ref:
    return Ref(entity=entity, feature=feature)
