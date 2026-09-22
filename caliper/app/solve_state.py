"""How constrained the sketch is, in words, from `queries.solve_status`. Qt-free.

Nothing is shown until the document has a constraint or a driving dimension: an
unconstrained sketch's degrees of freedom are just its size, and saying "4,000 degrees of
freedom" about a sketch nobody has constrained is noise.
"""

from caliper.contracts.document import (
    AngleDimension,
    Constraint,
    DistanceDimension,
    Document,
    EntityId,
    RadialDimension,
)
from caliper.contracts.queries import ConstraintState, SolveStatus

MAX_NAMED = 4
"""How many conflicting ids the status line names before saying "and N more"."""


def is_constrained(document: Document) -> bool:
    """True once the document holds a constraint or a driving dimension."""
    for entity in document.entities.values():
        match entity:
            case Constraint():
                return True
            case DistanceDimension() | RadialDimension() | AngleDimension() if (
                entity.value is not None
            ):
                return True
    return False


def fully_constrained(status: SolveStatus) -> frozenset[EntityId]:
    """Geometry with no degrees of freedom left."""
    return frozenset(id for id, dof in status.entity_dof.items() if dof == 0)


def unhealthy(status: SolveStatus) -> frozenset[EntityId]:
    """Constraints and driving dimensions that don't hold, or repeat others."""
    return frozenset(status.conflicting) | frozenset(status.redundant)


def describe(status: SolveStatus) -> str:
    match status.state:
        case ConstraintState.FULLY:
            return "Fully constrained"
        case ConstraintState.UNDER:
            noun = "degree" if status.dof == 1 else "degrees"
            return f"{status.dof:,} {noun} of freedom"
        case ConstraintState.OVER:
            return f"Over-constrained: {_named(status.redundant)} repeat others"
        case ConstraintState.CONFLICTING:
            return f"Conflicting: {_named(status.conflicting)}"


def _named(ids: tuple[EntityId, ...]) -> str:
    shown = ", ".join(ids[:MAX_NAMED])
    rest = len(ids) - MAX_NAMED
    return f"{shown} and {rest} more" if rest > 0 else shown
