"""Dimension tool: pick what to measure, place the label, type the value.

1. Click a point, a line, a rectangle side, a circle, or an arc (`reference_at_point`).
2. Optionally click a second one: two points, a point and a line, two lines, and so on.
3. Click in empty space to place the label. The kind (length, horizontal, vertical,
   distance, angle, radius, diameter) follows where the label goes, as in Onshape; the
   engine decides it (`infer_dimension`).
4. An entry opens with the measured value. Return makes the dimension driving at that value,
   so nothing moves unless you type another number; the preview shows what a typed value
   does before you commit. Emptying the entry adds a driven (reference) dimension, as an
   empty value does in Properties. If the size is already fixed by other constraints, it's
   added as driven too, which is the engine's own advice, and the status bar says so.

Every preview is the engine's result on a scratch `Bus(document)`, as the Fillet tool does,
so what you see is what Return produces. Esc cancels the dimension.
"""

from dataclasses import dataclass, replace
from enum import StrEnum

from PySide6.QtCore import Qt

from caliper.app import references, theme
from caliper.app.dimension_layout import engine_placement
from caliper.app.properties import format_number
from caliper.app.session import DocumentSession
from caliper.app.tools.base import Pointer, Tool
from caliper.app.viewport.annotations import drawing, paint
from caliper.app.viewport.highlight import paint_references
from caliper.app.viewport.painter import GEOMETRY_TYPES, ModelPainter, cosmetic_pen
from caliper.contracts.commands import Applied, CreateDimension, Rejected
from caliper.contracts.document import DistanceDimension, EntityId, Point2, Ref
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.queries import DimensionType
from caliper.engine.commands.bus import Bus

KIND_NAME = {
    DimensionType.LENGTH: "Length",
    DimensionType.DISTANCE: "Distance",
    DimensionType.HORIZONTAL_DISTANCE: "Horizontal",
    DimensionType.VERTICAL_DISTANCE: "Vertical",
    DimensionType.RADIUS: "Radius",
    DimensionType.DIAMETER: "Diameter",
    DimensionType.ANGLE: "Angle",
}
_ALONG_AXIS = {
    DimensionType.HORIZONTAL_DISTANCE,
    DimensionType.VERTICAL_DISTANCE,
}


class DimensionPhase(StrEnum):
    FIRST = "first"
    """Nothing picked yet."""
    MORE = "more"
    """One reference picked: click another, or place the label."""
    PLACE = "place"
    """Two references picked: place the label."""
    VALUE = "value"
    """Placed: the entry is open for the value."""


@dataclass(frozen=True, slots=True)
class Preview:
    """A dimension created on a scratch bus: what committing would produce."""

    bus: Bus
    id: EntityId
    command: CreateDimension
    """The command that made it, with the placement the engine needs (see `_command`)."""
    kind: DimensionType
    measured: float


