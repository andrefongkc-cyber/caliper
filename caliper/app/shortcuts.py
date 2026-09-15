"""Help → Keyboard Shortcuts: a sheet generated from the window's real actions.

Menu actions are read live, so a new action or a changed key shows up here without editing
this file. Canvas gestures aren't actions, so they're listed once below.
"""

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from caliper.app import theme
from caliper.app.tokens import SPACE

GESTURES: tuple[tuple[str, str], ...] = (
    ("Pan", "Two-finger scroll · middle-drag · Space+drag"),
    ("Zoom", "Pinch · Cmd+scroll · mouse wheel"),
    ("Type exact values", "Start typing a number while drawing · Tab between fields"),
    ("Edit a size", "Double-click a rectangle edge, circle, or arc"),
    ("Add to selection", "Shift+click"),
    ("Box select", "Drag from left to right (inside) or right to left (touching)"),
    ("Suspend snapping", "Hold Option"),
    ("Cancel", "Esc cancels the operation; Esc again leaves the tool · right-click"),
)


def keys(action: QAction) -> str:
    return " or ".join(
        s.toString(QKeySequence.SequenceFormat.NativeText) for s in action.shortcuts()
    )


def sections(menus: list[QMenu]) -> list[tuple[str, list[tuple[str, str]]]]:
    """(menu title, [(action name, keys)]) for every action with a shortcut."""
    result = []
    for menu in menus:
        rows = [
            (a.text().replace("&", ""), keys(a))
            for a in menu.actions()
            if not a.isSeparator() and a.shortcuts()
        ]
        if rows:
            result.append((menu.title().replace("&", ""), rows))
    result.append(("Canvas", list(GESTURES)))
    return result


def duplicate_shortcuts(actions: list[QAction]) -> dict[str, list[str]]:
    """Key sequences bound to more than one distinct action."""
    owners: dict[str, set[str]] = {}
    for action in actions:
        for sequence in action.shortcuts():
            text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            owners.setdefault(text, set()).add(action.objectName() or action.text())
    return {key: sorted(names) for key, names in owners.items() if len(names) > 1}


class ShortcutSheet(QDialog):
    def __init__(self, menus: list[QMenu], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.l, SPACE.l, SPACE.l, SPACE.l)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        for title, rows in sections(menus):
            group = QTreeWidgetItem([title.upper(), ""])
            group.setFont(0, theme.font(size=11, bold=True))
            group.setForeground(0, theme.TEXT_DIM)
            self.tree.addTopLevelItem(group)
            for name, shortcut in rows:
                item = QTreeWidgetItem([name, shortcut])
                item.setForeground(1, theme.TEXT_DIM)
                group.addChild(item)
            group.setExpanded(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        layout.addWidget(self.tree)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, 620)
