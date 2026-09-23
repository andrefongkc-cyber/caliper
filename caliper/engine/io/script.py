"""Command scripts: a list of commands to run headlessly.

    {"format": "caliper.script", "schema_version": 1,
     "commands": [{"kind": "create_rectangle", ...}]}

Commands are decoded structurally; the bus reports bad values as Rejected errors.
"""

from pathlib import Path

from caliper.contracts.commands import Command
from caliper.contracts.errors import LoadError
from caliper.engine.io import canonical
from caliper.engine.io.codec import DecodeError, decode_command

FORMAT = "caliper.script"
SCHEMA_VERSION = 1


def loads(text: str) -> tuple[Command, ...]:
    data = canonical.parse(text)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise LoadError("not a Caliper command script")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise LoadError(f"unsupported script schema_version {data.get('schema_version')!r}")
    if unknown := data.keys() - {"format", "schema_version", "commands"}:
        raise LoadError(f"unknown top-level field(s): {', '.join(sorted(unknown))}")
    commands = data.get("commands")
    if not isinstance(commands, list):
        raise LoadError("commands must be a list")
    try:
        return tuple(decode_command(item, f"commands[{i}]") for i, item in enumerate(commands))
    except DecodeError as e:
        raise LoadError(str(e)) from e


def load(path: Path) -> tuple[Command, ...]:
    return loads(path.read_text(encoding="utf-8"))
