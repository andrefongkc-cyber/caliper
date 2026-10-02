"""Command scripts: a list of commands to run headlessly.

    {"format": "caliper.script", "schema_version": 1,
     "commands": [{"kind": "create_rectangle", ...}]}

A script runs on a new part, one sketch on XY (`Document.empty()`). Schema 2 adds `"part":
"empty"`, a part with no sketch at all, which is where the app's 3D tab starts (ADR 0015), so
what it records replays exactly. A script without `part` reads as before.

Commands are decoded structurally; the bus reports bad values as Rejected errors.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from caliper.contracts.commands import Command
from caliper.contracts.document import Document
from caliper.contracts.errors import LoadError
from caliper.engine import part
from caliper.engine.io import canonical
from caliper.engine.io.codec import DecodeError, decode_command, encode

FORMAT = "caliper.script"
SCHEMA_VERSION = 2
EMPTY = "empty"
"""`part` for a script that starts from a part with no sketch."""


@dataclass(frozen=True, slots=True)
class Script:
    commands: tuple[Command, ...]
    empty: bool = False
    """Starts from a part with no sketch (`part.no_sketch()`), not a new part's one sketch."""

    def start(self) -> Document:
        """The document the commands run on."""
        return part.no_sketch() if self.empty else Document.empty()


def read(text: str) -> Script:
    data = canonical.parse(text)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise LoadError("not a Caliper command script")
    version = data.get("schema_version")
    if version not in (1, SCHEMA_VERSION):
        raise LoadError(f"unsupported script schema_version {version!r}")
    known = {"format", "schema_version", "commands"} | ({"part"} if version == 2 else set())
    if unknown := data.keys() - known:
        raise LoadError(f"unknown top-level field(s): {', '.join(sorted(unknown))}")
    start = data.get("part")
    if start not in (None, EMPTY):
        raise LoadError(f"part must be {EMPTY!r}, or left out for a new part")
    commands = data.get("commands")
    if not isinstance(commands, list):
        raise LoadError("commands must be a list")
    try:
        decoded = tuple(decode_command(item, f"commands[{i}]") for i, item in enumerate(commands))
    except DecodeError as e:
        raise LoadError(str(e)) from e
    return Script(commands=decoded, empty=start == EMPTY)


def loads(text: str) -> tuple[Command, ...]:
    return read(text).commands


def load(path: Path) -> tuple[Command, ...]:
    return loads(path.read_text(encoding="utf-8"))


def dumps(commands: tuple[Command, ...], *, empty: bool = False) -> str:
    """A script of `commands`: schema 1 on a new part, as every older script is, or schema 2
    when it starts from a part with no sketch."""
    data: dict[str, object] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION if empty else 1,
        "commands": encode(commands),
    }
    if empty:
        data["part"] = EMPTY
    return json.dumps(data, indent=2) + "\n"