class DimensionTool(Tool):
    name = "Dimension"
    shortcut = "D"
    uses_hover = True
    commit_failure_message = ""  # a rejected value is explained by the engine's message

    def __init__(self, session: DocumentSession) -> None:
        super().__init__(session)
        self.phase = DimensionPhase.FIRST
        self.refs: tuple[Ref, ...] = ()
        self.current: Point2 | None = None
        self.preview: Preview | None = None
        self.typed: Preview | None = None
        """The preview with the value typed so far, when it differs from the measurement."""
        self.entry_request: tuple[tuple[str, ...], str] | None = None
        """Fields and first text for the canvas to open the value entry with, once."""

    @property
    def hint(self) -> str:
        kind = f"{KIND_NAME[self.preview.kind]}: " if self.preview is not None else ""
        return {
            DimensionPhase.FIRST: "Dimension: click a point, line, circle, or arc",
            DimensionPhase.MORE: f"Dimension: {kind}click another, or click to place (Esc cancels)",
            DimensionPhase.PLACE: f"Dimension: {kind}click to place (Esc cancels)",
            DimensionPhase.VALUE: f"Dimension: {kind}type a value, Return to add (Esc cancels)",
        }[self.phase]

    @property
    def busy(self) -> bool:
        return self.phase is not DimensionPhase.FIRST

    @property
    def needs_entry(self) -> bool:
        return self.phase is DimensionPhase.VALUE

    @property
    def numeric_fields(self) -> tuple[str, ...]:  # type: ignore[override]
        if self.phase is not DimensionPhase.VALUE or self.preview is None:
            return ()
        return (KIND_NAME[self.preview.kind],)

    # --- Input ----------------------------------------------------------------------------

    def move(self, pointer: Pointer) -> None:
        self.current = pointer.raw
        if self.phase in (DimensionPhase.MORE, DimensionPhase.PLACE):
            self.preview = self._preview(pointer.raw)

    def release(self, pointer: Pointer) -> None:
        self.current = pointer.raw
        queries = self.session.queries
        match self.phase:
            case DimensionPhase.FIRST:
                ref = queries.reference_at_point(pointer.raw, pointer.tolerance)
                if ref is None:
                    self.session.message.emit("Click a point, line, circle, or arc to dimension")
                    return
                self.refs = (ref,)
                self.phase = DimensionPhase.MORE
                self.preview = self._preview(pointer.raw)
            case DimensionPhase.MORE:
                ref = queries.reference_at_point(pointer.raw, pointer.tolerance)
                if ref is not None and ref not in self.refs and self._fits((*self.refs, ref)):
                    self.refs = (*self.refs, ref)
                    self.phase = DimensionPhase.PLACE
                    self.preview = self._preview(pointer.raw)
                else:
                    self._place(pointer.raw)
            case DimensionPhase.PLACE:
                self._place(pointer.raw)

    def cancel(self) -> None:
        self.phase = DimensionPhase.FIRST
        self.refs = ()
        self.current = None
        self.preview = self.typed = None
        self.entry_request = None

    # --- Typed values ---------------------------------------------------------------------

    def type_values(self, values: tuple[float | None, ...]) -> None:  # type: ignore[override]
        value = values[0] if values else None  # empty once the entry has closed
        preview = self.preview
        if preview is None or value is None or value == preview.measured:
            self.typed = None
            return
        self.typed = self._run(replace(preview.command, value=value))

    def commit_values(self, values: tuple[float | None, ...]) -> bool:  # type: ignore[override]
        preview = self.preview
        if preview is None:
            return False
        value = values[0] if values else None  # emptied: a driven (reference) dimension
        result = self.session.execute(replace(preview.command, value=value))
        if isinstance(result, Rejected) and value == preview.measured:
            (error, *_) = result.errors
            if error.code is ErrorCode.CONSTRAINT_REDUNDANT:
                # The size is already fixed: keep the dimension, as a reference.
                driven = self.session.execute(replace(preview.command, value=None))
                if isinstance(driven, Applied):
                    fixed_by = ", ".join(error.ids)
                    self.session.message.emit(
                        f"Added as a driven dimension: {fixed_by} already fix this size"
                    )
                    self.cancel()
                    return True
        if isinstance(result, Applied):
            self.cancel()
            return True
        return False

    # --- Drawing --------------------------------------------------------------------------

    def paint(self, painter: ModelPainter) -> None:
        if not self.refs:
            return
        painter.set_pen(cosmetic_pen(theme.SELECTED, theme.HIGHLIGHT_WIDTH))
        paint_references(painter, self.session, self.refs)
        shown = self.typed or self.preview
        if shown is None:
            return
        if self.typed is not None:
            # What the typed value would do to the geometry.
            before = self.session.document.entities
            painter.set_pen(cosmetic_pen(theme.PREVIEW, theme.GEOMETRY_WIDTH, Qt.PenStyle.DashLine))
            for id, entity in shown.bus.document.entities.items():
                if isinstance(entity, GEOMETRY_TYPES) and before.get(id) != entity:
                    painter.geometry(entity)
        plan = drawing(shown.bus, shown.id, painter.view)
        if plan is not None:
            paint(painter, plan, theme.PREVIEW)

    # --- Steps ----------------------------------------------------------------------------

    def _fits(self, refs: tuple[Ref, ...]) -> bool:
        """Whether some dimension kind accepts these references."""
        options = self.session.queries.applicable_constraints(refs)
        return any(isinstance(o.type, DimensionType) and o.error is None for o in options)

    def _place(self, at: Point2) -> None:
        preview = self._preview(at, quiet=False)
        if preview is None:
            return
        self.preview = preview
        self.phase = DimensionPhase.VALUE
        self.entry_request = ((KIND_NAME[preview.kind],), format_number(round(preview.measured, 6)))

    def _preview(self, at: Point2, *, quiet: bool = True) -> Preview | None:
        kind = self.session.queries.infer_dimension(self.refs, at)
        if isinstance(kind, Error):
            if not quiet:
                self.session.message.emit(kind.message)
            return None
        command = self._command(kind, at)
        if command is None:
            return None
        preview = self._run(command)
        if preview is None and not quiet:
            self.session.message.emit("That can't be dimensioned here")
        return preview

    def _command(self, kind: DimensionType, at: Point2) -> CreateDimension | None:
        """The command for a label at `at`.

        For horizontal and vertical distances the engine measures the offset across a→b
        rather than along the dimension's normal, so it's told the point that gives the
        offset the shell draws (`engine_placement`), and the label lands where it was put.
        """
        command = CreateDimension(refs=self.refs, placement=at, type=kind)
        if kind not in _ALONG_AXIS:
            return command
        first = self._run(command)
        if first is None:
            return None
        dim = first.bus.document.entities[first.id]
        if not isinstance(dim, DistanceDimension):
            return command
        a = references.point(first.bus.queries, dim.a)
        b = references.point(first.bus.queries, dim.b)
        if a is None or b is None:
            return command
        return replace(command, placement=engine_placement(dim.orientation, a, b, at))

    def _run(self, command: CreateDimension) -> Preview | None:
        scratch = Bus(self.session.document)
        result = scratch.execute(command)
        if not isinstance(result, Applied):
            return None
        (id,) = result.created_ids
        measured = scratch.queries.dimension_value(id)
        resolved = result.command
        if isinstance(measured, Error) or not isinstance(resolved, CreateDimension):
            return None
        kind = resolved.type if resolved.type is not None else DimensionType.DISTANCE
        return Preview(bus=scratch, id=id, command=command, kind=kind, measured=measured)
