"""Canonical JSON: the exact byte encoding every Caliper file uses (ADR 0005)."""

import json
from collections.abc import Sequence
from typing import NoReturn

from caliper.contracts.errors import Error

type JSON = bool | int | float | str | list[JSON] | dict[str, JSON] | None


class LoadError(ValueError):
    """An input file isn't valid. `errors` lists validation problems, if there were any."""

    def __init__(self, message: str, errors: Sequence[Error] = ()) -> None:
        details = "; ".join(f"{e.field}: {e.message}" if e.field else e.message for e in errors)
        super().__init__(f"{message}: {details}" if details else message)
        self.errors = tuple(errors)


def dumps(data: JSON) -> str:
    """Sorted keys, two-space indent, shortest round-trip floats, no NaN, trailing newline."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def parse(text: str) -> object:
    """Parse strictly: NaN, Infinity, and duplicate keys are errors, not silently accepted."""

    def reject_constant(name: str) -> NoReturn:
        raise LoadError(f"{name} is not allowed in Caliper files")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        data = dict(pairs)
        if len(data) != len(pairs):
            raise LoadError("duplicate key in JSON object")
        return data

    try:
        return json.loads(text, parse_constant=reject_constant, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as e:
        raise LoadError(f"invalid JSON: {e}") from e
