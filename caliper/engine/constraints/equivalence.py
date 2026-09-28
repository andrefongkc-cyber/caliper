"""Is the optimized solver's answer the same solution as the reference's, and if not, where
do they part? For tests and development (`bench/numerics.py`); nothing here reaches users.

Two documents solved from the same document by the same command are equivalent when both
hold every relation (`sketch.residuals`) and every stored number agrees within
`tolerance.PRECISION` of the sketch's scale: that, not round-off, is how exactly relations fix
geometry where they meet tangentially (see `tolerance`). A difference within
`tolerance.UNCHANGED` is no difference to the solver: the same geometry. A structural
difference (an entity missing from one, or another kind, reference, or type) is always
significant.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from caliper.contracts.document import Document, EntityId
from caliper.engine.constraints import tolerance
from caliper.engine.constraints.model import PARAMS, read
from caliper.engine.constraints.sketch import GEOMETRY
from caliper.engine.io.canonical import JSON
from caliper.engine.io.codec import encode


@dataclass(frozen=True, slots=True)
class Difference:
    """One stored value that differs between the reference's document and the candidate's."""

    entity: EntityId
    field: str
    """Where in the entity's stored form: `start.x`, `value`, `refs`, or `` for the whole
    entity when one document lacks it."""
    reference: JSON
    candidate: JSON
    absolute: float
    """How far apart two numbers are; infinite for anything else."""
    relative: float
    """`absolute` over the reference number's magnitude (at least 1)."""
    tolerance: float
    """`tolerance.PRECISION` times the sketch's scale: the most two solutions may differ."""
    same: bool
    """Within `tolerance.UNCHANGED` of the scale: the same geometry, to the solver."""

    @property
    def significant(self) -> bool:
        """More than two solutions of the same relations may differ by: another solution."""
        return not self.absolute <= self.tolerance


def scale(document: Document) -> float:
    """The scale of a sketch's geometry, as the solver's tolerances use it."""
    return tolerance.scale(
        read(entity, path)
        for entity in document.entities.values()
        if isinstance(entity, GEOMETRY)
        for path in PARAMS[type(entity)]
    )


def differences(reference: Document, candidate: Document) -> list[Difference]:
    """Every stored value that differs between two documents, the largest first."""
    size = scale(reference)
    precision = tolerance.PRECISION * size
    same = tolerance.UNCHANGED * size
    found: list[Difference] = []
    for id in sorted(reference.entities.keys() | candidate.entities.keys()):
        a, b = reference.entities.get(id), candidate.entities.get(id)
        if a is b:
            continue
        if a is None or b is None:
            left = None if a is None else encode(a)
            right = None if b is None else encode(b)
            found.append(
                Difference(id, "", left, right, float("inf"), float("inf"), precision, False)
            )
            continue
        for path, x, y in _leaves(encode(a), encode(b)):
            if _number(x) and _number(y):
                gap = abs(float(x) - float(y))  # type: ignore[arg-type]
                if path.rpartition(".")[2] in _TURNS:  # 359.99999999999994 is 0, near enough
                    gap = min(gap, abs(360.0 - gap))
                relative = gap / max(1.0, abs(float(x)))  # type: ignore[arg-type]
                found.append(Difference(id, path, x, y, gap, relative, precision, gap <= same))
            else:
                found.append(
                    Difference(id, path, x, y, float("inf"), float("inf"), precision, False)
                )
    found.sort(key=lambda d: (-d.absolute, d.entity, d.field))
    return found


def report(found: list[Difference], limit: int = 20) -> str:
    """The differences as a table, the largest first, for a failing test or a terminal."""
    if not found:
        return "no differences"
    significant = sum(d.significant for d in found)
    same = sum(d.same for d in found)
    lines = [
        f"{len(found)} differences: {significant} significant, "
        f"{len(found) - significant - same} within precision, {same} the same geometry "
        f"(precision {found[0].tolerance:.3g})",
        f"{'entity':8} {'field':16} {'reference':>22} {'candidate':>22} "
        f"{'absolute':>10} {'relative':>10}  verdict",
    ]
    for d in found[:limit]:
        verdict = "SIGNIFICANT" if d.significant else "same" if d.same else "precision"
        lines.append(
            f"{d.entity:8} {d.field or '(entity)':16} {_shown(d.reference):>22} "
            f"{_shown(d.candidate):>22} {d.absolute:10.3g} {d.relative:10.3g}  {verdict}"
        )
    if len(found) > limit:
        lines.append(f"... and {len(found) - limit} more")
    return "\n".join(lines)


_TURNS = frozenset({"start_angle", "label_angle"})
"""Fields that are directions, in degrees: the same a full turn apart."""


def _leaves(a: JSON, b: JSON, path: str = "") -> Iterator[tuple[str, JSON, JSON]]:
    """The places two encoded values differ, as (path, left, right)."""
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        for key in sorted(a.keys() | b.keys()):
            yield from _leaves(a.get(key), b.get(key), f"{path}.{key}" if path else key)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for n, (x, y) in enumerate(zip(a, b, strict=True)):
            yield from _leaves(x, y, f"{path}[{n}]")
    elif a != b or type(a) is not type(b):
        yield path, a, b


def _number(value: JSON) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _shown(value: JSON) -> str:
    return repr(value) if _number(value) else str(value)[:22]
