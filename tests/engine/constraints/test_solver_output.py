"""The fast solver's exact output, pinned before Performance V2.2 changed how it works.

Perf-4 (evaluating equations) and Perf-5 (the redundancy check) promise bit-identical results
(Andre's choice, 2026-10-01). `test_numerics.py` pins the *reference* solver and holds the
fast one only to equivalence; this pins the fast one itself: every document after every
command, and every outcome (applied, or rejected with its codes and the ids it names), of the
recorded sessions, the 150-line chain, and the mirror and pattern repeats. One bit different
anywhere fails here.

Pinned on macOS before Perf-4 (`shared/performance-v2.2` at 0f451cb). Arcs may differ across
platforms in the last bits (ADR 0008); if Linux CI gives another digest for that reason, pin
that one beside it per platform, as `test_numerics.py` says for N1, rather than loosening this.
"""

import hashlib
import itertools
import json
import math
from collections.abc import Callable

import pytest

from caliper.ai.model import ToolCall
from caliper.ai.tools import Workspace
from caliper.contracts.commands import (
    Applied,
    Command,
    CreateConstraint,
    CreateLine,
    Rejected,
)
from caliper.contracts.document import ConstraintType, Document, EntityId, Feature, Point2, Ref
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot
from caliper.engine.io.codec import COMMAND_KINDS, decode_command
from tests.engine.constraints.test_numerics import SESSIONS


class Fingerprint:
    def __init__(self) -> None:
        self._hash = hashlib.sha256()

    def outcome(self, result: object, document: Document) -> None:
        if isinstance(result, Rejected):
            for error in result.errors:
                self._hash.update(f"rejected {error.code} {error.ids} {error.message}\n".encode())
        else:
            self._hash.update(b"applied\n")
        self._hash.update(snapshot.dumps(document).encode())

    def text(self, value: object) -> None:
        self._hash.update(json.dumps(value, sort_keys=True, default=str).encode())

    def digest(self) -> str:
        return self._hash.hexdigest()


def session(name: str) -> str:
    """A recorded Claude Desktop session's commands and undos, step by step."""
    found, bus = Fingerprint(), Bus(kernel=None)
    for call in json.loads((SESSIONS / f"{name}.json").read_text())["calls"]:
        tool, arguments = call["tool"], call["arguments"]
        if tool == "undo":
            bus.undo()
            found.outcome(None, bus.document)
        elif tool in COMMAND_KINDS:
            command = decode_command({**arguments, "kind": tool}, tool)
            found.outcome(bus.execute(command), bus.document)
    return found.digest()


def chain() -> str:
    """150 lines, horizontal and vertical by turns, joined end to end: one large cluster."""
    found, bus = Fingerprint(), Bus(kernel=None)
    previous: EntityId | None = None
    for n in range(150):
        horizontal = n % 2 == 0
        start = Point2(x=float(n), y=float(n))
        end = Point2(
            x=start.x + (10.0 if horizontal else 0.0), y=start.y + (0.0 if horizontal else 10.0)
        )
        result = bus.execute(CreateLine(start=start, end=end))
        assert isinstance(result, Applied)
        (line,) = result.created_ids
        commands: list[Command] = [
            CreateConstraint(
                type=ConstraintType.HORIZONTAL if horizontal else ConstraintType.VERTICAL,
                refs=(Ref(entity=line, feature=Feature.CURVE),),
            )
        ]
        if previous is not None:
            commands.append(
                CreateConstraint(
                    type=ConstraintType.COINCIDENT,
                    refs=(
                        Ref(entity=previous, feature=Feature.END),
                        Ref(entity=line, feature=Feature.START),
                    ),
                )
            )
        for command in commands:
            found.outcome(bus.execute(command), bus.document)
        previous = line
    found.text(bus.queries.solve_status())
    return found.digest()


def _call(w: Workspace, found: Fingerprint, name: str, **arguments: object) -> dict[str, object]:
    outcome = w.call(ToolCall(id="pin", name=name, arguments=arguments))
    found.text(outcome.content)
    assert not outcome.is_error, outcome.content
    assert isinstance(outcome.content, dict)
    return outcome.content


def _made(content: dict[str, object]) -> str:
    created = content["created"]
    assert isinstance(created, list)
    return str(created[0])


def _ref(entity: str, feature: str) -> dict[str, str]:
    return {"entity": entity, "feature": feature}


def _at(radius: float, degrees: float) -> dict[str, float]:
    t = math.radians(degrees)
    return {"x": 120 + radius * math.cos(t), "y": 80 + radius * math.sin(t)}


