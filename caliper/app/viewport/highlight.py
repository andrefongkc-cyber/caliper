"""Highlighting references: whole curves, rectangle sides, or points."""

from caliper.app import references
from caliper.app.session import DocumentSession
from caliper.app.viewport.painter import GEOMETRY_TYPES, ModelPainter
from caliper.contracts.document import Feature, Ref

POINT_PX = 3.5


def paint_references(
    painter: ModelPainter, session: DocumentSession, refs: tuple[Ref, ...]
) -> None:
    """Draw each reference with the painter's current pen."""
    entities = session.document.entities
    queries = session.queries
    for ref in refs:
        entity = entities.get(ref.entity)
        if ref.feature is Feature.CURVE and isinstance(entity, GEOMETRY_TYPES):
            painter.geometry(entity)
        elif (side := references.straight(queries, ref)) is not None:
            painter.line(side.start, side.end)
        elif (point := references.point(queries, ref)) is not None:
            painter.dot(point, POINT_PX)
