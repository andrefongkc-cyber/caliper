"""Design tokens: the only place the shell defines a colour, a size, or a duration.

Everything visual reads from here, through `theme` for Qt objects. A test fails if a hex
colour or `QColor(` appears anywhere else in `caliper/app/`.

Colour roles, not colour names: `accent` is selection and focus; `agent` is reserved for
changes proposed by an AI agent (P6); `pass` / `fail` are check results and errors. State is
never shown by colour alone: pair it with a shape, a badge, or text.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class Palette:
    # Chrome
    window: str
    panel: str
    field: str
    border: str
    ink: str
    ink_dim: str
    ink_on_accent: str
    # Roles
    accent: str
    agent: str
    passed: str
    failed: str
    # Canvas
    canvas: str
    grid_minor: str
    grid_major: str
    axis_x: str
    axis_y: str
    geometry: str
    construction: str
    """Construction geometry: real and constrained, but not part of any profile."""
    constrained: str
    """Geometry with no degrees of freedom left (the status bar says so in words too)."""
    hover: str
    preview: str
    dimension: str
    snap: str
    rubber_band_alpha: int
    """Opacity (0-255) of the accent fill inside a box selection."""


DARK = Palette(
    window="#2b2d31",
    panel="#232428",
    field="#1b1c1f",
    border="#3a3c42",
    ink="#d9dbe0",
    ink_dim="#9a9ea7",
    ink_on_accent="#ffffff",
    accent="#3d8bfd",
    agent="#c792ea",
    passed="#4fc48c",
    failed="#e5534b",
    canvas="#1c1d20",
    grid_minor="#26282c",
    grid_major="#31343a",
    axis_x="#8a3b3b",
    axis_y="#3b7a4a",
    geometry="#d4d6db",
    construction="#8a8f99",
    constrained="#56b6c2",
    hover="#8fc1ff",
    preview="#e3b341",
    dimension="#9fb4c8",
    snap="#e3b341",
    rubber_band_alpha=40,
)


@dataclass(frozen=True, slots=True)
class Space:
    """Spacing steps in logical pixels. Layouts use these, never ad-hoc numbers."""

    xxs: int = 2
    xs: int = 4
    s: int = 6
    m: int = 8
    l: int = 12  # noqa: E741
    xl: int = 16


@dataclass(frozen=True, slots=True)
class TypeScale:
    """Point sizes on top of the system UI font (SF Pro on macOS)."""

    caption: int = 11
    body: int = 13
    heading: int = 13
    mono: int = 12
    heading_weight: int = 600


@dataclass(frozen=True, slots=True)
class Stroke:
    """Canvas line widths in logical pixels; cosmetic pens keep them sharp on Retina."""

    guide: float = 1.0
    geometry: float = 1.5
    highlight: float = 2.5


@dataclass(frozen=True, slots=True)
class Radius:
    control: int = 3
    field: int = 2


SPACE = Space()
TYPE = TypeScale()
STROKE = Stroke()
RADIUS = Radius()
