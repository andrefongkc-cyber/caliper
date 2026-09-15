"""The main window: menus, tool bar, canvas, properties dock, status bar."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence, QResizeEvent
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QWidget,
)

from caliper.app import icons
from caliper.app.properties import PropertiesPanel
from caliper.app.session import DocumentSession
from caliper.app.tools.controller import ToolController
from caliper.app.viewport.canvas import Canvas
from caliper.contracts.commands import DeleteEntities
from caliper.contracts.document import Point2
from caliper.engine.io.canonical import LoadError

FILE_FILTER = "Caliper documents (*.caliper)"
SUFFIX = ".caliper"
MESSAGE_MS = 5000
DOCK_WIDTH = 260
TOOL_ICON_SIZE = 18
COMPACT_TOOLBAR_BELOW = 980
"""Window width in logical pixels below which the tool bar drops its labels."""


class MainWindow(QMainWindow):
    def __init__(self, session: DocumentSession | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session if session is not None else DocumentSession(parent=self)
        self.controller = ToolController(self.session, self)
        self.canvas = Canvas(self.session, self.controller)
        self.properties = PropertiesPanel(self.session)
        self.tool_actions: dict[str, QAction] = {}

        self.setCentralWidget(self.canvas)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self._build_actions()
        self._build_menus()
        self._build_tool_bar()
        self._build_dock()
        self._build_status_bar()

        self.session.file_changed.connect(self._update_title)
        self.session.document_changed.connect(self._update_edit_actions)
        self.session.selection_changed.connect(self._update_edit_actions)
        self.session.message.connect(self.show_message)
        self.controller.changed.connect(self._update_tool_state)
        self.canvas.cursor_moved.connect(self._update_cursor)

        self._update_title()
        self._update_edit_actions()
        self._update_tool_state()
        self.resize(1280, 800)

    # --- Construction ---------------------------------------------------------------------

    def _action(
        self,
        text: str,
        slot: Callable[..., object],
        shortcut: QKeySequence | QKeySequence.StandardKey | str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        self.addAction(action)
        return action

    def _build_actions(self) -> None:
        std = QKeySequence.StandardKey
        self.new_action = self._action("New", self.new_document, std.New)
        self.open_action = self._action("Open…", self.open_document, std.Open)
        self.save_action = self._action("Save", self.save_document, std.Save)
        self.save_as_action = self._action("Save As…", self.save_document_as, std.SaveAs)
        self.close_action = self._action("Close Window", self.close, std.Close)
        self.undo_action = self._action("Undo", self.undo, std.Undo)
        self.redo_action = self._action("Redo", self.redo, std.Redo)
        self.delete_action = self._action("Delete", self.delete_selection)
        self.delete_action.setShortcuts(
            [QKeySequence(Qt.Key.Key_Delete), QKeySequence(Qt.Key.Key_Backspace)]
        )
        self.select_all_action = self._action("Select All", self.select_all, std.SelectAll)
        self.fit_action = self._action("Zoom to Fit", self.canvas.zoom_to_fit, "F")
        self.grid_action = self._action("Show Grid", self._toggle_grid, "G")
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(True)
        self.snap_action = self._action("Snap to Grid", self._toggle_snap)
        self.snap_action.setCheckable(True)
        self.snap_action.setChecked(True)
        for action, name in (
            (self.undo_action, "undo"),
            (self.redo_action, "redo"),
            (self.fit_action, "fit"),
            (self.grid_action, "grid"),
        ):
            action.setIcon(icons.icon(name))

        group = QActionGroup(self)
        group.setExclusive(True)
        for name, tool in self.controller.tools.items():
            action = self._action(
                name, lambda _=False, n=name: self.controller.activate(n), tool.shortcut
            )
            action.setCheckable(True)
            action.setToolTip(f"{name} ({tool.shortcut})")
            action.setIcon(icons.icon(name.lower()))
            group.addAction(action)
            self.tool_actions[name] = action

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("File")
        for action in (self.new_action, self.open_action):
            file_menu.addAction(action)
        file_menu.addSeparator()
        for action in (self.save_action, self.save_as_action):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(self.close_action)

        edit_menu = bar.addMenu("Edit")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.delete_action)
        edit_menu.addAction(self.select_all_action)

        view_menu = bar.addMenu("View")
        view_menu.addAction(self.fit_action)
        view_menu.addSeparator()
        view_menu.addAction(self.grid_action)
        view_menu.addAction(self.snap_action)

        sketch_menu = bar.addMenu("Sketch")
        for action in self.tool_actions.values():
            sketch_menu.addAction(action)

    def _build_tool_bar(self) -> None:
        bar = QToolBar("Sketch")
        bar.setObjectName("sketch-tools")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar.setIconSize(QSize(TOOL_ICON_SIZE, TOOL_ICON_SIZE))
        # Groups by category, so later categories (constrain, inspect) add a group, not a redesign.
        select, *create = self.tool_actions.values()
        bar.addAction(select)
        bar.addSeparator()
        for action in create:
            bar.addAction(action)
        bar.addSeparator()
        bar.addAction(self.fit_action)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.tool_bar = bar

    def _build_dock(self) -> None:
        dock = QDockWidget("Properties", self)
        dock.setObjectName("properties")
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        dock.setWidget(self.properties)
        dock.setMinimumWidth(DOCK_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.resizeDocks([dock], [DOCK_WIDTH], Qt.Orientation.Horizontal)
        self.properties_dock = dock

    def _build_status_bar(self) -> None:
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        self.hint_label = QLabel()
        self.cursor_label = QLabel()
        self.cursor_label.setMinimumWidth(190)
        self.cursor_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status.addWidget(self.hint_label, 1)
        status.addPermanentWidget(self.cursor_label)

    # --- File -----------------------------------------------------------------------------

    def new_document(self) -> None:
        if self.confirm_discard():
            self.controller.cancel_operation()
            self.session.new()
            self.canvas.reset_view()

    def open_document(self, path: Path | None = None) -> bool:
        if not self.confirm_discard():
            return False
        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(
                self, "Open", self._last_directory(), FILE_FILTER
            )
            if not chosen:
                return False
            path = Path(chosen)
        return self.load(path)

    def load(self, path: Path) -> bool:
        """Open `path` without asking about unsaved changes. Shows a dialog on failure."""
        try:
            self.controller.cancel_operation()
            self.session.open(path)
        except LoadError as error:
            self.show_load_error(path, error)
            return False
        except OSError as error:
            self.show_load_error(path, LoadError(error.strerror or str(error)))
            return False
        self._remember_directory(path)
        self.canvas.zoom_to_fit()
        self.show_message(f"Opened {path.name}")
        return True

    def save_document(self) -> bool:
        if self.session.path is None:
            return self.save_document_as()
        return self._save_to(self.session.path)

    def save_document_as(self) -> bool:
        start = str(self.session.path) if self.session.path else self._last_directory()
        chosen, _ = QFileDialog.getSaveFileName(self, "Save As", start, FILE_FILTER)
        if not chosen:
            return False
        path = Path(chosen)
        if path.suffix != SUFFIX:
            path = path.with_name(path.name + SUFFIX)
        return self._save_to(path)

    def _save_to(self, path: Path) -> bool:
        try:
            self.session.save(path)
        except OSError as error:
            QMessageBox.critical(self, "Couldn't save", f"{path}\n\n{error.strerror or error}")
            return False
        self._remember_directory(path)
        self.show_message(f"Saved {path.name}")
        return True

    def confirm_discard(self) -> bool:
        """True if there are no unsaved changes, or the user saved or chose to discard them."""
        if not self.session.is_dirty:
            return True
        name = self.session.path.name if self.session.path else "Untitled"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Save changes to {name}?")
        box.setInformativeText("Your changes will be lost if you don't save them.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            return self.save_document()
        return answer == QMessageBox.StandardButton.Discard

    def show_load_error(self, path: Path, error: LoadError) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setText(f"Couldn't open {path.name}")
        lines = [f"{e.field}: {e.message}" if e.field else e.message for e in error.errors]
        box.setInformativeText(str(error).split(":")[0] if lines else str(error))
        if lines:
            box.setDetailedText("\n".join(lines))
        box.exec()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        compact = event.size().width() < COMPACT_TOOLBAR_BELOW
        style = (
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if compact
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        if self.tool_bar.toolButtonStyle() != style:
            self.tool_bar.setToolButtonStyle(style)
        super().resizeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.confirm_discard():
            event.accept()
        else:
            event.ignore()

    # --- Edit -----------------------------------------------------------------------------

    def undo(self) -> None:
        self.controller.cancel_operation()
        self.session.undo()

    def redo(self) -> None:
        self.controller.cancel_operation()
        self.session.redo()

    def delete_selection(self) -> None:
        if self.session.selection:
            self.session.execute(DeleteEntities(ids=tuple(sorted(self.session.selection))))

    def select_all(self) -> None:
        self.session.set_selection(frozenset(self.session.document.entities))

    # --- State ----------------------------------------------------------------------------

    def show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, MESSAGE_MS)

    def _update_title(self) -> None:
        name = self.session.path.name if self.session.path else "Untitled"
        self.setWindowTitle(f"{name}[*] — Caliper")
        self.setWindowFilePath(str(self.session.path) if self.session.path else "")
        self.setWindowModified(self.session.is_dirty)

    def _update_edit_actions(self) -> None:
        bus = self.session.bus
        self.undo_action.setEnabled(bus.undo_label is not None)
        self.undo_action.setText(f"Undo {bus.undo_label}" if bus.undo_label else "Undo")
        self.redo_action.setEnabled(bus.redo_label is not None)
        self.redo_action.setText(f"Redo {bus.redo_label}" if bus.redo_label else "Redo")
        self.delete_action.setEnabled(bool(self.session.selection))

    def _update_tool_state(self) -> None:
        tool = self.controller.active
        self.tool_actions[tool.name].setChecked(True)
        self.hint_label.setText(tool.hint)

    def _update_cursor(self, point: Point2 | None) -> None:
        self.cursor_label.setText(
            "" if point is None else f"X {point.x:9.3f}   Y {point.y:9.3f}  mm"
        )

    def _toggle_grid(self, checked: bool) -> None:
        self.canvas.show_grid = checked
        self.canvas.update()

    def _toggle_snap(self, checked: bool) -> None:
        self.canvas.snap_to_grid = checked

    def _last_directory(self) -> str:
        return str(QSettings().value("last_directory", str(Path.home())))

    def _remember_directory(self, path: Path) -> None:
        QSettings().setValue("last_directory", str(path.parent))
