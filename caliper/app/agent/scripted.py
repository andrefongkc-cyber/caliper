"""A scripted stand-in agent: turns a few plain-English requests into plans. Qt-free.

There's no language model in V1 (the AI layer arrives in V3). This exists so the review UI
can be built and used now, against the real command bus and queries, with the same plan
shape a model would produce. It understands only the phrasings in `EXAMPLES`, and says so.
"""

import re
from dataclasses import dataclass

from caliper.app.agent.proposal import Plan
from caliper.contracts.commands import (
    Command,
    CreateCircle,
    CreateRectangle,
    DeleteEntities,
    ModifyEntity,
    MoveEntities,
)
from caliper.contracts.document import (
    Circle,
    Document,
    EntityId,
    Feature,
    Point2,
    Rectangle,
    Ref,
)
from caliper.contracts.queries import Expectation, Metric

TOLERANCE = 0.001
NUMBER = r"(-?\d+(?:\.\d+)?)"

EXAMPLES = (
    "rectangle 120 x 50",
    "circle diameter 16 at 30, 25",
    "make it 140 wide",
    "make it 60 tall",
    "4 holes diameter 6 inset 10",
    "move it by 10, 0",
    "delete it",
)


@dataclass(frozen=True, slots=True)
class Understood:
    plan: Plan | None
    message: str
    """What the agent says back: the plan's explanation, or why it can't help."""


def _next_id(document: Document, offset: int = 0) -> EntityId:
    return EntityId(f"e{document.next_id + offset}")


@dataclass(frozen=True, slots=True)
class Problem:
    message: str


def _target(document: Document, selection: frozenset[EntityId], text: str) -> EntityId | Problem:
    """The entity a request refers to: an explicit id, else the single selected entity."""
    named = re.search(r"\b(e\d+)\b", text)
    if named:
        id = EntityId(named.group(1))
        return id if id in document.entities else Problem(f"There's no {id} in this sketch.")
    if len(selection) == 1:
        (only,) = selection
        return only
    return Problem("Select one shape first, or name it, like e1.")


