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
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from caliper.app import icons, solve_state
from caliper.app.agent.proposal import Proposal
from caliper.app.agent.ui import AgentController, PromptBar, ProposalCard
from caliper.app.palette import CommandPalette
from caliper.app.panels.browser import SketchBrowser
from caliper.app.panels.checks import ChecksPanel
from caliper.app.panels.history import HistoryList
from caliper.app.properties import PropertiesPanel
from caliper.app.session import DocumentSession
from caliper.app.shortcuts import ShortcutSheet
from caliper.app.tokens import SPACE
from caliper.app.tools.controller import ToolController
from caliper.app.viewport.canvas import Canvas
from caliper.contracts.commands import DeleteEntities
from caliper.contracts.document import Point2
from caliper.contracts.errors import Error
from caliper.engine.commands.bus import Bus
from caliper.engine.io.canonical import LoadError

FILE_FILTER = "Caliper documents (*.caliper)"
SUFFIX = ".caliper"
MESSAGE_MS = 5000
DOCK_WIDTH = 260
BROWSER_WIDTH = 250
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
        self.palette = CommandPalette(self.session, self)
        self.palette.return_focus = self.canvas
        self.command_panel = CommandPalette(self.session, docked=True)
        self.command_panel.return_focus = self.canvas

        self.prompt_bar = PromptBar()
        self.proposal_card = ProposalCard(self.canvas)
        self.agent = AgentController(self.session, self.prompt_bar, self.proposal_card, self)
        self.canvas.proposal = lambda: self.agent.proposal
        self.canvas.reject_proposal = self.agent.reject
        self.agent.proposal_changed.connect(self.canvas.update)
        self.agent.proposal_shown.connect(self._frame_proposal)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.canvas, 1)
        central_layout.addWidget(self.prompt_bar)
        self.setCentralWidget(central)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self._build_actions()
        self._build_menus()
        self._build_tool_bar()
        self._build_dock()
        self._build_status_bar()

        self.session.file_changed.connect(self._update_title)
        self.session.document_changed.connect(self._update_edit_actions)
        self.session.document_changed.connect(self._update_solve_status)
        self.session.selection_changed.connect(self._update_edit_actions)
        # A committed transaction sends no Change, so refresh labels when history moves too.
        self.session.history_changed.connect(self._update_edit_actions)
        self.session.message.connect(self.show_message)
        self.controller.changed.connect(self._update_tool_state)
        self.canvas.cursor_moved.connect(self._update_cursor)

        palette_actions = [
            *self.tool_actions.values(),
            self.undo_action,
            self.redo_action,
            self.delete_action,
            self.select_all_action,
            self.fit_action,
            self.grid_action,
            self.constraints_action,
            self.snap_action,
            self.new_action,
            self.open_action,
            self.save_action,
            self.save_as_action,
        ]
        self.palette.set_actions(palette_actions)
        self.command_panel.set_actions(palette_actions)
        self._update_title()
        self._update_edit_actions()
        self._update_solve_status()
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
        self.history_action = self._action("Include History in Saved Files", self._toggle_history)
        self.history_action.setCheckable(True)
        self.history_action.setToolTip(
            "Save the list of commands that built this drawing. Off by default: project files "
            "are sent to suppliers, and a part's history isn't theirs to read."
        )
        self.fit_action = self._action("Zoom to Fit", self.canvas.zoom_to_fit, "F")
        self.grid_action = self._action("Show Grid", self._toggle_grid, "G")
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(True)
        self.constraints_action = self._action("Show Constraints", self._toggle_constraints)
        self.constraints_action.setCheckable(True)
        self.constraints_action.setChecked(True)
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

        self.palette_action = self._action("Command Palette…", self.palette.open, "Ctrl+K")
        self.ask_action = self._action("Ask the Agent…", self._focus_prompt, "Ctrl+L")
        self.accept_action = self._action("Accept Proposal", self.agent.accept, "Ctrl+Return")
        self.reject_action = self._action("Reject Proposal", self.agent.reject)
        self.shortcuts_action = self._action("Keyboard Shortcuts", self.show_shortcuts, "Ctrl+/")
        for action, tip in (
            (self.fit_action, "Zoom to Fit"),
            (self.grid_action, "Show Grid"),
            (self.undo_action, "Undo"),
            (self.redo_action, "Redo"),
        ):
            action.setToolTip(
                f"{tip} ({action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)})"
            )

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
        file_menu.addAction(self.history_action)
        file_menu.addSeparator()
        file_menu.addAction(self.close_action)

        edit_menu = bar.addMenu("Edit")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.delete_action)
        edit_menu.addAction(self.select_all_action)

        view_menu = bar.addMenu("View")
        view_menu.addAction(self.palette_action)
        view_menu.addSeparator()
        view_menu.addAction(self.fit_action)
        view_menu.addSeparator()
        view_menu.addAction(self.grid_action)
        view_menu.addAction(self.constraints_action)
        view_menu.addAction(self.snap_action)

        sketch_menu = bar.addMenu("Sketch")
        for action in self.tool_actions.values():
            sketch_menu.addAction(action)

        agent_menu = bar.addMenu("Agent")
        for action in (self.ask_action, self.accept_action, self.reject_action):
            agent_menu.addAction(action)

        help_menu = bar.addMenu("Help")
        help_menu.addAction(self.shortcuts_action)
        self.menus = [file_menu, edit_menu, view_menu, sketch_menu, agent_menu, help_menu]

    def _build_tool_bar(self) -> None:
        bar = QToolBar("Sketch")
        bar.setObjectName("sketch-tools")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar.setIconSize(QSize(TOOL_ICON_SIZE, TOOL_ICON_SIZE))
        # Groups by category, so later categories (constrain, inspect) add a group, not a redesign.
        for category in ("select", "create", "inspect"):
            for name, tool in self.controller.tools.items():
                if tool.category == category:
                    bar.addAction(self.tool_actions[name])
            bar.addSeparator()
        bar.addAction(self.fit_action)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.tool_bar = bar

    def _build_dock(self) -> None:
        features = QDockWidget.DockWidgetFeature.DockWidgetMovable

        self.browser = SketchBrowser(self.session)
        self.browser.frame_requested.connect(lambda id: self.canvas.frame(frozenset({id})))
        self.history = HistoryList(self.session)
        tabs = QTabWidget()
        tabs.setObjectName("browser-tabs")
        tabs.setDocumentMode(True)
        tabs.addTab(self.browser, "Sketch")
        tabs.addTab(self.history, "History")
        self.browser_tabs = tabs
        browser = QDockWidget("Browser", self)
        browser.setObjectName("browser")
        browser.setFeatures(features)
        browser.setTitleBarWidget(QWidget())  # the tabs are the title
        browser.setWidget(tabs)
        browser.setMinimumWidth(BROWSER_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, browser)
        self.browser_dock = browser

        dock = QDockWidget("Properties", self)
        dock.setObjectName("properties")
        dock.setFeatures(features)
        dock.setWidget(self.properties)
        dock.setMinimumWidth(DOCK_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.properties_dock = dock

        self.checks = ChecksPanel(self.session)
        checks = QDockWidget("Checks", self)
        checks.setObjectName("checks-dock")
        checks.setFeatures(features)
        checks.setWidget(self.checks)
        checks.setMinimumWidth(DOCK_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, checks)
        self.checks_dock = checks

        commands = QDockWidget("Commands", self)
        commands.setObjectName("commands-dock")
        commands.setFeatures(features)
        commands.setWidget(self.command_panel)
        commands.setMinimumWidth(DOCK_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, commands)
        self.commands_dock = commands

        self.splitDockWidget(dock, commands, Qt.Orientation.Vertical)
        self.splitDockWidget(commands, checks, Qt.Orientation.Vertical)

        self.resizeDocks([browser, dock], [BROWSER_WIDTH, DOCK_WIDTH], Qt.Orientation.Horizontal)
        self.resizeDocks([dock, commands, checks], [260, 260, 280], Qt.Orientation.Vertical)

    def _build_status_bar(self) -> None:
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        self.hint_label = QLabel()
        self.solve_label = QLabel()
        self.solve_label.setObjectName("solve-status")
        self.solve_label.setToolTip(
            "How much of the sketch can still move. Fully constrained geometry is drawn in "
            "its own colour."
        )
        self.cursor_label = QLabel()
        self.cursor_label.setMinimumWidth(190)
        self.cursor_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status.addWidget(self.hint_label, 1)
        status.addPermanentWidget(self.solve_label)
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
        steps = self.session.opened_steps
        recorded = f" · {steps} recorded step{'s' if steps != 1 else ''}" if steps else ""
        self.show_message(f"Opened {path.name}{recorded}")
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

    def _frame_proposal(self, proposal: Proposal) -> None:
        """Keep both the change and the card in view: frame it in the space left of the card."""
        box = Bus(proposal.result).queries.bounding_box()
        if not isinstance(box, Error):
            inset = self.proposal_card.width() + 2 * SPACE.l
            self.canvas.frame_box(box, right_inset=inset)

    def _focus_prompt(self) -> None:
        self.prompt_bar.input.setFocus()
        self.prompt_bar.input.selectAll()

    def show_shortcuts(self) -> None:
        ShortcutSheet(self.menus, self).exec()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        compact = event.size().width() < COMPACT_TOOLBAR_BELOW
        style = (
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if compact
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        if self.tool_bar.toolButtonStyle() != style:
            self.tool_bar.setToolButtonStyle(style)
        if self.proposal_card.isVisible():
            self.proposal_card.reposition()
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

    def _update_solve_status(self) -> None:
        document = self.session.document
        if not solve_state.is_constrained(document):
            self.solve_label.setText("")
            self.solve_label.hide()
            return
        self.solve_label.setText(solve_state.describe(self.session.queries.solve_status()))
        self.solve_label.show()

    def _update_tool_state(self) -> None:
        tool = self.controller.active
        self.tool_actions[tool.name].setChecked(True)
        self.hint_label.setText(tool.hint)

    def _update_cursor(self, point: Point2 | None) -> None:
        self.cursor_label.setText(
            "" if point is None else f"X {point.x:9.3f}   Y {point.y:9.3f}  mm"
        )

    def _toggle_history(self, checked: bool) -> None:
        self.session.save_history = checked
        if checked and self.session.path is not None:
            self.show_message("Saved files will include how the drawing was built")

    def _toggle_grid(self, checked: bool) -> None:
        self.canvas.show_grid = checked
        self.canvas.update()

    def _toggle_constraints(self, checked: bool) -> None:
        self.canvas.show_constraints = checked
        self.canvas.update()

    def _toggle_snap(self, checked: bool) -> None:
        self.canvas.snap_to_grid = checked

    def _last_directory(self) -> str:
        return str(QSettings().value("last_directory", str(Path.home())))

    def _remember_directory(self, path: Path) -> None:
        QSettings().setValue("last_directory", str(path.parent))
