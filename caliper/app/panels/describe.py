"""Short, human names for entities, used by the browser, history, and checks. Qt-free.

Sizes shown are the entity's stored inputs; anything measured (a line's length, a
dimension's value) comes from queries.
"""

from caliper.app.properties import format_number
from caliper.contracts.document import (
    Arc,
    Circle,
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
}


def kind_title(entity: Entity) -> str:
    """Short enough for a narrow browser: "Distance", "Diameter", "Rectangle"."""
    match entity:
        case DistanceDimension():
            return "Distance"
        case RadialDimension(measure=measure):
            return measure.value.capitalize()
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
            value = queries.dimension_value(id)
            shown = "?" if isinstance(value, Error) else n(value)
            return f"{shown} · {orientation.value}"
        case RadialDimension(measure=measure):
            value = queries.dimension_value(id)
            shown = "?" if isinstance(value, Error) else n(value)
            return f"{'⌀' if measure is RadialMeasure.DIAMETER else 'R'}{shown}"
    return ""


def ref_text(ref: Ref) -> str:
    return f"{ref.entity} {ref.feature.value.replace('_', ' ')}"