def understand(text: str, document: Document, selection: frozenset[EntityId]) -> Understood:
    request = text.strip().lower().replace("\u00d7", "x").replace("⌀", "diameter ")

    if m := re.fullmatch(
        rf"(?:add )?(?:a )?rectangle {NUMBER} ?x ?{NUMBER}(?: at {NUMBER}, ?{NUMBER})?", request
    ):
        w, h = float(m.group(1)), float(m.group(2))
        corner = Point2(x=float(m.group(3) or 0), y=float(m.group(4) or 0))
        id = _next_id(document)
        return _plan(
            "Add Rectangle",
            f"Add a {w:g} \u00d7 {h:g} rectangle with its corner at ({corner.x:g}, {corner.y:g}).",
            (CreateRectangle(corner=corner, width=w, height=h),),
            (
                Expectation(metric=Metric.BBOX_WIDTH, expected=w, tolerance=TOLERANCE, ids=(id,)),
                Expectation(metric=Metric.BBOX_HEIGHT, expected=h, tolerance=TOLERANCE, ids=(id,)),
            ),
        )

    if m := re.fullmatch(
        rf"(?:add )?(?:a )?(?:circle|hole) (?:diameter )?{NUMBER} at {NUMBER}, ?{NUMBER}", request
    ):
        d, x, y = (float(g) for g in m.groups())
        id = _next_id(document)
        return _plan(
            "Add Circle",
            f"Add a ⌀{d:g} circle centred at ({x:g}, {y:g}).",
            (CreateCircle(center=Point2(x=x, y=y), radius=d / 2),),
            (Expectation(metric=Metric.BBOX_WIDTH, expected=d, tolerance=TOLERANCE, ids=(id,)),),
        )

    if m := re.fullmatch(rf"(?:make|set) (?:it|e\d+) {NUMBER} (wide|tall|high)", request):
        target = _target(document, selection, request)
        if isinstance(target, Problem):
            return Understood(None, target.message)
        value, direction = float(m.group(1)), m.group(2)
        field, metric = (
            ("width", Metric.BBOX_WIDTH) if direction == "wide" else ("height", Metric.BBOX_HEIGHT)
        )
        entity = document.entities[target]
        if not isinstance(entity, Rectangle):
            return Understood(None, f"{target} is a {entity.kind}; only rectangles have a {field}.")
        return _plan(
            f"Change {field.capitalize()}",
            f"Set the {field} of {target} to {value:g}, keeping its bottom-left corner fixed.",
            (ModifyEntity(id=target, changes={field: value}),),
            (Expectation(metric=metric, expected=value, tolerance=TOLERANCE, ids=(target,)),),
        )

    if m := re.fullmatch(
        rf"(\d+) holes (?:diameter )?{NUMBER} inset {NUMBER}(?: in e\d+)?", request
    ):
        count, d, inset = int(m.group(1)), float(m.group(2)), float(m.group(3))
        target = _target(document, selection, request)
        if isinstance(target, Problem):
            return Understood(None, target.message)
        plate = document.entities[target]
        if not isinstance(plate, Rectangle):
            return Understood(None, f"Corner holes go in a rectangle; {target} is a {plate.kind}.")
        if count != 4:
            return Understood(None, "I can only place 4 holes, one near each corner.")
        if 2 * inset >= min(plate.width, plate.height) or d / 2 >= inset:
            return Understood(None, f"An inset of {inset:g} doesn't leave room for ⌀{d:g} holes.")
        c, w, h = plate.corner, plate.width, plate.height
        centres = [
            Point2(x=c.x + inset, y=c.y + inset),
            Point2(x=c.x + w - inset, y=c.y + inset),
            Point2(x=c.x + w - inset, y=c.y + h - inset),
            Point2(x=c.x + inset, y=c.y + h - inset),
        ]
        ids = [_next_id(document, i) for i in range(4)]
        corners = (Feature.BOTTOM_LEFT, Feature.BOTTOM_RIGHT, Feature.TOP_RIGHT, Feature.TOP_LEFT)
        checks = [
            Expectation(
                metric=axis,
                expected=inset,
                tolerance=TOLERANCE,
                refs=(Ref(entity=target, feature=corner), Ref(entity=hole, feature=Feature.CENTER)),
            )
            for hole, corner in zip(ids, corners, strict=True)
            for axis in (Metric.DISTANCE_X, Metric.DISTANCE_Y)
        ]
        checks.append(
            Expectation(metric=Metric.BBOX_WIDTH, expected=d, tolerance=TOLERANCE, ids=(ids[0],))
        )
        return _plan(
            "Add Corner Holes",
            f"Add four ⌀{d:g} holes to {target}, each {inset:g} in from its corner.",
            tuple(CreateCircle(center=p, radius=d / 2) for p in centres),
            tuple(checks),
        )

    if m := re.fullmatch(rf"move (?:it|e\d+) by {NUMBER}, ?{NUMBER}", request):
        target = _target(document, selection, request)
        if isinstance(target, Problem):
            return Understood(None, target.message)
        dx, dy = float(m.group(1)), float(m.group(2))
        return _plan(
            "Move",
            f"Move {target} by ({dx:g}, {dy:g}).",
            (MoveEntities(ids=(target,), dx=dx, dy=dy),),
        )

    if re.fullmatch(r"delete (?:it|e\d+)", request):
        target = _target(document, selection, request)
        if isinstance(target, Problem):
            return Understood(None, target.message)
        entity = document.entities[target]
        extra = " and any dimensions on it" if isinstance(entity, Rectangle | Circle) else ""
        return _plan("Delete", f"Delete {target}{extra}.", (DeleteEntities(ids=(target,)),))

    return Understood(
        None,
        "I'm a scripted stand-in, not a language model, so I only understand requests like: "
        + "; ".join(f"“{e}”" for e in EXAMPLES),
    )


def _plan(
    label: str,
    explanation: str,
    commands: tuple[Command, ...],
    checks: tuple[Expectation, ...] = (),
) -> Understood:
    return Understood(Plan(label, explanation, commands, checks), explanation)
