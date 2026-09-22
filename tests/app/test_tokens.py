"""Design tokens are the single source of visual values."""

import re
from pathlib import Path

import pytest

from caliper.app import tokens

APP = Path(__file__).resolve().parents[2] / "caliper" / "app"
ALLOWED = {APP / "tokens.py"}
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
QCOLOR_LITERAL = re.compile(r"QColor\(\s*(\"|'|\d)")


@pytest.mark.parametrize(
    "path",
    sorted(p for p in APP.rglob("*.py") if p not in ALLOWED),
    ids=lambda p: str(p.relative_to(APP)),
)
def test_no_colour_literals_outside_tokens(path: Path) -> None:
    source = path.read_text()
    assert not HEX.findall(source), f"{path.name} has a hex colour; add a token instead"
    assert not QCOLOR_LITERAL.findall(source), f"{path.name} builds a QColor from a literal"


def test_palette_colours_are_valid_hex() -> None:
    for field in tokens.Palette.__dataclass_fields__:
        value = getattr(tokens.DARK, field)
        if isinstance(value, str):
            assert re.fullmatch(r"#[0-9a-f]{6}", value), f"{field}={value!r}"


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize(
    ("fg", "bg", "minimum"),
    [
        ("ink", "window", 4.5),
        ("ink", "field", 4.5),
        ("ink_dim", "window", 4.5),
        ("geometry", "canvas", 7.0),
        ("accent", "canvas", 3.0),
        ("failed", "window", 3.0),
        ("passed", "window", 3.0),
        ("agent", "canvas", 3.0),
        ("dimension", "canvas", 4.5),
        ("construction", "canvas", 4.5),
    ],
)
def test_contrast_meets_wcag(fg: str, bg: str, minimum: float) -> None:
    p = tokens.DARK
    assert contrast(getattr(p, fg), getattr(p, bg)) >= minimum
