"""Qt objects built from the design tokens: colours, fonts, palette, stylesheet.

Flat, neutral greys with one accent for selection, following SolidWorks/Fusion conventions.
No gradients, rounded cards, or decorative icons. Values live in `tokens`; this module only
turns them into Qt types.
"""

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from caliper.app.tokens import DARK, RADIUS, SPACE, STROKE, TYPE, Palette

P: Palette = DARK

# Chrome
WINDOW = QColor(P.window)
PANEL = QColor(P.panel)
FIELD = QColor(P.field)
BORDER = QColor(P.border)
TEXT = QColor(P.ink)
TEXT_DIM = QColor(P.ink_dim)
ACCENT = QColor(P.accent)
AGENT = QColor(P.agent)
PASSED = QColor(P.passed)
ERROR = QColor(P.failed)

# Canvas
CANVAS = QColor(P.canvas)
GRID_MINOR = QColor(P.grid_minor)
GRID_MAJOR = QColor(P.grid_major)
AXIS_X = QColor(P.axis_x)
AXIS_Y = QColor(P.axis_y)
GEOMETRY = QColor(P.geometry)
CONSTRUCTION = QColor(P.construction)
HOVER = QColor(P.hover)
SELECTED = ACCENT
PREVIEW = QColor(P.preview)
DIMENSION = QColor(P.dimension)
SNAP = QColor(P.snap)
RUBBER_BAND = QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), P.rubber_band_alpha)

GUIDE_WIDTH = STROKE.guide
GEOMETRY_WIDTH = STROKE.geometry
HIGHLIGHT_WIDTH = STROKE.highlight


def font(size: int = TYPE.body, *, bold: bool = False, mono: bool = False) -> QFont:
    """The system UI font (or fixed-width font) at a type-scale size."""
    if mono:
        f = QFont("Menlo")
        f.setStyleHint(QFont.StyleHint.Monospace)
    else:
        f = QApplication.font() if QApplication.instance() else QFont()
    f.setPointSize(size)
    if bold:
        f.setWeight(QFont.Weight(TYPE.heading_weight))
    return f


def _stylesheet() -> str:
    s, r = SPACE, RADIUS
    return f"""
QMainWindow::separator {{ background: {P.border}; width: 1px; height: 1px; }}
QToolBar {{ background: {P.window}; border: none; border-bottom: 1px solid {P.border};
    spacing: {s.xxs}px; padding: {s.xxs}px {s.s}px; }}
QToolBar QToolButton {{ padding: {s.xs - 1}px {s.m}px; border: 1px solid transparent;
    border-radius: {r.control}px; color: {P.ink}; }}
QToolBar QToolButton:hover {{ border-color: {P.border}; }}
QToolBar QToolButton:checked {{ background: {P.field}; border-color: {P.accent}; }}
QToolBar::separator {{ background: {P.border}; width: 1px; margin: {s.xs}px {s.s}px; }}
QDockWidget::title {{ background: {P.window}; padding: {s.xs}px {s.m}px; text-align: left;
    border-bottom: 1px solid {P.border}; }}
QStatusBar {{ background: {P.window}; border-top: 1px solid {P.border}; color: {P.ink_dim}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ padding: {s.xxs}px {s.m}px; }}
QLineEdit, QComboBox {{ background: {P.field}; border: 1px solid {P.border};
    border-radius: {r.field}px; padding: {s.xxs}px {s.xs}px;
    selection-background-color: {P.accent}; }}
QLineEdit:focus {{ border-color: {P.accent}; }}
QLineEdit[invalid="true"] {{ border-color: {P.failed}; }}
QLabel[role="section"] {{ color: {P.ink_dim}; font-weight: {TYPE.heading_weight};
    padding-top: {s.s}px; }}
QLabel[role="error"] {{ color: {P.failed}; }}
QTabWidget#browser-tabs::pane {{ border: none; }}
QTabWidget#browser-tabs, QTabWidget#browser-tabs QTabBar {{ background: {P.window}; }}
QTabWidget#browser-tabs QTabBar {{ border-bottom: 1px solid {P.border}; }}
QWidget#checks {{ background: {P.panel}; }}
QTabBar::tab {{ background: {P.window}; color: {P.ink_dim}; padding: {s.s}px {s.l}px;
    border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {P.ink}; border-bottom-color: {P.accent}; }}
QTabBar::tab:hover {{ color: {P.ink}; }}
QTreeWidget, QListWidget {{ background: {P.panel}; border: none; outline: none; }}
QTreeWidget::item, QListWidget::item {{ padding: {s.xxs}px 0; }}
QTreeWidget::item:selected {{ background: {P.field}; color: {P.ink}; }}
QTreeWidget::item:hover {{ background: {P.field}; }}
QTreeWidget::item:selected:!has-children {{ color: {P.accent}; }}
QPushButton {{ background: {P.field}; color: {P.ink}; border: 1px solid {P.border};
    border-radius: {r.control}px; padding: {s.xs}px {s.l}px; }}
QPushButton:hover {{ border-color: {P.ink_dim}; }}
QPushButton:default {{ border-color: {P.accent}; }}
QPushButton:pressed {{ background: {P.panel}; }}
QFrame#prompt-bar {{ background: {P.window}; border-top: 1px solid {P.border}; }}
QLineEdit#prompt {{ padding: {s.s}px {s.m}px; font-size: 13px; }}
QLabel#agent-chip {{ color: {P.agent}; border: 1px solid {P.agent}; border-radius: {r.control}px;
    padding: {s.xxs}px {s.s}px; font-size: 11px; }}
QFrame#proposal-card {{ background: {P.panel}; border: 1px solid {P.agent};
    border-radius: 4px; }}
QLabel#proposal-eyebrow {{ color: {P.agent}; font-size: 10px; font-weight: 700;
    letter-spacing: 1px; }}
QLabel#proposal-commands {{ color: {P.ink_dim}; background: {P.field};
    padding: {s.s}px; border-radius: {r.field}px; }}
QPushButton#accept {{ background: {P.agent}; color: {P.field}; border-color: {P.agent};
    font-weight: 600; }}
QPushButton#accept:disabled {{ background: {P.field}; color: {P.ink_dim};
    border-color: {P.border}; }}
QLabel#empty-hint {{ color: {P.ink_dim}; font-size: 13px; line-height: 150%; }}
QToolTip {{ background: {P.panel}; color: {P.ink}; border: 1px solid {P.border};
    padding: {s.xs}px {s.s}px; }}
"""


STYLESHEET = _stylesheet()


def apply(app: QApplication) -> None:
    app.setStyle(QStyleFactory.create("Fusion"))
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: WINDOW,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: FIELD,
        QPalette.ColorRole.AlternateBase: PANEL,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: WINDOW,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.ToolTipBase: PANEL,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: QColor(P.ink_on_accent),
        QPalette.ColorRole.PlaceholderText: TEXT_DIM,
    }
    for role, color in roles.items():
        palette.setColor(role, color)
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, TEXT_DIM)
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
