"""Dark theme: palette, canvas colors, and compact chrome metrics.

Flat, neutral greys with one accent blue for selection, following SolidWorks/Fusion
conventions. No gradients, rounded cards, or decorative icons.
"""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

# Chrome
WINDOW = QColor("#2b2d31")
PANEL = QColor("#232428")
FIELD = QColor("#1b1c1f")
BORDER = QColor("#3a3c42")
TEXT = QColor("#d9dbe0")
TEXT_DIM = QColor("#8b8f98")
ACCENT = QColor("#3d8bfd")
ERROR = QColor("#e5534b")

# Canvas
CANVAS = QColor("#1c1d20")
GRID_MINOR = QColor("#26282c")
GRID_MAJOR = QColor("#31343a")
AXIS_X = QColor("#8a3b3b")
AXIS_Y = QColor("#3b7a4a")
GEOMETRY = QColor("#d4d6db")
HOVER = QColor("#8fc1ff")
SELECTED = ACCENT
PREVIEW = QColor("#e3b341")
DIMENSION = QColor("#9fb4c8")
SNAP = QColor("#e3b341")
RUBBER_BAND = QColor(61, 139, 253, 40)

GEOMETRY_WIDTH = 1.5
"""Logical pixels. Cosmetic pens, so Retina screens draw them at full device resolution."""
HIGHLIGHT_WIDTH = 2.5

STYLESHEET = f"""
QMainWindow::separator {{ background: {BORDER.name()}; width: 1px; height: 1px; }}
QToolBar {{ background: {WINDOW.name()}; border: none; border-bottom: 1px solid {BORDER.name()};
    spacing: 2px; padding: 2px 6px; }}
QToolBar QToolButton {{ padding: 3px 8px; border: 1px solid transparent; border-radius: 3px; }}
QToolBar QToolButton:hover {{ border-color: {BORDER.name()}; }}
QToolBar QToolButton:checked {{ background: {FIELD.name()}; border-color: {ACCENT.name()}; }}
QToolBar::separator {{ background: {BORDER.name()}; width: 1px; margin: 4px 6px; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {WINDOW.name()}; padding: 4px 8px; text-align: left;
    border-bottom: 1px solid {BORDER.name()}; }}
QStatusBar {{ background: {WINDOW.name()}; border-top: 1px solid {BORDER.name()};
    color: {TEXT_DIM.name()}; }}
QStatusBar::item {{ border: none; }}
QLineEdit, QComboBox {{ background: {FIELD.name()}; border: 1px solid {BORDER.name()};
    border-radius: 2px; padding: 2px 4px; selection-background-color: {ACCENT.name()}; }}
QLineEdit:focus {{ border-color: {ACCENT.name()}; }}
QLineEdit[invalid="true"] {{ border-color: {ERROR.name()}; }}
QLabel[role="section"] {{ color: {TEXT_DIM.name()}; font-weight: 600; padding-top: 6px; }}
QLabel[role="error"] {{ color: {ERROR.name()}; }}
"""


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
        QPalette.ColorRole.HighlightedText: QColor("#ffffff"),
        QPalette.ColorRole.PlaceholderText: TEXT_DIM,
    }
    for role, color in roles.items():
        palette.setColor(role, color)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, TEXT_DIM)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, TEXT_DIM)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, TEXT_DIM)
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
