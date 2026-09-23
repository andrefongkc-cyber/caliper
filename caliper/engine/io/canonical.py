"""Canonical JSON: the exact byte encoding every Caliper file uses (ADR 0005)."""

import json
from typing import NoReturn

# Re-exported: it lived here until after V1, and the shell still imports it from here.
from caliper.contracts.errors import LoadError as LoadError

type JSON = bool | int | float | str | list[JSON] | dict[str, JSON] | None


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
