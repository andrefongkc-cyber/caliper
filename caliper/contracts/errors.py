"""Structured errors shared by commands and queries. Frozen for V1.

Invalid input is reported as an `Error` value, never raised. Exceptions mean a bug in
Caliper itself (or API misuse such as undoing inside an open transaction).

Two things are deliberately outside this rule. Queries whose return type carries no
`Error` report unusable input as "no match" (see `queries.py`). And opening a file raises
`LoadError`, defined here, carrying these same `Error` values in `errors`, because a file
is a document that may be wrong in many ways at once.

Frozen as of V1: adding a code is a normal change, but renaming or repurposing one breaks
every caller that branches on it, and needs a joint `contracts/` PR. `LoadError` moved here
from `caliper.engine.io.canonical` after V1, so callers that open files catch it without
importing the engine; the old name still refers to this same class.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from caliper.contracts.document import EntityId


class ErrorCode(StrEnum):
    """Stable, machine-readable error codes.

    The AI verification loop branches on these, so they are part of the contract:
    adding a code is fine, renaming or repurposing one is not.
    """

    ENTITY_NOT_FOUND = "entity.not_found"
    ENTITY_WRONG_KIND = "entity.wrong_kind"
    ID_INVALID = "id.invalid"
    ID_TAKEN = "id.taken"
    VALUE_WRONG_TYPE = "value.wrong_type"
    VALUE_NOT_FINITE = "value.not_finite"
    VALUE_NOT_POSITIVE = "value.not_positive"
    VALUE_OUT_OF_RANGE = "value.out_of_range"
    FIELD_UNKNOWN = "field.unknown"
    GEOMETRY_DEGENERATE = "geometry.degenerate"
    REFERENCE_INVALID_FEATURE = "reference.invalid_feature"
    REFERENCE_DEGENERATE = "reference.degenerate"
    SELECTION_EMPTY = "selection.empty"
    PROFILE_NOT_CLOSED = "profile.not_closed"
    KERNEL_UNAVAILABLE = "kernel.unavailable"
    PROFILE_CONSTRUCTION = "profile.construction"
    """Construction geometry was offered as a profile."""
    CONSTRAINT_NOT_APPLICABLE = "constraint.not_applicable"
    """This constraint or dimension type doesn't fit the references given."""
    CONSTRAINT_UNSUPPORTED = "constraint.unsupported"
    """The type exists but needs something the engine doesn't have yet (Pierce)."""
    CONSTRAINT_CONFLICT = "constraint.conflict"
    """The constraints can't all hold at once. `Error.ids` names the ones involved: removing
    or changing any one of them would let the rest hold."""
    CONSTRAINT_REDUNDANT = "constraint.redundant"
    """A new constraint adds nothing: the ones in `Error.ids` already imply it."""
    SOLVER_NO_CONVERGENCE = "solver.no_convergence"
    """The solver ran out of iterations without either solving or proving a conflict."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Error:
    """A problem with a command or query. A value, never raised."""

    code: ErrorCode
    message: str
    """Human-readable, for display."""
    field: str | None = None
    """The offending input field, e.g. "width", so a properties panel can highlight it."""
    ids: tuple[EntityId, ...] = ()
    """The entities the error is about, sorted, e.g. the constraints in a conflict."""


class LoadError(ValueError):
    """An input file isn't valid. `errors` lists validation problems, if there were any.

    Raised by everything that reads a file or script: not valid JSON, not a Caliper file,
    a newer schema, or entities a command couldn't have created. The message joins the
    errors for display; `errors` keeps them structured, with each field's path in the file.
    """

    def __init__(self, message: str, errors: Sequence[Error] = ()) -> None:
        details = "; ".join(f"{e.field}: {e.message}" if e.field else e.message for e in errors)
        super().__init__(f"{message}: {details}" if details else message)
        self.errors = tuple(errors)
