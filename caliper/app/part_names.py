"""What the window calls the part's features and their settings: one vocabulary.

The Part panel, the Extrude panel, Properties, and the status bar all say "Sketch 1", "Top",
"end of Extrude 1", "Add to the solid", and "Along the normal" from here, so a feature reads
the same wherever it's shown. The contract's own values (`xy`, `add`, `reversed`) are what's
saved and what an agent sends; they aren't what a person reads.
"""

from collections.abc import Sequence

from caliper.app.viewport.scene3d import PLANE_NAMES
from caliper.contracts.document import (
    Document,
    EntityId,
    ExtrudeOperation,
    FaceRef,
    PartFeature,
    Plane,
)

OPERATIONS: dict[ExtrudeOperation, str] = {
    ExtrudeOperation.ADD: "Add to the solid",
    ExtrudeOperation.REMOVE: "Cut from the solid",
}
DIRECTIONS: dict[bool, str] = {False: "Along the normal", True: "Against it"}
"""An extrude's `reversed`, as a choice of two ways and not a box to tick."""


def titles(features: tuple[PartFeature, ...]) -> dict[EntityId, str]:
    """ "Sketch 1", "Extrude 2": each feature's kind and its number among that kind."""
    counts: dict[str, int] = {}
    found = {}
    for feature in features:
        counts[feature.kind] = counts.get(feature.kind, 0) + 1
        found[feature.id] = f"{feature.kind.title()} {counts[feature.kind]}"
    return found


def place_name(document: Document, plane: Plane | FaceRef) -> str:
    """Where a sketch sits, as the window names it: "Top", or "end of Extrude 1" (ADR 0016)."""
    if isinstance(plane, Plane):
        return PLANE_NAMES[plane]
    return f"{plane.face} of {titles(document.features).get(plane.feature, plane.feature)}"


def feature_text(document: Document, id: EntityId) -> str:
    """ "Sketch 1 (e0)": a feature by its name, with the id an agent or a file knows it by."""
    return f"{titles(document.features).get(id, 'Not in the part')} ({id})"


def profile_text(ids: Sequence[EntityId]) -> str:
    """What an extrude sweeps: all of its sketch, or the curves picked for it."""
    if not ids:
        return "All of its geometry"
    return f"{len(ids)} selected"
