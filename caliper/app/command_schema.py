"""What a person can run by name, derived from the contract's `Command` union.

The command palette lists these and builds a parameter form from each command's dataclass
fields, so a new command type shows up without shell changes. It's the same shape an AI
tool call fills in: typed fields with names, not a hand-written list of menu items. Qt-free.
"""

import dataclasses
import typing
from dataclasses import dataclass

from caliper.contracts.commands import Command, ModifyEntity
from caliper.contracts.document import EntityId, Point2


@dataclass(frozen=True, slots=True)
class Field:
    path: str
    """Where the value goes: "radius", or "center.x" for a point component."""
    label: str
    default: float | None


@dataclass(frozen=True, slots=True)
class CommandSpec:
    type: type
    title: str
    """Title case, as the bus labels it: "Create Rectangle"."""
    fields: tuple[Field, ...]
    uses_selection: bool
    """True if the command's `ids` come from the current selection."""


def _title(kind: str) -> str:
    return " ".join(word.capitalize() for word in kind.split("_"))


def _spec(command_type: type) -> CommandSpec | None:
    """None for commands the palette can't express yet (references, arbitrary changes)."""
    if command_type is ModifyEntity:
        return None  # edited through the properties panel and on the canvas
    hints = typing.get_type_hints(command_type)
    fields: list[Field] = []
    uses_selection = False
    for f in dataclasses.fields(command_type):
        hint = hints[f.name]
        if f.name == "id":
            continue
        if hint is float:
            fields.append(Field(f.name, f.name.replace("_", " ").capitalize(), None))
        elif hint is Point2:
            label = f.name.replace("_", " ").capitalize()
            fields += [Field(f"{f.name}.x", f"{label} X", 0.0), Field(f"{f.name}.y", "Y", 0.0)]
        elif hint == tuple[EntityId, ...] and f.name == "ids":
            uses_selection = True
        else:
            return None
    kind: str = command_type.kind  # type: ignore[attr-defined]
    return CommandSpec(command_type, _title(kind), tuple(fields), uses_selection)


def command_specs() -> tuple[CommandSpec, ...]:
    specs = (_spec(t) for t in typing.get_args(Command))
    return tuple(s for s in specs if s is not None)


def build(spec: CommandSpec, values: dict[str, float], selection: frozenset[EntityId]) -> Command:
    """Assemble the command. Every field in `spec.fields` must have a value."""
    kwargs: dict[str, object] = {}
    points: dict[str, dict[str, float]] = {}
    for field in spec.fields:
        name, _, axis = field.path.partition(".")
        if axis:
            points.setdefault(name, {})[axis] = values[field.path]
        else:
            kwargs[name] = values[field.path]
    for name, xy in points.items():
        kwargs[name] = Point2(x=xy["x"], y=xy["y"])
    if spec.uses_selection:
        kwargs["ids"] = tuple(sorted(selection))
    return spec.type(**kwargs)  # type: ignore[no-any-return]


def matches(query: str, title: str) -> bool:
    """Every query word appears in the title, in any order: "rect cre" finds Create Rectangle."""
    words = query.lower().split()
    return all(word in title.lower() for word in words)
