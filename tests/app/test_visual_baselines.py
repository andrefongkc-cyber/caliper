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
from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QImage

from caliper.app import theme
from caliper.app.viewport import annotations
from caliper.app.viewport.painter import cosmetic_pen
from caliper.contracts.commands import (
    CreateArc,
    CreateCircle,
    CreateDistanceDimension,
    CreateLine,
    CreateRectangle,
)
from caliper.contracts.document import DistanceOrientation, Feature, Point2, Ref

BASELINES = Path(__file__).parent / "baselines"
SIZE = QSize(480, 320)
CHANNEL_TOLERANCE = 24
"""Per-channel difference that still counts as the same pixel (antialiasing jitter)."""
MAX_CHANGED_FRACTION = 0.001
"""Share of pixels allowed to differ: 0.1%, about 150 pixels at this size."""
UPDATE = os.environ.get("CALIPER_UPDATE_BASELINES") == "1"


VIEW = (1.5, 60.0, 260.0)
"""Scale in px/mm, and where the model origin lands on the canvas, in widget pixels."""


def render(window, view: tuple[float, float, float] = VIEW) -> QImage:
    canvas = window.canvas
    canvas.setFixedSize(SIZE)
    canvas.empty_hint.hide()  # text renders differently across machines
    canvas.view.scale, canvas.view.origin_x, canvas.view.origin_y = view
    canvas._layer = None  # drawn afresh for this view and these entities
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


CORNER_SCALE = 200.0
"""px/mm: the stress plate's 2 mm carry-on spans 400 px, and the dimension's text is off the
canvas (text renders differently across machines)."""
CORNER = (
    CORNER_SCALE,
    SIZE.width() / 2 - 229 * CORNER_SCALE,
    SIZE.height() / 2 + 159.5 * CORNER_SCALE,
)
"""The view of the plate's top-right corner: centred on (229, 159.5)."""


def plate_corner(window) -> None:
    """The stress plate's top-right corner: the top edge ends at the R12 fillet, 2 mm short of
    the Ø0.2 hole dimensioned to it. The edge carried on to the dimension is dashed; drawn
    solid it looked like the edge poking out past the arc (#52)."""
    session = window.session
    p = Point2
    (top,) = session.execute(CreateLine(start=p(x=228, y=160), end=p(x=12, y=160))).created_ids
    session.execute(CreateArc(center=p(x=228, y=148), radius=12, start_angle=0, sweep_angle=90))
    (hole,) = session.execute(CreateCircle(center=p(x=230, y=150), radius=0.1)).created_ids
    session.execute(
        CreateDistanceDimension(
            a=Ref(entity=hole, feature=Feature.CENTER),
            b=Ref(entity=top, feature=Feature.CURVE),
            orientation=DistanceOrientation.ALIGNED,
            offset=4,  # the dimension line between the edge's end and the hole, as on the plate
            value=10,
        )
    )


STATES: dict[str, Callable[..., None]] = {
    "empty": empty,
    "shapes": shapes,
    "selection_and_hover": selection_and_hover,
    "plate_corner": plate_corner,
}
VIEWS = {"plate_corner": CORNER}


@pytest.mark.parametrize("state", sorted(STATES))
def test_canvas_matches_baseline(window, state: str) -> None:
    STATES[state](window)
    image = render(window, VIEWS.get(state, VIEW))
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


def carry_on_strip() -> QRect:
    """The canvas pixels around the top edge's line from its end, x 228, to the foot, x 230."""
    scale, origin_x, origin_y = CORNER
    left, right = round(origin_x + 228.05 * scale), round(origin_x + 229.95 * scale)
    y = round(origin_y - 160 * scale)
    return QRect(left, y - 1, right - left, 3)


def test_the_plate_corner_fails_if_the_carry_on_is_drawn_solid_again(
    window, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deliberate break: the line carried on to the dimension drawn solid, as before #52.
    plate_corner(window)
    baseline = QImage(str(BASELINES / "canvas_plate_corner.png")).convertToFormat(
        QImage.Format.Format_RGB32
    )
    strip = carry_on_strip()
    assert changed_fraction(render(window, CORNER).copy(strip), baseline.copy(strip)) <= 0.02
    real = annotations.paint

    def solid(painter, plan, color) -> None:  # type: ignore[no-untyped-def]
        painter.set_pen(cosmetic_pen(color, theme.GUIDE_WIDTH))
        for a, b in plan.extensions:
            painter.line(a, b)
        real(
            painter,
            annotations.DimensionDrawing(
                lines=plan.lines, arrows=plan.arrows, label_at=plan.label_at, text=plan.text
            ),
            color,
        )

    monkeypatch.setattr(annotations, "paint", solid)
    reverted = render(window, CORNER)
    assert changed_fraction(reverted, baseline) > MAX_CHANGED_FRACTION
    # Solid, the gaps fill: about a fifth of the strip changes, against a 2% allowance.
    assert changed_fraction(reverted.copy(strip), baseline.copy(strip)) > 0.10
