"""Short, human names for entities, used by the browser, history, and checks. Qt-free.

Sizes shown are the entity's stored inputs; anything measured (a line's length, a
dimension's value) comes from queries.
"""

from caliper.app.properties import format_number, ref_text
from caliper.contracts.document import (
    AngleDimension,
    Arc,
    Circle,
    Constraint,
    DistanceDimension,
    Entity,
    EntityId,
    Feature,
    Line,
    RadialDimension,
    RadialMeasure,
    Rectangle,
    Ref,
)
from caliper.contracts.errors import Error
from caliper.contracts.queries import Queries

ICON: dict[str, str] = {
    "line": "line",
    "circle": "circle",
    "arc": "arc",
    "rectangle": "rectangle",
    "distance_dimension": "dimension",
    "radial_dimension": "dimension",
    "angle_dimension": "angle",
    "point": "point",
    "constraint": "constraint",
}
"""An icon for every entity kind; `test_every_kind_has_an_icon` keeps it complete."""


def kind_title(entity: Entity) -> str:
    """Short enough for a narrow browser: "Distance", "Diameter", "Rectangle"."""
    match entity:
        case DistanceDimension():
            return "Distance"
        case RadialDimension(measure=measure):
            return measure.value.capitalize()
        case AngleDimension():
            return "Angle"
        case Constraint(type=type):
            return type.value.capitalize()
    return entity.kind.replace("_", " ").capitalize()


def n(value: float, digits: int = 3) -> str:
    return format_number(round(value, digits))


def summary(entity: Entity, id: EntityId, queries: Queries) -> str:
    match entity:
        case Rectangle(width=w, height=h):
            return f"{n(w)} \u00d7 {n(h)}"
        case Circle(radius=r):
            return f"⌀{n(2 * r)}"
        case Arc(radius=r, sweep_angle=sweep):
            return f"R{n(r)} · {n(sweep, 1)}°"
        case Line():
            distance = queries.measure_distance(
                Ref(entity=id, feature=Feature.START), Ref(entity=id, feature=Feature.END)
            )
            return "" if isinstance(distance, Error) else f"{n(distance.value)} long"
        case DistanceDimension(orientation=orientation):
            return f"{_value(entity, id, queries)} · {orientation.value}"
        case RadialDimension(measure=measure):
            sign = "⌀" if measure is RadialMeasure.DIAMETER else "R"
            return _value(entity, id, queries, prefix=sign)
        case AngleDimension():
            return _value(entity, id, queries, unit="°")
        case Constraint(refs=refs):
            return ", ".join(ref_text(ref) for ref in refs)
    return ""


def _value(
    entity: DistanceDimension | RadialDimension | AngleDimension,
    id: EntityId,
    queries: Queries,
    prefix: str = "",
    unit: str = "",
) -> str:
    """A dimension's value; in parentheses when it's driven, as on the canvas."""
    value = queries.dimension_value(id)
    shown = "?" if isinstance(value, Error) else f"{prefix}{n(value)}{unit}"
    return f"({shown})" if entity.value is None else shown
