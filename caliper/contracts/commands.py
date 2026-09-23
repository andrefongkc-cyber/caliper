"""Commands: the only way to change a Document. Frozen for V1.

Every mutation, whether it comes from the UI, the AI layer, or a script, is one of these
dataclasses sent to a CommandBus. See ADR 0002.

Conventions:
- Commands are plain data: frozen, keyword-only, serializable. They carry no validation.
  An invalid command must be representable so the bus can return `Rejected` with
  structured errors instead of raising.
- Numbers are floats. The bus normalizes ints to float in the resolved command so a
  script and the UI produce identical files. Bools are rejected.
- Create commands take an optional `id`. Left as None, the bus allocates one. The
  resolved command in `Applied` always carries the concrete id, and that is what replay
  and the transcript record.
- Commands never define their own inverse. The bus records a Delta and undo inverts it.
- With constraints in the document, every command that changes geometry, a constraint, or
  a driving value also solves: the geometry the constraints move is part of the same
  delta. A command whose result can't satisfy the constraints is `Rejected` with
  `constraint.conflict` and changes nothing. The solve prefers to move, in order: nothing;
  the part named last (a constraint's last reference, a dimension's `b`, an edited entity's
  other fields); that part's whole entity; everything connected to it.

Frozen as of V1: a new command type, or a change to an existing one, needs a joint
`contracts/` PR. ADR 0002 has the reasoning. The post-V1 fixes added `ChangeReason.COMMIT`,
so committing a transaction is announced like every other change to the undo stack.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType, TracebackType
from typing import ClassVar, Protocol, Self

from caliper.contracts.document import (
    ConstraintType,
    DistanceOrientation,
    Document,
    Entity,
    EntityId,
    Point2,
    RadialMeasure,
    Ref,
)
from caliper.contracts.errors import Error
from caliper.contracts.queries import DimensionType, Queries

# --- Commands ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CreatePoint:
    kind: ClassVar[str] = "create_point"
    position: Point2
    construction: bool = False
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateLine:
    kind: ClassVar[str] = "create_line"
    start: Point2
    end: Point2
    construction: bool = False
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateCircle:
    kind: ClassVar[str] = "create_circle"
    center: Point2
    radius: float
    construction: bool = False
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateArc:
    kind: ClassVar[str] = "create_arc"
    center: Point2
    radius: float
    start_angle: float
    sweep_angle: float
    construction: bool = False
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateRectangle:
    kind: ClassVar[str] = "create_rectangle"
    corner: Point2
    width: float
    height: float
    construction: bool = False
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateDistanceDimension:
    kind: ClassVar[str] = "create_distance_dimension"
    a: Ref
    b: Ref
    orientation: DistanceOrientation
    offset: float
    value: float | None = None
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateRadialDimension:
    kind: ClassVar[str] = "create_radial_dimension"
    target: EntityId
    measure: RadialMeasure
    label_angle: float
    value: float | None = None
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateAngleDimension:
    kind: ClassVar[str] = "create_angle_dimension"
    a: Ref
    b: Ref
    supplementary: bool = False
    offset: float
    value: float | None = None
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateDimension:
    """Dimension a selection, choosing the kind the way a CAD sketcher does.

    `Queries.infer_dimension(refs, placement)` names the kind this creates; `type` asks for
    one explicitly instead (rejected if it doesn't fit the selection). The result is a
    DistanceDimension, RadialDimension, or AngleDimension whose placement fields come from
    `placement`. `value` None makes it driven; a number makes it driving, and the solver
    moves geometry to match. The resolved command carries the inferred `type`.
    """

    kind: ClassVar[str] = "create_dimension"
    refs: tuple[Ref, ...]
    placement: Point2
    value: float | None = None
    type: DimensionType | None = None
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateConstraint:
    """Add a geometric constraint and solve.

    `refs` may come in any order the type accepts; the resolved command carries the
    canonical order the constraint is stored in. Rejected with `constraint.not_applicable`
    when the type doesn't fit the references, `constraint.unsupported` for Pierce,
    `constraint.conflict` when it can't hold together with the existing constraints, and
    `constraint.redundant` when they already imply it; the last two name the constraints
    involved in `Error.ids`.
    """

    kind: ClassVar[str] = "create_constraint"
    type: ConstraintType
    refs: tuple[Ref, ...]
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FilletCorner:
    """Round the corner where two lines meet with an arc of `radius`, tangent to both.

    V1 rounds a corner someone drew: `a` and `b` must already share an endpoint. Both lines
    are trimmed back to where the arc touches them, and the arc is added as a new entity
    with `id`, allocated when it is None. Lines that only meet if extended, or not at all,
    are rejected.

    A coincident constraint joining the two corner endpoints is removed with the corner it
    held. No constraints are added: tangency is the user's choice (it is suggested). The
    arc is construction geometry only if both lines are.
    """

    kind: ClassVar[str] = "fillet_corner"
    a: EntityId
    b: EntityId
    radius: float
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MoveEntities:
    """Translate geometry by (dx, dy).

    Dimensions and constraints in `ids` are ignored; they follow their references. Geometry
    constrained to what moved follows it where the constraints require.
    """

    kind: ClassVar[str] = "move_entities"
    ids: tuple[EntityId, ...]
    dx: float
    dy: float


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteEntities:
    """Delete entities, plus any dimension or constraint referring to a deleted entity, in
    one delta."""

    kind: ClassVar[str] = "delete_entities"
    ids: tuple[EntityId, ...]


ParamValue = (
    float
    | bool
    | Point2
    | Ref
    | tuple[Ref, ...]
    | EntityId
    | DistanceOrientation
    | RadialMeasure
    | ConstraintType
    | None
)
"""Any value an entity field can hold. None only where the field allows it (`value`)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ModifyEntity:
    """Set one or more fields of an existing entity, e.g. `changes={"width": 120.0}`.

    Keys are the entity dataclass's field names. All changes validate together and apply
    atomically. Rectangle width and height edits keep `corner` fixed.

    Setting a dimension's `value` drives geometry to it (None makes it driven again).
    Editing geometry holds the edited fields where they were set and lets the constraints
    move the rest; an edit the constraints can't allow (moving a fixed point) is rejected.
    """

    kind: ClassVar[str] = "modify_entity"
    id: EntityId
    changes: Mapping[str, ParamValue]


Command = (
    CreatePoint
    | CreateLine
    | CreateCircle
    | CreateArc
    | CreateRectangle
    | CreateDistanceDimension
    | CreateRadialDimension
    | CreateAngleDimension
    | CreateDimension
    | CreateConstraint
    | MoveEntities
    | DeleteEntities
    | ModifyEntity
    | FilletCorner
)


# --- Results ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Delta:
    """What a change did, as whole entity values before and after.

    An id only in `before` was removed, only in `after` was added, in both was modified.
    Holds entity definitions only, never derived geometry.
    """

    before: Mapping[EntityId, Entity]
    after: Mapping[EntityId, Entity]
    next_id_before: int
    next_id_after: int

    @property
    def added(self) -> frozenset[EntityId]:
        return frozenset(self.after.keys() - self.before.keys())

    @property
    def removed(self) -> frozenset[EntityId]:
        return frozenset(self.before.keys() - self.after.keys())

    @property
    def modified(self) -> frozenset[EntityId]:
        return frozenset(self.before.keys() & self.after.keys())

    def inverted(self) -> Self:
        return type(self)(
            before=self.after,
            after=self.before,
            next_id_before=self.next_id_after,
            next_id_after=self.next_id_before,
        )

    @classmethod
    def empty(cls, next_id: int) -> Self:
        none: Mapping[EntityId, Entity] = MappingProxyType({})
        return cls(before=none, after=none, next_id_before=next_id, next_id_after=next_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class Applied:
    command: Command
    """The resolved command: ids filled in, ints normalized to float."""
    delta: Delta
    label: str
    """Short and title-case, e.g. "Create Rectangle", shown as "Undo Create Rectangle"."""
    created_ids: tuple[EntityId, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Rejected:
    """Nothing changed."""

    command: Command
    """The command as submitted."""
    errors: tuple[Error, ...]
    """Never empty."""


CommandResult = Applied | Rejected


class ChangeReason(StrEnum):
    EXECUTE = "execute"
    UNDO = "undo"
    REDO = "redo"
    ROLLBACK = "rollback"
    COMMIT = "commit"
    """A transaction closed and changed the undo and redo stacks: its entry was recorded, or,
    unrecorded, the stacks were cleared. Its commands already sent their own EXECUTE
    changes, so the document is already in this state and `delta` is empty. Sent once, by
    the outermost transaction, and only when the transaction changed the document."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Change:
    """Sent to subscribers after the document or the undo and redo stacks change.

    `delta` is what changed since the previous Change, so applying each in turn tracks the
    document. Read the new state from `bus.document`, and the labels from `undo_label` and
    `redo_label`, which are already up to date when a Change arrives.
    """

    reason: ChangeReason
    delta: Delta
    label: str


Listener = Callable[[Change], None]
Unsubscribe = Callable[[], None]


# --- Bus --------------------------------------------------------------------------------


class Transaction(Protocol):
    """Groups commands into one unit. Execute commands with `bus.execute` while it is open.

    Leaving the `with` block commits, unless `rollback()` was called or an exception
    escaped; then every change made inside is reverted and subscribers get a ROLLBACK
    change. A Rejected command does not roll back by itself: the caller decides, which
    lets an agent retry inside a transaction. Nested transactions merge into the outermost.
    """

    def rollback(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class CommandBus(Protocol):
    """The single entry point for changing a document.

    There is no way to replace the document in place: a bus is created around one, and
    opening a file means a new bus, because an undo stack must never cross documents.

    Committing a transaction records its undo entry and sends one COMMIT `Change` with an
    empty delta: the document is already in that state, but the undo and redo labels have
    just changed, and a view that displays them refreshes then.
    """

    @property
    def document(self) -> Document:
        """The current snapshot. Immutable, so it is safe to hold on to."""
        ...

    @property
    def queries(self) -> Queries:
        """Queries bound to the current snapshot."""
        ...

    def execute(self, command: Command, *, merge_key: str | None = None) -> CommandResult:
        """Validate and apply a command.

        Consecutive executes with the same `merge_key` merge into one undo entry, e.g.
        "e3.width" while a value is being dragged. Ignored inside a transaction.

        A command that changes nothing (setting a width to the value it already has) is
        `Applied` with an empty delta: nothing is recorded and subscribers hear nothing.
        """
        ...

    def transaction(self, label: str, *, undoable: bool = True) -> Transaction:
        """Open a transaction.

        undoable=True: the commit becomes one undo entry named `label`, holding the net
        delta however many commands ran. undoable=False: nothing is recorded, and the
        commit clears the undo and redo stacks, because their entries no longer match
        the document.
        """
        ...

    def undo(self) -> Change | None:
        """Revert the latest undo entry. None if there is nothing to undo."""
        ...

    def redo(self) -> Change | None: ...

    @property
    def undo_label(self) -> str | None: ...

    @property
    def redo_label(self) -> str | None: ...

    def subscribe(self, listener: Listener) -> Unsubscribe: ...
