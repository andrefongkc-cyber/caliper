"""Functional line icons for tools and actions.

Drawn on a 20-unit grid with 1.5-unit strokes in `currentColor`, then rendered at the needed
size and device pixel ratio and tinted from the tokens: ink normally, accent when checked,
dim when disabled. Icons identify a tool; they never replace its label or tooltip.
"""

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from caliper.app import theme

_STROKE = (
    'fill="none" stroke="currentColor" stroke-width="1.5" '
    'stroke-linecap="round" stroke-linejoin="round"'
)

_SHAPES: dict[str, str] = {
    # An arrow pointer.
    "select": '<path d="M5 3 L5 16 L8.5 12.5 L11 17.5 L13 16.5 L10.5 11.5 L15 11.5 Z"/>',
    # A segment with its two endpoints.
    "line": '<path d="M4.5 15.5 L15.5 4.5"/><circle cx="4.5" cy="15.5" r="1.3"/>'
    '<circle cx="15.5" cy="4.5" r="1.3"/>',
    "circle": '<circle cx="10" cy="10" r="6.5"/><path d="M10 9.2 V10.8 M9.2 10 H10.8"/>',
    "arc": '<path d="M3.5 14 A6.5 6.5 0 0 1 16.5 14"/><path d="M10 13.2 V14.8 M9.2 14 H10.8"/>',
    "rectangle": '<rect x="3.5" y="5.5" width="13" height="9"/>',
    # A dimension line with extension lines and arrowheads.
    # A square corner with the corner itself rounded away.
    "fillet": '<path d="M4 16.5 V10 A5.5 5.5 0 0 1 9.5 4.5 H16"/>'
    '<path d="M4 6.5 H6 M5 5.5 V7.5" stroke-width="1.2"/>'
    '<path d="M14 16.5 H16 M15 15.5 V17.5" stroke-width="1.2"/>',
    "dimension": '<path d="M4 6 V14 M16 6 V14 M4 10 H16 M6.5 8 L4 10 L6.5 12 M13.5 8 L16 10 '
    'L13.5 12"/>',
    # Corners closing in on a frame.
    "fit": '<path d="M3.5 7.5 V3.5 H7.5 M12.5 3.5 H16.5 V7.5 M16.5 12.5 V16.5 H12.5 M7.5 16.5 '
    'H3.5 V12.5"/><rect x="7" y="7" width="6" height="6"/>',
    # A sketch point: a dot with a small cross through it.
    "point": '<circle cx="10" cy="10" r="2.2"/><path d="M10 3.5 V6.5 M10 13.5 V16.5 '
    'M3.5 10 H6.5 M13.5 10 H16.5"/>',
    # A right angle marked as kept: the perpendicular sign with its corner square.
    "constraint": '<path d="M3.5 16 H16.5 M10 16 V4"/><path d="M10 12.5 H13.5 V16" '
    'stroke-width="1.2"/>',
    # Two rays from a corner with the arc between them.
    "angle": '<path d="M3.5 16.5 H16.5 M3.5 16.5 L13 5"/><path d="M9.5 16.5 A6 6 0 0 0 7.4 11.9"/>',
    "grid": '<path d="M3.5 7.5 H16.5 M3.5 12.5 H16.5 M7.5 3.5 V16.5 M12.5 3.5 V16.5"/>',
    "undo": '<path d="M7.5 5 L4 8.5 L7.5 12 M4 8.5 H12 A4 4 0 0 1 12 16.5 H9"/>',
    "redo": '<path d="M12.5 5 L16 8.5 L12.5 12 M16 8.5 H8 A4 4 0 0 0 8 16.5 H11"/>',
    "measure": '<path d="M3 13 L13 3 L17 7 L7 17 Z M6 10 L7.5 11.5 M8.5 7.5 L10 9 M11 5 '
    'L12.5 6.5"/>',
}

_SHAPES["constrain"] = _SHAPES["constraint"]  # the tool that adds them

NAMES = frozenset(_SHAPES)
SIZES = (16, 20, 32)


def svg(name: str, color: QColor) -> bytes:
    body = _SHAPES[name]
    markup = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" '
        f'color="{color.name()}"><g {_STROKE}>{body}</g></svg>'
    )
    return markup.replace("currentColor", color.name()).encode()


def pixmap(name: str, size: int, color: QColor, ratio: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color)))
    image = QPixmap(round(size * ratio), round(size * ratio))
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


@cache
def icon(name: str) -> QIcon:
    """A QIcon with normal, checked, and disabled tints at 1x and 2x."""
    result = QIcon()
    tints = (
        (QIcon.Mode.Normal, QIcon.State.Off, theme.TEXT),
        (QIcon.Mode.Normal, QIcon.State.On, theme.ACCENT),
        (QIcon.Mode.Active, QIcon.State.Off, theme.TEXT),
        (QIcon.Mode.Active, QIcon.State.On, theme.ACCENT),
        (QIcon.Mode.Disabled, QIcon.State.Off, theme.TEXT_DIM),
        (QIcon.Mode.Disabled, QIcon.State.On, theme.TEXT_DIM),
    )
    for size in SIZES:
        for ratio in (1.0, 2.0):
            for mode, state, color in tints:
                result.addPixmap(pixmap(name, size, color, ratio), mode, state)
    return result
