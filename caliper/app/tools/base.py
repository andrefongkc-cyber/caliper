"""What every tool receives and implements.

Tools see model coordinates only (millimetres, Y up). The canvas converts mouse events into
a `Pointer` before a tool gets them, including the pick tolerance in mm and any snap.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from caliper.app.session import DocumentSession
from caliper.app.viewport.painter import ModelPainter
from caliper.contracts.document import EntityId, Point2, Ref


class SnapKind(StrEnum):
    NONE = "none"
    GRID = "grid"
    FEATURE = "feature"
    GUIDE = "guide"
    """Aligned with a recently hovered feature point's x or y."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Pointer:
    raw: Point2
    """Where the pointer actually is."""
    point: Point2
    """Where the pointer is after snapping. Tools use this for anything they create."""
    snap: SnapKind
    ref: Ref | None
    """The snapped feature, when snap is FEATURE."""
    tolerance: float
    """Pick radius in mm, derived from a fixed on-screen radius and the current zoom."""
    shift: bool = False
    force_box: bool = False
    """The user asked for a box selection even where a shape would be picked (⌘ held)."""
    guides: tuple[tuple[Point2, Point2], ...] = ()
    """Alignment guides to draw, from an acquired feature point to `point`."""
    annotation: EntityId | None = None
    """A dimension label or constraint glyph under the pointer. These are drawn by the shell,
    so the shell hit-tests them; they sit on top of geometry and win a click."""


class Tool:
    """One tool mode. Subclasses keep their own `phase` and send commands on completion."""

    name: str = ""
    shortcut: str = ""
    category: str = "create"
    """Tool bar group: "select", "create", or "inspect"."""
    uses_hover: bool = False
    """Whether the canvas should highlight the entity under the pointer."""

    def __init__(self, session: DocumentSession) -> None:
        self.session = session

    @property
    def hint(self) -> str:
        return ""

    @property
    def busy(self) -> bool:
        """True while an operation is in progress, so Esc cancels it rather than the tool."""
        return False

    def press(self, pointer: Pointer) -> None: ...

    def move(self, pointer: Pointer) -> None: ...

    def release(self, pointer: Pointer) -> None: ...

    def cancel(self) -> None:
        """Abandon the operation in progress. Sends nothing."""

    def paint(self, painter: ModelPainter) -> None:
        """Draw the preview for the operation in progress."""

    # --- Typed values ---------------------------------------------------------------------

    numeric_fields: tuple[str, ...] = ()
    """Labels of the values a user can type while this tool is busy, e.g. ("Width", "Height").

    Empty means the tool takes no typed input.
    """

    def type_values(self, values: Sequence[float | None]) -> None:
        """Preview with typed values. `None` means "not typed yet": follow the pointer."""

    def commit_values(self, values: Sequence[float | None]) -> bool:
        """Finish the operation with typed values. False if they don't make a valid shape."""
        return False

    commit_failure_message = "Those values don't make a shape: sizes must be above 0"
    """Shown when `commit_values` returns False; empty if the tool has already said why."""

    @property
    def needs_entry(self) -> bool:
        """True while the operation can't go on without the value entry, so closing the
        entry (Esc) cancels the operation rather than just the typing."""
        return False

    entry_request: tuple[tuple[str, ...], str] | None = None
    """Set by a tool that wants the value entry opened now: (field labels, first text).
    The canvas opens it after the click that set it, and clears this."""
