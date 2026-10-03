"""The window's chrome that folds away (2026-10-03, at Andre's request): the side panels behind
strips on the view's edges, and the tool bar's tools in a tray that slides out to the right of
the line after the 2D/3D switch, and back into it.

Each is one checkable action, so its menu item, its control, and its shortcut agree. Folded
away, the tools' shortcuts still work: they are the window's actions, not the buttons'.
"""

from typing import Literal

from PySide6.QtCore import QEasingCurve, QEvent, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QAction, QKeySequence, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolBar, QToolButton, QWidget

SLIDE_MS = 160
"""How long the tray takes to slide out or back."""
EDGE_WIDTH = 12
POINT_LEFT = "\u2039"
"""The chevrons: single angle quotation marks, lighter than arrows."""
POINT_RIGHT = "\u203a"


class EdgeToggle(QToolButton):
    """A strip down one edge of the view that shows or hides the panels beyond it. Its chevron
    points the way they'll go."""

    def __init__(self, action: QAction, side: Literal["left", "right"], what: str) -> None:
        super().__init__()
        self.side = side
        self.what = what
        """What it folds, for the tooltip: "the left panel"."""
        self.setObjectName(f"{side}-edge")
        self.setDefaultAction(action)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setFixedWidth(EDGE_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        action.toggled.connect(self._point)
        self._point(action.isChecked())

    def _point(self, shown: bool) -> None:
        action = self.defaultAction()
        action.setIconText(POINT_LEFT if shown == (self.side == "left") else POINT_RIGHT)
        keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        action.setToolTip(f"{'Hide' if shown else 'Show'} {self.what} ({keys})")


class SlidingTray(QWidget):
    """The tools, in a tray that slides out to the right of its handle and back into it.

    The handle is the line after the 2D/3D switch, with a chevron; the tray is as wide as the
    part of the tools that's out, and the tools' right end follows its right edge, so they come
    out from behind the line. Shut, the tools are hidden. In a window too narrow for them all,
    the tray takes what's left and the tools that don't fit wait behind the bar's » menu, as
    they did on the bar itself; `row` puts the handle and the tray in one widget for the bar,
    so the bar gives the tray that room rather than hiding it whole."""

    def __init__(self, bar: QToolBar, action: QAction) -> None:
        super().__init__()
        self.setObjectName("tool-tray")
        self.bar = bar
        bar.setParent(self)
        self.action = action
        self.handle = QToolButton()
        self.handle.setObjectName("tray-handle")
        self.handle.setDefaultAction(action)
        self.handle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.handle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.out = 1.0 if action.isChecked() else 0.0
        """How far out the tray is: 0 shut, 1 open."""
        self._slide = QVariantAnimation(self)
        self._slide.setDuration(SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.valueChanged.connect(self._reveal)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        action.toggled.connect(self._toggled)
        self._point(action.isChecked())
        self._reveal(self.out)

    def sizeHint(self) -> QSize:  # noqa: N802
        full = self.bar.sizeHint()
        return QSize(round(self.out * full.width()), full.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self.bar.sizeHint().height())

    def row(self) -> QWidget:
        """The handle, then the tray, as one widget that takes the rest of a tool bar."""
        row = QWidget()
        row.setObjectName("tray-row")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(0)
        line.addWidget(self.handle)
        line.addWidget(self)
        line.addStretch(1)
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return row

    @property
    def sliding(self) -> bool:
        return self._slide.state() == QVariantAnimation.State.Running

    def refit(self) -> None:
        """The tools changed size (their labels dropped in a narrow window, say)."""
        self.updateGeometry()
        self._place()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.LayoutRequest:  # the tools asked for another size
            self.refit()
        return super().event(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self._place()
        super().resizeEvent(event)

    def _toggled(self, shown: bool) -> None:
        self._point(shown)
        self._slide.stop()
        self._slide.setStartValue(self.out)
        self._slide.setEndValue(1.0 if shown else 0.0)
        self._slide.start()

    def _point(self, shown: bool) -> None:
        self.action.setIconText(POINT_LEFT if shown else POINT_RIGHT)
        self.action.setToolTip("Slide the tools back" if shown else "Slide the tools out")

    def _reveal(self, out: object) -> None:
        self.out = float(out)  # type: ignore[arg-type]
        self.bar.setVisible(self.out > 0.0)
        self.updateGeometry()
        self._place()

    def _place(self) -> None:
        full = self.bar.sizeHint().width()
        x = min(0, round(self.out * full) - full)  # out from behind the handle
        self.bar.setGeometry(x, 0, min(full, self.width() - x), self.height())
