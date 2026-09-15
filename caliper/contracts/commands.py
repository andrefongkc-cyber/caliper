"""Commands: the only way to change a Document. Freezes hard.

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
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType, TracebackType
from typing import ClassVar, Protocol, Self

from caliper.contracts.document import (
    DistanceOrientation,
    Document,
    Entity,
    EntityId,
    Point2,
    RadialMeasure,
    Ref,
)
from caliper.contracts.errors import Error
from caliper.contracts.queries import Queries

# --- Commands ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateLine:
    kind: ClassVar[str] = "create_line"
    start: Point2
    end: Point2
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateCircle:
    kind: ClassVar[str] = "create_circle"
    center: Point2
    radius: float
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateArc:
    kind: ClassVar[str] = "create_arc"
    center: Point2
    radius: float
    start_angle: float
    sweep_angle: float
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateRectangle:
    kind: ClassVar[str] = "create_rectangle"
    corner: Point2
    width: float
    height: float
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateDistanceDimension:
    kind: ClassVar[str] = "create_distance_dimension"
    a: Ref
    b: Ref
    orientation: DistanceOrientation
    offset: float
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateRadialDimension:
    kind: ClassVar[str] = "create_radial_dimension"
    target: EntityId
    measure: RadialMeasure
    label_angle: float
    id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MoveEntities:
    """Translate geometry by (dx, dy).

    Annotations in `ids` are ignored; they follow their references.
    """

    kind: ClassVar[str] = "move_entities"
    ids: tuple[EntityId, ...]
    dx: float
    dy: float


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteEntities:
    """Delete entities, plus any annotation referencing a deleted entity, in one delta."""

    kind: ClassVar[str] = "delete_entities"
    ids: tuple[EntityId, ...]


ParamValue = float | Point2 | Ref | EntityId | DistanceOrientation | RadialMeasure
"""Any value an entity field can hold."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ModifyEntity:
    """Set one or more fields of an existing entity, e.g. `changes={"width": 120.0}`.

    Keys are the entity dataclass's field names. All changes validate together and apply
    atomically. Rectangle width and height edits keep `corner` fixed.
    """

    kind: ClassVar[str] = "modify_entity"
    id: EntityId
    changes: Mapping[str, ParamValue]


Command = (
    CreateLine
    | CreateCircle
    | CreateArc
    | CreateRectangle
    | CreateDistanceDimension
    | CreateRadialDimension
    | MoveEntities
    | DeleteEntities
    | ModifyEntity
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


@dataclass(frozen=True, slots=True, kw_only=True)
class Change:
    """Sent to subscribers after the document changes. Read the new state from `bus.document`."""

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
    """The single entry point for changing a document."""

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
