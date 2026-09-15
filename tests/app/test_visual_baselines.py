"""Pixel baselines for the canvas in fixed states.

Only the canvas is compared, in states without text, because text rendering varies between
machines while lines and fills don't. Regenerate after an intended visual change with:

    CALIPER_UPDATE_BASELINES=1 QT_QPA_PLATFORM=offscreen \
        uv run pytest tests/app/test_visual_baselines.py
"""

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from caliper.contracts.commands import CreateArc, CreateCircle, CreateLine, CreateRectangle
from caliper.contracts.document import Point2

BASELINES = Path(__file__).parent / "baselines"
SIZE = QSize(480, 320)
CHANNEL_TOLERANCE = 24
"""Per-channel difference that still counts as the same pixel (antialiasing jitter)."""
MAX_CHANGED_FRACTION = 0.001
"""Share of pixels allowed to differ: 0.1%, about 150 pixels at this size."""
UPDATE = os.environ.get("CALIPER_UPDATE_BASELINES") == "1"


def render(window) -> QImage:
    canvas = window.canvas
    canvas.setFixedSize(SIZE)
    view = canvas.view
    view.scale, view.origin_x, view.origin_y = 1.5, 60.0, 260.0
    return canvas.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)


def changed_fraction(a: QImage, b: QImage) -> float:
    assert a.size() == b.size(), f"size {a.size()} != {b.size()}"
    changed = 0
    for y in range(a.height()):
        for x in range(a.width()):
            pa, pb = a.pixelColor(x, y), b.pixelColor(x, y)
            if (
                abs(pa.red() - pb.red()) > CHANNEL_TOLERANCE
                or abs(pa.green() - pb.green()) > CHANNEL_TOLERANCE
                or abs(pa.blue() - pb.blue()) > CHANNEL_TOLERANCE
            ):
                changed += 1
    return changed / (a.width() * a.height())


def draw_shapes(window) -> tuple[str, str]:
    session = window.session
    (rect,) = session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=120, height=50)
    ).created_ids
    (circle,) = session.execute(CreateCircle(center=Point2(x=190, y=25), radius=25)).created_ids
    session.execute(
        CreateArc(center=Point2(x=60, y=100), radius=30, start_angle=0, sweep_angle=180)
    )
    session.execute(CreateLine(start=Point2(x=-20, y=-20), end=Point2(x=-20, y=70)))
    return rect, circle


def empty(window) -> None:
    pass


def shapes(window) -> None:
    draw_shapes(window)


def selection_and_hover(window) -> None:
    rect, circle = draw_shapes(window)
    window.session.set_selection(frozenset({rect}))
    window.session.set_hover(circle)


STATES: dict[str, Callable[..., None]] = {
    "empty": empty,
    "shapes": shapes,
    "selection_and_hover": selection_and_hover,
}


@pytest.mark.parametrize("state", sorted(STATES))
def test_canvas_matches_baseline(window, state: str) -> None:
    STATES[state](window)
    image = render(window)
    path = BASELINES / f"canvas_{state}.png"
    if UPDATE or not path.exists():
        if not UPDATE:
            pytest.fail(f"no baseline at {path}; run with CALIPER_UPDATE_BASELINES=1 to create it")
        BASELINES.mkdir(exist_ok=True)
        assert image.save(str(path))
        return
    baseline = QImage(str(path)).convertToFormat(QImage.Format.Format_RGB32)
    fraction = changed_fraction(image, baseline)
    if fraction > MAX_CHANGED_FRACTION:
        image.save(str(path.with_name(f"{path.stem}.actual.png")))
    assert fraction <= MAX_CHANGED_FRACTION, (
        f"{fraction:.2%} of pixels changed; see {path.stem}.actual.png"
    )


def test_a_one_pixel_shift_is_detected(window) -> None:
    shapes(window)
    before = render(window)
    window.canvas.view.origin_x += 1
    after = window.canvas.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    assert changed_fraction(before, after) > MAX_CHANGED_FRACTION