def _star_layout(found: Fingerprint) -> tuple[Workspace, str, str, str, str]:
    w = Workspace(Document.empty())
    centre = _made(_call(w, found, "create_point", position=_at(0, 0), construction=True))
    _call(w, found, "create_constraint", type="fix", refs=[_ref(centre, "point")])
    axis = _made(
        _call(w, found, "create_line", start=_at(0, 0), end=_at(40, 90), construction=True)
    )
    _call(
        w,
        found,
        "create_constraint",
        type="coincident",
        refs=[_ref(centre, "point"), _ref(axis, "start")],
    )
    _call(w, found, "create_constraint", type="vertical", refs=[_ref(axis, "curve")])
    rings = []
    for radius in (25, 12):
        ring = _made(
            _call(w, found, "create_circle", center=_at(0, 0), radius=radius, construction=True)
        )
        _call(
            w,
            found,
            "create_constraint",
            type="concentric",
            refs=[_ref(ring, "curve"), _ref(centre, "point")],
        )
        rings.append(ring)
    return w, centre, axis, rings[0], rings[1]


def grid() -> str:
    found = Fingerprint()
    w = Workspace(Document.empty())
    origin = _made(_call(w, found, "create_point", position={"x": 0, "y": 0}))
    _call(w, found, "create_constraint", type="fix", refs=[_ref(origin, "point")])
    hole = _made(_call(w, found, "create_circle", center={"x": 60, "y": 25}, radius=3))
    for orientation, value in (("horizontal", 60), ("vertical", 25)):
        _call(
            w,
            found,
            "create_distance_dimension",
            a=_ref(origin, "point"),
            b=_ref(hole, "center"),
            orientation=orientation,
            offset=5,
            value=value,
        )
    _call(w, found, "linear_pattern", ids=[hole], count=5, spacing=30, count2=4, spacing2=30)
    found.outcome(None, w.document)
    return found.digest()


def half_star() -> str:
    found = Fingerprint()
    w, _, axis, _, _ = _star_layout(found)
    corners = [_at(25 if k % 2 == 0 else 12, 90 + 15 * k) for k in range(13)]
    lines = [
        _made(_call(w, found, "create_line", start=a, end=b))
        for a, b in itertools.pairwise(corners)
    ]
    for a, b in itertools.pairwise(lines):
        _call(
            w,
            found,
            "create_constraint",
            type="coincident",
            refs=[_ref(a, "end"), _ref(b, "start")],
        )
    _call(w, found, "mirror_entities", ids=lines, axis=axis)
    found.outcome(None, w.document)
    return found.digest()


def star() -> str:
    found = Fingerprint()
    w, centre, axis, outer, inner = _star_layout(found)
    right = _made(_call(w, found, "create_line", start=_at(12, 75), end=_at(25, 90)))
    left = _made(_call(w, found, "create_line", start=_at(25, 90), end=_at(12, 105)))
    for refs in (
        [_ref(right, "end"), _ref(left, "start")],
        [_ref(axis, "curve"), _ref(right, "end")],
        [_ref(outer, "curve"), _ref(right, "end")],
        [_ref(inner, "curve"), _ref(left, "end")],
    ):
        _call(w, found, "create_constraint", type="coincident", refs=refs)
    _call(
        w,
        found,
        "create_constraint",
        type="symmetric",
        refs=[_ref(left, "end"), _ref(right, "start"), _ref(axis, "curve")],
    )
    _call(w, found, "circular_pattern", ids=[right, left], center=centre, count=12)
    found.outcome(None, w.document)
    return found.digest()


PINNED: dict[str, tuple[Callable[[], str], str]] = {
    "rectangle": (
        lambda: session("rectangle"),
        "6532d64e8fbd43572cb2da383ef75fdc6aab237e34b63ffab1daa4eaee0fd15b",
    ),
    "ball-bearing": (
        lambda: session("ball-bearing"),
        "dba83d088eaa14329b40eb8141212cfcf0c9c8f23e3c112e1edf7c95d09e5095",
    ),
    "stress-plate-build": (
        lambda: session("stress-plate-build"),
        "81561e590c747be08417148a8a26b04a833bdf4ba8882c162572c2125e851f72",
    ),
    "chain-150": (chain, "210044d75aade4641f799fa732ac596bbe2764640cdfd06531a73aea6f39c215"),
    "grid-5x4": (grid, "e3b13530c98f3758d4cf2245523648d20f8171563f46d0ee960e42e94ac51b63"),
    "half-star": (half_star, "15fdac176fbfff40c83439487376ba6ab13b2a9a61a5e8d1619cd45f7050fecf"),
    "star-12": (star, "449908af3df2ac1a7158d0cc7405293647958e7209718b49d45b9377830bbb3e"),
}


@pytest.mark.parametrize("name", sorted(PINNED))
def test_the_fast_solver_gives_exactly_what_it_gave(name: str) -> None:
    work, digest = PINNED[name]
    assert work() == digest
