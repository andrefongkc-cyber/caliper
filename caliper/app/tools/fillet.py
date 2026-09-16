"""Fillet tool: click two lines that meet, type a radius, and the corner is rounded.

The preview is the engine's own answer, not a guess: the tool runs the command on a scratch
copy of the document and draws what came back. If the radius doesn't fit, the engine says so
(and names the largest one that does), and nothing is sent to the real document.
"""

from enum import StrEnum

from PySide6.QtCore import Qt

from caliper.app import theme
from caliper.app.session import DocumentSession
from caliper.app.tools.base import Pointer, Tool
from caliper.app.tools.shapes import clean
from caliper.app.viewport.painter import ModelPainter, cosmetic_pen
from caliper.contracts.commands import Applied, FilletCorner, Rejected
from caliper.contracts.document import Arc, Document, EntityId, Line
from caliper.engine.commands.bus import Bus

DEFAULT_RADIUS = 5.0


class FilletPhase(StrEnum):
    FIRST = "first"
    SECOND = "second"
    RADIUS = "radius"


class FilletTool(Tool):
    name = "Fillet"
    shortcut = "O"
    category = "create"
    uses_hover = True
    numeric_fields = ("Radius",)

    def __init__(self, session: DocumentSession) -> None:
        super().__init__(session)
        self.phase = FilletPhase.FIRST
        self.a: EntityId | None = None
        self.b: EntityId | None = None
        self.radius = DEFAULT_RADIUS
        self.preview: Document | None = None
        """The scratch result to draw, or None if the radius doesn't fit."""

    @property
    def hint(self) -> str:
        return {
            FilletPhase.FIRST: "Fillet: click one line of the corner",
            FilletPhase.SECOND: "Fillet: click the other line (Esc cancels)",
            FilletPhase.RADIUS: f"Fillet: type a radius, then Return (now {clean(self.radius):g})",
        }[self.phase]

    @property
    def busy(self) -> bool:
        return self.phase is not FilletPhase.FIRST

    def release(self, pointer: Pointer) -> None:
        if self.phase is FilletPhase.RADIUS:
            self.commit_values((self.radius,))
            return
        hit = self.session.queries.entity_at_point(pointer.raw, pointer.tolerance)
        if hit is None or not isinstance(self.session.document.entities.get(hit), Line):
            self.session.message.emit("Click a line: a fillet rounds where two lines meet")
            return
        if self.phase is FilletPhase.FIRST:
            self.a, self.phase = hit, FilletPhase.SECOND
        elif hit != self.a:
            self.b, self.phase = hit, FilletPhase.RADIUS
            self._refresh(self.radius)

    def cancel(self) -> None:
        self.phase = FilletPhase.FIRST
        self.a = self.b = None
        self.radius = DEFAULT_RADIUS
        self.preview = None

    # --- Typed radius ---------------------------------------------------------------------

    def type_values(self, values) -> None:
        (radius,) = values
        self._refresh(self.radius if radius is None else radius)

    def commit_values(self, values) -> bool:
        (radius,) = values
        if self.a is None or self.b is None:
            return False
        self._refresh(self.radius if radius is None else radius)
        if self.preview is None:
            return False  # the engine already said why; don't send a command that must fail
        result = self.session.execute(FilletCorner(a=self.a, b=self.b, radius=clean(self.radius)))
        if isinstance(result, Applied):
            self.cancel()
            return True
        return False

    def _refresh(self, radius: float) -> None:
        """Ask the engine what this radius would do, on a copy."""
        self.radius = radius
        self.preview = None
        if self.a is None or self.b is None:
            return
        scratch = Bus(self.session.document)
        result = scratch.execute(FilletCorner(a=self.a, b=self.b, radius=clean(radius)))
        if isinstance(result, Rejected):
            self.session.message.emit(result.errors[0].message)
            return
        self.preview = scratch.document

    # --- Drawing --------------------------------------------------------------------------

    def paint(self, painter: ModelPainter) -> None:
        entities = self.session.document.entities
        painter.set_pen(cosmetic_pen(theme.SELECTED, theme.HIGHLIGHT_WIDTH))
        for id in (self.a, self.b):
            chosen = entities.get(id) if id is not None else None
            if isinstance(chosen, Line):
                painter.geometry(chosen)
        if self.preview is None:
            return
        painter.set_pen(cosmetic_pen(theme.PREVIEW, theme.GEOMETRY_WIDTH))
        for id, entity in self.preview.entities.items():
            if isinstance(entity, Line | Arc) and entities.get(id) != entity:
                painter.geometry(entity)
        painter.set_pen(cosmetic_pen(theme.PREVIEW, theme.GUIDE_WIDTH, Qt.PenStyle.DashLine))
        for id, entity in entities.items():
            if isinstance(entity, Line) and id in (self.a, self.b):
                painter.geometry(entity)
