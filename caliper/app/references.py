"""Where a reference is on screen: a point, or a straight curve's two ends. Qt-free.

Every location comes from `queries.feature_point`. A straight curve (a line's CURVE, a
rectangle side) is its two end features, following the direction `Feature` documents:
sides run left to right (BOTTOM, TOP) or upward (LEFT, RIGHT). Circles and arcs are
answered by `round_curve`, from their stored centre and radius.
"""

from caliper.app.dimension_layout import Anchor, Segment
from caliper.contracts.document import Arc, Circle, Document, Feature, Point2, Ref
from caliper.contracts.errors import Error
from caliper.contracts.queries import Queries

SIDE_ENDS: dict[Feature, tuple[Feature, Feature]] = {
    Feature.BOTTOM: (Feature.BOTTOM_LEFT, Feature.BOTTOM_RIGHT),
    Feature.RIGHT: (Feature.BOTTOM_RIGHT, Feature.TOP_RIGHT),
    Feature.TOP: (Feature.TOP_LEFT, Feature.TOP_RIGHT),
    Feature.LEFT: (Feature.BOTTOM_LEFT, Feature.TOP_LEFT),
}


def point(queries: Queries, ref: Ref) -> Point2 | None:
    found = queries.feature_point(ref)
    return None if isinstance(found, Error) else found


def straight(queries: Queries, ref: Ref) -> Segment | None:
    """A line's CURVE or a rectangle side as a segment, or None for anything else."""
    if ref.feature is Feature.CURVE:
        if _has_center(queries, ref):  # an arc has START and END too, but isn't straight
            return None
        ends = (Feature.START, Feature.END)
    elif ref.feature in SIDE_ENDS:
        ends = SIDE_ENDS[ref.feature]
    else:
        return None
    a = point(queries, Ref(entity=ref.entity, feature=ends[0]))
    b = point(queries, Ref(entity=ref.entity, feature=ends[1]))
    return None if a is None or b is None else Segment(a, b)


def anchor(queries: Queries, ref: Ref) -> Anchor | None:
    """A point feature's location, or a straight curve's segment."""
    if ref.feature is Feature.CURVE or ref.feature in SIDE_ENDS:
        return straight(queries, ref)
    return point(queries, ref)


def round_curve(document: Document, ref: Ref) -> Circle | Arc | None:
    """The circle or arc a CURVE reference names, if it names one."""
    entity = document.entities.get(ref.entity)
    if ref.feature is Feature.CURVE and isinstance(entity, Circle | Arc):
        return entity
    return None


def _has_center(queries: Queries, ref: Ref) -> bool:
    return isinstance(queries.feature_point(Ref(entity=ref.entity, feature=Feature.CENTER)), Point2)
