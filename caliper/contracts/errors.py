"""Structured errors shared by commands and queries.

Invalid input is reported as an `Error` value, never raised. Exceptions mean a bug in
Caliper itself (or API misuse such as undoing inside an open transaction).
"""

from dataclasses import dataclass
from enum import StrEnum


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


@dataclass(frozen=True, slots=True, kw_only=True)
class Error:
    """A problem with a command or query. A value, never raised."""

    code: ErrorCode
    message: str
    """Human-readable, for display."""
    field: str | None = None
    """The offending input field, e.g. "width", so a properties panel can highlight it."""
