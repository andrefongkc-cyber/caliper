"""Constrain tool, and what a constraint action applies to.

Click points or curves to pick them, in order; click a picked one again to drop it; click
empty space to start over. Then a constraint action (Sketch > Constrain, the palette, or its
key) adds the constraint. The picks are UI state on the session, never in the document.

Without picks, constraint actions apply to the selected entities' curves: a line, circle,
or arc's CURVE and a point's POINT, oldest id first, so the newer entity is the one that
moves ("the last reference moves"). A rectangle has four sides and no single curve, so a
selected rectangle offers nothing; pick one of its sides with this tool instead.
"""

from caliper.app import theme
from caliper.app.session import DocumentSession
from caliper.app.tools.base import Pointer, Tool
from caliper.app.viewport.highlight import paint_references
from caliper.app.viewport.painter import ModelPainter, cosmetic_pen
from caliper.contracts.document import Arc, Circle, EntityId, Feature, Line, Point, Ref
from caliper.contracts.queries import ConstraintOption


def natural(id: EntityId) -> tuple[str, int, str]:
    """e2 before e10."""
    head = id.rstrip("0123456789")
    digits = id[len(head) :]
    return (head, int(digits) if digits else -1, id)


def target_refs(session: DocumentSession) -> tuple[Ref, ...]:
    """What a constraint action would apply to right now."""
    if session.references:
        return session.references
    refs: list[Ref] = []
    entities = session.document.entities
    for id in sorted(session.selection, key=natural):
        match entities.get(id):
            case Line() | Circle() | Arc():
                refs.append(Ref(entity=id, feature=Feature.CURVE))
            case Point():
                refs.append(Ref(entity=id, feature=Feature.POINT))
            case _:
                return ()
    return tuple(refs)


def constraint_options(session: DocumentSession) -> dict[str, ConstraintOption]:
    """Each constraint type's option for the current target, keyed by the type's value."""
    refs = target_refs(session)
    if not refs:
        return {}
    return {option.type.value: option for option in session.queries.applicable_constraints(refs)}


class ConstrainTool(Tool):
    name = "Constrain"
    shortcut = "K"
    category = "constrain"

    def __init__(self, session: DocumentSession) -> None:
        super().__init__(session)
        self.under: Ref | None = None
        """The point or curve under the pointer."""

    @property
    def hint(self) -> str:
        picked = len(self.session.references)
        if not picked:
            return "Constrain: click points or curves, then press a constraint key (H V I E T)"
        noun = "reference" if picked == 1 else "references"
        return f"Constrain: {picked} {noun} picked · H V I E T or Commands to apply · Esc clears"

    @property
    def busy(self) -> bool:
        return bool(self.session.references)

    def move(self, pointer: Pointer) -> None:
        self.under = self.session.queries.reference_at_point(pointer.raw, pointer.tolerance)

    def press(self, pointer: Pointer) -> None:
        ref = self.session.queries.reference_at_point(pointer.raw, pointer.tolerance)
        picked = self.session.references
        if ref is None:
            self.session.set_references(())
        elif ref in picked:
            self.session.set_references(tuple(r for r in picked if r != ref))
        else:
            self.session.set_references((*picked, ref))

    def cancel(self) -> None:
        self.session.set_references(())
        self.under = None

    def paint(self, painter: ModelPainter) -> None:
        picked = self.session.references
        if self.under is not None and self.under not in picked:
            painter.set_pen(cosmetic_pen(theme.HOVER, theme.HIGHLIGHT_WIDTH))
            paint_references(painter, self.session, (self.under,))
        painter.set_pen(cosmetic_pen(theme.SELECTED, theme.HIGHLIGHT_WIDTH))
        paint_references(painter, self.session, picked)
