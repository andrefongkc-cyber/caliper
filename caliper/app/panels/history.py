"""History: every change with who made it, newest first. Click a row to go back to it.

Undone changes stay listed, dimmed, until a new change replaces them, just like redo.
"""

import time

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from caliper.app import theme
from caliper.app.session import Author, DocumentSession
from caliper.app.tokens import RADIUS, SPACE

POSITION_ROLE = Qt.ItemDataRole.UserRole
AUTHOR_ROLE = Qt.ItemDataRole.UserRole + 1
WHEN_ROLE = Qt.ItemDataRole.UserRole + 2
UNDONE_ROLE = Qt.ItemDataRole.UserRole + 3


def ago(at: float, now: float) -> str:
    seconds = max(0, int(now - at))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return time.strftime("%H:%M", time.localtime(at))


class _Row(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        undone = bool(index.data(UNDONE_ROLE))
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, theme.FIELD)
        rect = option.rect.adjusted(SPACE.m, 0, -SPACE.m, 0)
        painter.save()
        font = QFont(option.font)
        font.setItalic(undone)
        painter.setFont(font)
        right = rect.right()
        when = index.data(WHEN_ROLE) or ""
        author = index.data(AUTHOR_ROLE) or ""
        small = theme.font(size=11)
        painter.setFont(small)
        metrics = painter.fontMetrics()
        painter.setPen(theme.TEXT_DIM)
        painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, when)
        reserved = metrics.horizontalAdvance(when)
        if author:
            chip_w = metrics.horizontalAdvance(author) + 2 * SPACE.s
            chip = QRect(
                right - reserved - SPACE.m - chip_w,
                rect.center().y() - metrics.height() // 2 - 1,
                chip_w,
                metrics.height() + 2,
            )
            painter.setPen(theme.AGENT if author == Author.AGENT else theme.TEXT_DIM)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(chip, RADIUS.control, RADIUS.control)
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, author)
            reserved += SPACE.m + chip_w
        painter.setFont(font)
        painter.setPen(theme.TEXT_DIM if undone else theme.TEXT)
        label_rect = rect.adjusted(0, 0, -(reserved + SPACE.m), 0)
        label = painter.fontMetrics().elidedText(
            index.data(), Qt.TextElideMode.ElideRight, label_rect.width()
        )
        painter.drawText(
            label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label
        )
        painter.restore()


class HistoryList(QListWidget):
    def __init__(self, session: DocumentSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("history")
        self.setItemDelegate(_Row(self))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setUniformItemSizes(True)
        self.itemClicked.connect(self._go_to)
        session.history_changed.connect(self.rebuild)
        self.rebuild()

    def rebuild(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.clear()
        history, position = self.session.history, self.session.history_position
        for index in range(len(history) - 1, -1, -1):
            entry = history[index]
            item = QListWidgetItem(entry.label)
            item.setData(POSITION_ROLE, index + 1)
            item.setData(AUTHOR_ROLE, entry.author.value)
            item.setData(WHEN_ROLE, ago(entry.at, now))
            item.setData(UNDONE_ROLE, index >= position)
            item.setSizeHint(item.sizeHint().expandedTo(self._row_size()))
            self.addItem(item)
        start = QListWidgetItem("Start" if history else "No changes yet")
        start.setData(POSITION_ROLE, 0)
        start.setData(UNDONE_ROLE, position > 0 or not history)
        start.setSizeHint(self._row_size())
        self.addItem(start)

    def _row_size(self) -> QSize:
        return QSize(0, self.fontMetrics().height() + 2 * SPACE.s)

    def _go_to(self, item: QListWidgetItem) -> None:
        target = int(item.data(POSITION_ROLE))
        while self.session.history_position > target:
            before = self.session.history_position
            self.session.undo()
            if self.session.history_position == before:
                break
        while self.session.history_position < target:
            before = self.session.history_position
            self.session.redo()
            if self.session.history_position == before:
                break
