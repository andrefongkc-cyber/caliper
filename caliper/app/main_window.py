"""The main window: menus, tool bar, canvas, properties dock, status bar.

Two tabs, each its own document (ADR 0015): 3D, the part, where the app starts, sketched on
its planes and extruded; and 2D, a sketch to test on. New, Open, and Save act on the tab
shown. A sketch is edited in 3D by the same canvas as in 2D, facing the sketch's plane, over
the part (`viewport/backdrop.py`), and closed with Finish or Cancel."""

from collections import Counter
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence, QResizeEvent
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from caliper.ai.agent import from_environment
from caliper.app import icons, solve_state
from caliper.app.agent.mcp_host import McpHost
from caliper.app.agent.proposal import Proposal
from caliper.app.agent.timing import TIMING_FILE, markdown, timing_file
from caliper.app.agent.ui import AgentController, PromptBar, ProposalCard
from caliper.app.extrude import ExtrudeForm
from caliper.app.opener import OpenServer
from caliper.app.palette import CommandPalette
from caliper.app.panels.assistant import AssistantLog
from caliper.app.panels.browser import SketchBrowser
from caliper.app.panels.checks import ChecksPanel
from caliper.app.panels.features import FeatureTree, place_name, titles, volume_text
from caliper.app.panels.history import HistoryList
from caliper.app.panels.timing import TimingPanel
from caliper.app.properties import PropertiesPanel
from caliper.app.session import DocumentSession, Space
from caliper.app.shortcuts import ShortcutSheet
from caliper.app.tokens import SPACE
from caliper.app.tools.constrain import constraint_options
from caliper.app.tools.controller import ToolController
from caliper.app.viewport.backdrop import Backdrop, look
from caliper.app.viewport.canvas import Canvas
from caliper.app.viewport.scene3d import PLANE_NAMES
from caliper.app.viewport.view3d import View3D
from caliper.contracts.commands import (
    Applied,
    CreateConstraint,
    CreateSketch,
    DeleteEntities,
    ModifyEntity,
)
from caliper.contracts.document import (
    ConstraintType,
    Document,
    EntityId,
    Expectation,
    FaceRef,
    Geometry,
    Plane,
    Point2,
    Sketch,
)
from caliper.contracts.errors import Error
from caliper.engine.commands.bus import Bus
from caliper.engine.io.canonical import LoadError

FILE_FILTER = "Caliper documents (*.caliper)"
SUFFIX = ".caliper"
RECENT_FILES = 10
"""How many files File → Open Recent lists, newest first."""
MESSAGE_MS = 5000
DOCK_WIDTH = 260
BROWSER_WIDTH = 250
TOOL_ICON_SIZE = 18
COMPACT_TOOLBAR_BELOW = 980
"""Window width in logical pixels below which the tool bar drops its labels."""
CONSTRAINT_KEYS: dict[ConstraintType, str] = {
    ConstraintType.HORIZONTAL: "H",
    ConstraintType.VERTICAL: "V",
    ConstraintType.COINCIDENT: "I",
    ConstraintType.EQUAL: "E",
    ConstraintType.TANGENT: "T",
}
"""Onshape's keys, where it has one. The rest are in Sketch > Constrain and the palette."""
NOTHING_TO_CONSTRAIN = "Select lines, circles, arcs, or points, or pick them with Constrain (K)"


class MainWindow(QMainWindow):
    def __init__(
        self,
        session: DocumentSession | None = None,
        parent: QWidget | None = None,
        *,
        mode: str = "3d",
    ):
        """`mode` is the tab to start in: the part ("3d"), as the app does, or the 2D sketch."""
        super().__init__(parent)
        self.session = session if session is not None else DocumentSession(parent=self)
        self.controller = ToolController(self.session, self)
        self.canvas = Canvas(self.session, self.controller)
        self.view3d = View3D(self.session)
        """The part from its planes up (V2, ADR 0015), in the 3D tab."""
        self.view3d.open_requested.connect(self._open_picked)
        self.sketch_open: EntityId | None = None
        """The sketch being edited in 3D, until Finish or Cancel: UI state."""
        self._sketch_entry = 0
        """Where the history was when it opened: Cancel undoes back to there."""
        self._backdrop: Backdrop | None = None
        """The part behind the canvas while a sketch is edited in 3D, or a proposal shown."""
        self._previewing: EntityId | None = None
        """A sketch a proposal shown in 3D is making, faced before it exists."""
        self._canvas_views: dict[str, tuple[float, float, float]] = {}
        """The canvas's view in each tab, while the other shows: the canvas edits both."""
        self.views = QStackedWidget()
        self.views.addWidget(self.canvas)
        self.views.addWidget(self.view3d)
        self.sketch_label = QLabel(self.views)
        """Which sketch the canvas edits (V2's sketch mode), over its top left corner. Over
        the views, not in the canvas, so the canvas's own drawing is as it was."""
        self.sketch_label.setObjectName("sketch-label")
        self.sketch_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.sketch_label.move(SPACE.m, SPACE.m)
        self.mode = self.session.space.value
        """"3d", the part, or "2d", the test sketch: the tab shown, and so the document."""
        self.properties = PropertiesPanel(self.session)
        self.tool_actions: dict[str, QAction] = {}
        self.palette = CommandPalette(self.session, self)
        self.palette.return_focus = self.canvas
        self.command_panel = CommandPalette(self.session, docked=True)
        self.command_panel.return_focus = self.canvas

        self.prompt_bar = PromptBar()
        self.proposal_card = ProposalCard(self.canvas)
        """In whichever view shows (`_seat_card`): inside it, not over the stack, so a repaint
        of the view doesn't make Qt composite the card again on every call."""
        self.agent = AgentController(
            self.session, self.prompt_bar, self.proposal_card, self, assistant=from_environment()
        )
        self.canvas.proposal = lambda: self.agent.proposal
        self.canvas.reject_proposal = self.agent.reject
        self.agent.proposal_changed.connect(self.canvas.show_proposal)
        self.agent.proposal_changed.connect(self._proposal_settled)
        self.agent.proposal_shown.connect(self._frame_proposal)
        self.canvas.orbited.connect(self._update_sketch_tools)
        self.mcp: McpHost | None = None
        """Claude Desktop's way in, once `serve_mcp` is called."""
        self._framed_base: Document | None = None
        """The document the proposal last framed was prepared against."""
        self.opener: OpenServer | None = None
        """Files other Caliper processes hand this window, once `serve_opens` is called."""
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.views, 1)
        central_layout.addWidget(self.prompt_bar)
        self.setCentralWidget(central)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self._build_actions()
        self._build_menus()
        self._build_tool_bar()
        self._build_dock()
        self._build_status_bar()
        self._build_sketch_bar()

        self.session.file_changed.connect(self._update_title)
        self.session.document_changed.connect(self._update_edit_actions)
        self.session.document_changed.connect(self._update_solve_status)
        for signal in (
            self.session.document_changed,
            self.session.selection_changed,
            self.session.references_changed,
        ):
            signal.connect(self._update_constraint_actions)
        self.session.selection_changed.connect(self._update_edit_actions)
        # A committed transaction sends no Change, so refresh labels when history moves too.
        self.session.history_changed.connect(self._update_edit_actions)
        self.session.message.connect(self.show_message)
        for signal in (self.session.active_sketch_changed, self.session.document_changed):
            signal.connect(self._update_part_labels)
        self.session.active_sketch_changed.connect(self._sketch_gone)
        self.session.document_changed.connect(self._follow_face)
        self.controller.changed.connect(self._update_tool_state)
        self.canvas.cursor_moved.connect(self._update_cursor)

        palette_actions = [
            self.mode_2d_action,
            self.mode_3d_action,
            self.sketch_action,
            *self.new_sketch_actions.values(),
            self.finish_sketch_action,
            self.cancel_sketch_action,
            self.face_action,
            self.extrude_action,
            *self.tool_actions.values(),
            *self.constraint_actions.values(),
            self.construction_action,
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
        self._update_constraint_actions()
        self._update_tool_state()
        self._update_part_labels()
        self.resize(1280, 800)
        self._mode_set = False
        self.set_mode(mode)

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
        # Not `self.open_document` itself: `triggered` would pass its `checked` flag as the path.
        self.open_action = self._action("Open…", lambda: self.open_document(), std.Open)
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
        self.fit_action = self._action("Zoom to Fit", self._fit, "F")
        self.grid_action = self._action("Show Grid", self._toggle_grid, "G")
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(True)
        self.constraints_action = self._action(
            "Show Constraints", self._toggle_constraints, "Shift+C"
        )
        self.constraints_action.setCheckable(True)
        self.constraints_action.setChecked(True)
        self.constraints_action.setIconText("Constraints")
        self.snap_action = self._action("Snap to Grid", self._toggle_snap)
        self.snap_action.setCheckable(True)
        self.snap_action.setChecked(True)
        for action, name in (
            (self.undo_action, "undo"),
            (self.redo_action, "redo"),
            (self.fit_action, "fit"),
            (self.grid_action, "grid"),
            (self.constraints_action, "badges"),
        ):
            action.setIcon(icons.icon(name))

        # The core mode switch (ADR 0015): the part in 3D, a sketch to test on in 2D. Each tab
        # is its own document.
        self.mode_2d_action = self._action("2D Sketch", lambda: self.set_mode("2d"), "Ctrl+1")
        self.mode_3d_action = self._action("3D Part", lambda: self.set_mode("3d"), "Ctrl+2")
        self.mode_group = QActionGroup(self)
        for action, text, tip in (
            (self.mode_2d_action, "2D", "A sketch to test on, in its own file (⌘1)"),
            (self.mode_3d_action, "3D", "The part: sketch on its planes, extrude, orbit (⌘2)"),
        ):
            action.setCheckable(True)
            action.setIconText(text)
            action.setToolTip(tip)
            self.mode_group.addAction(action)
        self.mode_2d_action.setChecked(True)

        # The part (V2): sketches on its planes, and extrudes of them.
        self.extrude_action = self._action("Extrude…", self.start_extrude, "Shift+E")
        self.extrude_action.setIconText("Extrude")
        self.extrude_action.setToolTip(
            "Sweep a sketch (the one picked, or its selected geometry) into a solid (Shift+E)"
        )
        self.sketch_action = self._action("Sketch", self.sketch, "Shift+S")
        self.sketch_action.setToolTip(
            "Sketch on the picked plane, or edit the picked sketch (Shift+S)"
        )
        # `triggered` passes `checked` first, so the plane has to come after it.
        self.new_sketch_actions = {
            plane: self._action(
                f"Sketch on {PLANE_NAMES[plane]}",
                lambda _=False, p=plane: self.new_sketch(p),
            )
            for plane in (Plane.XY, Plane.XZ, Plane.YZ)
        }
        self.finish_sketch_action = self._action("Finish Sketch", self.finish_sketch)
        self.cancel_sketch_action = self._action("Cancel Sketch", self.cancel_sketch)
        self.face_action = self._action("Face the Sketch", self._face, "N")
        self.face_action.setToolTip("Look straight at the sketch again, to keep drawing (N)")

        self.palette_action = self._action("Command Palette…", self.palette.open, "Ctrl+K")
        self.ask_action = self._action("Ask the Agent…", self._focus_prompt, "Ctrl+L")
        self.accept_action = self._action("Accept Proposal", self.agent.accept, "Ctrl+Return")
        self.reject_action = self._action("Reject Proposal", self.agent.reject)
        self.start_run_action = self._action("Start Timing Run", self._start_run, "Ctrl+Shift+R")
        self.start_run_action.setEnabled(False)  # until Claude Desktop can connect
        self.shortcuts_action = self._action("Keyboard Shortcuts", self.show_shortcuts, "Ctrl+/")
        for action, tip in (
            (self.fit_action, "Zoom to Fit"),
            (self.grid_action, "Show Grid"),
            (self.constraints_action, "Show constraint badges (T, =, H…); dimensions stay"),
            (self.undo_action, "Undo"),
            (self.redo_action, "Redo"),
        ):
            action.setToolTip(
                f"{tip} ({action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)})"
            )

        self.constraint_actions: dict[ConstraintType, QAction] = {}
        for type in ConstraintType:
            action = self._action(
                type.value.capitalize(),
                lambda _=False, t=type: self.add_constraint(t),
                CONSTRAINT_KEYS.get(type),
            )
            action.setObjectName(f"constraint-{type.value}")
            self.constraint_actions[type] = action
        self.construction_action = self._action(
            "Toggle Construction", self.toggle_construction, "Q"
        )
        self.construction_action.setToolTip(
            "Construction geometry is constrained like the rest but never forms a profile (Q)"
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
        self.recent_menu = file_menu.addMenu("Open Recent")
        self.recent_menu.setToolTipsVisible(True)
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self._fill_recent()
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
        view_menu.addAction(self.mode_2d_action)
        view_menu.addAction(self.mode_3d_action)
        view_menu.addSeparator()
        view_menu.addAction(self.palette_action)
        view_menu.addSeparator()
        view_menu.addAction(self.fit_action)
        view_menu.addSeparator()
        view_menu.addAction(self.grid_action)
        view_menu.addAction(self.constraints_action)
        view_menu.addAction(self.snap_action)

        part_menu = bar.addMenu("Part")
        part_menu.addAction(self.sketch_action)
        for action in self.new_sketch_actions.values():
            part_menu.addAction(action)
        part_menu.addSeparator()
        for action in (self.finish_sketch_action, self.cancel_sketch_action, self.face_action):
            part_menu.addAction(action)
        part_menu.addSeparator()
        part_menu.addAction(self.extrude_action)

        sketch_menu = bar.addMenu("Sketch")
        for action in self.tool_actions.values():
            sketch_menu.addAction(action)
        sketch_menu.addSeparator()
        sketch_menu.addAction(self.construction_action)
        constrain_menu = sketch_menu.addMenu("Constrain")
        for action in self.constraint_actions.values():
            constrain_menu.addAction(action)

        agent_menu = bar.addMenu("Agent")
        for action in (self.ask_action, self.accept_action, self.reject_action):
            agent_menu.addAction(action)
        agent_menu.addSeparator()
        agent_menu.addAction(self.start_run_action)

        help_menu = bar.addMenu("Help")
        help_menu.addAction(self.shortcuts_action)
        self.menus = [
            file_menu,
            edit_menu,
            view_menu,
            part_menu,
            sketch_menu,
            constrain_menu,
            agent_menu,
            help_menu,
        ]

    def _build_tool_bar(self) -> None:
        bar = QToolBar("Sketch")
        bar.setObjectName("sketch-tools")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar.setIconSize(QSize(TOOL_ICON_SIZE, TOOL_ICON_SIZE))
        # The mode switch: two halves of one control, then what's being edited.
        switch = QWidget()
        switch.setObjectName("mode-switch")
        halves = QHBoxLayout(switch)
        halves.setContentsMargins(0, 0, 0, 0)
        halves.setSpacing(0)
        for action in (self.mode_2d_action, self.mode_3d_action):
            button = QToolButton()
            button.setDefaultAction(action)
            button.setObjectName(f"mode-{action.iconText().lower()}")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            halves.addWidget(button)
        bar.addWidget(switch)
        bar.addSeparator()
        sketch = QToolButton()
        sketch.setObjectName("sketch")
        sketch.setDefaultAction(self.sketch_action)
        sketch.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        planes = QMenu(sketch)
        for action in self.new_sketch_actions.values():
            planes.addAction(action)
        sketch.setMenu(planes)
        self.sketch_button = sketch
        bar.addWidget(sketch)
        bar.addAction(self.extrude_action)
        bar.widgetForAction(self.extrude_action).setObjectName("extrude")
        bar.addSeparator()
        # Groups by category, so a later category adds a group, not a redesign.
        for category in ("select", "create", "constrain", "inspect"):
            for name, tool in self.controller.tools.items():
                if tool.category == category:
                    bar.addAction(self.tool_actions[name])
            bar.addSeparator()
        for action in (self.fit_action, self.constraints_action):
            bar.addAction(action)  # icons only: they read at a glance, and the bar stays one row
            bar.widgetForAction(action).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
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
        self.assistant_log = AssistantLog(self.agent)
        tabs.addTab(self.assistant_log, "Assistant")
        self.agent.turn_started.connect(lambda _: tabs.setCurrentWidget(self.assistant_log))
        self.browser_tabs = tabs
        # The part above, the sketch being edited below (V2): the feature tree, then its contents.
        self.features = FeatureTree(self.session)
        self.features.editing = lambda: self.sketch_open if self.mode == "3d" else None
        self.features.edit_requested.connect(self.edit_sketch)
        self.features.sketch_requested.connect(self.new_sketch)
        split = QSplitter(Qt.Orientation.Vertical)
        split.setObjectName("browser-split")
        split.setChildrenCollapsible(False)
        split.addWidget(self.features)
        split.addWidget(tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([200, 450])  # the planes, then the features
        browser = QDockWidget("Browser", self)
        browser.setObjectName("browser")
        browser.setFeatures(features)
        browser.setTitleBarWidget(QWidget())  # the tabs are the title
        browser.setWidget(split)
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

        self.timing = TimingPanel()
        self.timing.start_requested.connect(self._start_run)
        copied = f"Copied the timing for {TIMING_FILE}"
        self.timing.copied.connect(lambda: self.show_message(copied))
        timing = QDockWidget("Timing", self)
        timing.setObjectName("timing-dock")
        timing.setFeatures(features)
        timing.setWidget(self.timing)
        timing.setMinimumWidth(DOCK_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, timing)
        self.splitDockWidget(timing, dock, Qt.Orientation.Vertical)  # above Properties
        timing.hide()  # until Claude Desktop can connect
        self.timing_dock = timing

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
        self.solid_label = QLabel()
        self.solid_label.setObjectName("solid-status")
        self.solid_label.setToolTip("The volume of the part's solid, after its last feature")
        status.addWidget(self.hint_label, 1)
        status.addPermanentWidget(self.solid_label)
        status.addPermanentWidget(self.solve_label)
        status.addPermanentWidget(self.cursor_label)

    # --- File -----------------------------------------------------------------------------

    def new_document(self) -> None:
        """A new document in the tab shown: in 3D a part with only its planes, in 2D a sketch."""
        if self.confirm_discard():
            self.controller.cancel_operation()
            self._close_sketch()
            self.session.new()
            if self.mode == "2d":
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
            self._close_sketch()
            self.session.open(path)
        except LoadError as error:
            self.show_load_error(path, error)
            return False
        except OSError as error:
            self.show_load_error(path, LoadError(error.strerror or str(error)))
            return False
        self._remember_directory(path)
        self._remember_file(path)
        if self.mode == "2d":
            self.canvas.zoom_to_fit()
        else:
            self.view3d.fit()
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
        self._remember_file(path)
        self.show_message(f"Saved {path.name}{self._write_test_timing(path)}")
        return True

    def _write_test_timing(self, drawing: Path) -> str:
        """Saved into a test folder: write its 003-timing.md from the latest run, unless it
        has one. Returns what to add to the status message."""
        target = timing_file(drawing)
        timing = None if self.mcp is None else self.mcp.timer.timing
        if target is None or timing is None or not timing.calls or target.exists():
            return ""
        try:
            target.write_text(markdown(timing))
        except OSError as error:
            return f" · couldn't write {target.name}: {error.strerror or error}"
        return f" · wrote {target.name}"

    def recent_files(self) -> list[Path]:
        """File → Open Recent, newest first."""
        value = QSettings().value("recent_files", [])
        if isinstance(value, str):  # one entry comes back as a string
            value = [value]
        return [Path(p) for p in value or []]

    def open_recent(self, path: Path) -> bool:
        if not path.is_file():
            self._forget_file(path)
            self.show_message(f"{path.name} isn't in {path.parent} any more")
            return False
        return self.open_document(path)

    def _remember_file(self, path: Path) -> None:
        path = path.absolute()
        paths = [path, *(p for p in self.recent_files() if p != path)][:RECENT_FILES]
        QSettings().setValue("recent_files", [str(p) for p in paths])

    def _forget_file(self, path: Path) -> None:
        paths = [p for p in self.recent_files() if p != path]
        QSettings().setValue("recent_files", [str(p) for p in paths])

    def _clear_recent(self) -> None:
        QSettings().remove("recent_files")

    def _fill_recent(self) -> None:
        """Rebuilt each time the menu opens, so it lists what another window saved too."""
        menu = self.recent_menu
        menu.clear()
        paths = self.recent_files()
        names = Counter(p.name for p in paths)
        for path in paths:
            label = path.name if names[path.name] == 1 else f"{path.name} — {path.parent.name}"
            action = menu.addAction(label)
            action.setToolTip(str(path))
            action.triggered.connect(lambda _=False, p=path: self.open_recent(p))
        menu.addSeparator()
        clear = menu.addAction("Clear Menu")
        clear.setEnabled(bool(paths))
        clear.triggered.connect(self._clear_recent)

    def confirm_discard(self) -> bool:
        """True if there are no unsaved changes, or the user saved or chose to discard them."""
        if not self.session.is_dirty:
            return True
        name = self.session.path.name if self.session.path else "Untitled"
        tab = "the 3D part" if self.mode == "3d" else "the 2D sketch"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Save changes to {name} ({tab})?")
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
        """Keep both the change and the card in view: frame it in the space left of the card.

        In 3D, the canvas faces the sketch the proposal draws in, over the part, first. A
        proposal an agent keeps adding to is framed again only when it outgrows the view:
        moving the view redraws the whole sketch, which each addition shouldn't cost."""
        result = Bus(proposal.result).queries
        if self.mode == "3d":
            drawn = self._show_proposal_in_3d(proposal)
            if not drawn:
                return  # nothing drawn to frame: an extrude, say, listed on the card
            box = result.bounding_box(drawn)
        else:
            box = result.bounding_box()
        if isinstance(box, Error):
            return
        inset = self.proposal_card.width() + 2 * SPACE.l
        if proposal.base is self._framed_base and self.canvas.shows(box, right_inset=inset):
            return
        self._framed_base = proposal.base
        self.canvas.frame_box(box, right_inset=inset)

    def _show_proposal_in_3d(self, proposal: Proposal) -> list[EntityId]:
        """Face the sketch a proposal draws in, so its dashed preview shows over the part: the
        sketch open already, another of the part's, or one the proposal makes. Returns the
        geometry it adds or changes there, to frame; none for a change to features only."""
        base, after = proposal.base, proposal.result
        changed = [
            (id, e)
            for id, e in after.entities.items()
            if isinstance(e, Geometry) and base.entities.get(id) != e
        ]
        if not changed:
            return []
        sketches = {f.id: f.plane for f in after.features if isinstance(f, Sketch)}
        open_one = [id for id, e in changed if e.sketch == self.sketch_open]
        if self.sketch_open is not None and open_one:
            return open_one
        sketch = changed[0][1].sketch
        if sketch not in sketches:
            return []
        if any(f.id == sketch for f in base.features):
            self._open_sketch(sketch, self.session.history_position)
        else:
            self._face_plane(sketches[sketch], None, after)  # a sketch the proposal makes
            self._previewing = sketch
        return [id for id, e in changed if e.sketch == sketch]

    def _proposal_settled(self) -> None:
        """A proposal shown over a sketch it was making is gone: accepted, edit that sketch
        (Accept keeps the proposal's ids); rejected, back to the part."""
        if self.agent.proposal is not None or self._previewing is None:
            return
        made, self._previewing = self._previewing, None
        if self._plane_of(made) is not None:
            self._open_sketch(made, self.session.history_position)
        else:
            self._close_sketch()

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

    def serve_mcp(self, path: Path) -> bool:
        """Let `caliper-mcp` (Claude Desktop) reach this window at `path`. Its changes come
        in as proposals, like the assistant's. False, with a status message, if it can't."""
        self.mcp = McpHost(self.session, self.agent, path, self)
        self.mcp.stepped.connect(self.assistant_log.remote_step)
        self.mcp.timed.connect(self.timing.show_timing)
        self.timing.source = lambda: None if self.mcp is None else self.mcp.timer.timing
        problem = self.mcp.start()
        if problem is not None:
            self.session.message.emit(problem)
        self.timing_dock.setVisible(problem is None)
        self.start_run_action.setEnabled(problem is None)
        return problem is None

    def serve_opens(self, path: Path) -> bool:
        """Open the files other Caliper processes hand over at `path` (Finder, or
        `python -m caliper.app file.caliper`), in this window. False if another window
        already takes them, or this platform can't."""
        self.opener = OpenServer(path, self)
        self.opener.requested.connect(self._open_handed)
        return self.opener.start()

    def _open_handed(self, path: Path) -> None:
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.open_document(path)

    def _start_run(self) -> None:
        if self.mcp is not None and self.mcp.listening:
            self.mcp.start_run()
            self.show_message("Timing a new run: send the task in Claude Desktop")

    def confirm_close(self) -> bool:
        """Ask about each tab's unsaved changes in turn, showing it: True if none is left."""
        for space, _ in self.session.unsaved():
            self.set_mode(space.value)
            if not self.confirm_discard():
                return False
        return True

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.confirm_close():
            if self.mcp is not None:
                self.mcp.close()
            if self.opener is not None:
                self.opener.close()
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

    def add_constraint(self, type: ConstraintType) -> None:
        """Constrain the picked references, or else the selected curves and points."""
        option = constraint_options(self.session).get(type.value)
        if option is None or option.error is not None:
            reason = option.error.message if option and option.error else NOTHING_TO_CONSTRAIN
            self.show_message(f"Can't add {type.value}: {reason}")
            return
        result = self.session.execute(CreateConstraint(type=type, refs=option.refs))
        if isinstance(result, Applied):
            self.session.set_references(())
            self.controller.changed.emit()  # the Constrain tool's hint counts the picks

    def toggle_construction(self) -> None:
        """Make the selected geometry construction, or real again if all of it already is."""
        entities = self.session.document.entities
        ids = sorted(i for i in self.session.selection if hasattr(entities.get(i), "construction"))
        if not ids:
            return
        make = not all(entities[i].construction for i in ids)
        with self.session.transaction("Toggle Construction"):
            for id in ids:
                if entities[id].construction != make:
                    self.session.execute(ModifyEntity(id=id, changes={"construction": make}))

    def select_all(self) -> None:
        """Everything in the sketch being edited; not its checks, which aren't drawn (the Checks
        panel), nor what's in another sketch."""
        entities = self.session.sketch_view.entities
        self.session.set_selection(
            frozenset(id for id, e in entities.items() if not isinstance(e, Expectation))
        )

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

    # --- 2D and 3D --------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Show the 3D tab, the part ("3d"), or the 2D tab, the test sketch ("2d"). Each is its
        own document, with its own file and undo history; switching shows the other, as
        opening a file does, and leaves both as they are."""
        if mode not in ("2d", "3d"):
            raise ValueError(f"no mode {mode!r}")
        if mode != self.mode or not self._mode_set:
            self.controller.cancel_operation()
            view = self.canvas.view
            if self._mode_set:  # the canvas's view in the tab left, to come back to
                self._canvas_views[self.mode] = (view.scale, view.origin_x, view.origin_y)
            self.session.use(Space(mode))
            first, self.mode, self._mode_set = not self._mode_set, mode, True
            kept = self._canvas_views.get(mode)
            if kept is not None:
                view.scale, view.origin_x, view.origin_y = kept
            elif mode == "2d" and not first:
                self.canvas.reset_view()
        (self.mode_2d_action if mode == "2d" else self.mode_3d_action).setChecked(True)
        self._show_views()
        if mode == "3d" and self.sketch_open is None:
            self.show_message("3D: drag to orbit, right-drag to pan, scroll to zoom, F to fit")

    def _show_views(self) -> None:
        """The view for the tab and the state: in 2D the canvas; in 3D the part, or the canvas
        over it while a sketch is edited."""
        in_3d = self.mode == "3d"
        on_canvas = not in_3d or self._backdrop is not None
        self.canvas.backdrop = self._backdrop if in_3d else None
        self.canvas.update()
        page = self.canvas if on_canvas else self.view3d
        self.views.setCurrentWidget(page)
        self._seat_card(page)
        self.sketch_label.setVisible(not in_3d)
        self.sketch_label.raise_()
        self.sketch_bar.setVisible(in_3d and self._backdrop is not None)
        self.sketch_bar.raise_()
        self.features.setVisible(in_3d)
        self.features.rebuild()
        for action in (self.sketch_action, *self.new_sketch_actions.values()):
            action.setEnabled(in_3d)
        self.extrude_action.setEnabled(in_3d)
        self.sketch_button.setEnabled(in_3d)
        self._update_sketch_tools()
        self._update_part_labels()
        if on_canvas:
            self.canvas.setFocus()
        else:
            self.view3d.refresh()
            self.view3d.setFocus()

    def _seat_card(self, page: QWidget) -> None:
        card = self.proposal_card
        if card.parentWidget() is page:
            return
        shown = not card.isHidden()
        card.setParent(page)  # which hides it
        card.reposition()
        card.setVisible(shown)

    def _update_sketch_tools(self) -> None:
        """Sketch tools act on the canvas: in 2D always, in 3D while an open sketch can be
        drawn on, facing it or turned up to 70° from it (ADR 0016)."""
        backdrop = self._backdrop if self.mode == "3d" else None
        drawing = self.mode == "2d" or (
            backdrop is not None and backdrop.drawable and self.sketch_open is not None
        )
        for action in self._sketch_actions():
            action.setEnabled(drawing)
        sketching = self.mode == "3d" and self.sketch_open is not None
        for action in (self.finish_sketch_action, self.cancel_sketch_action):
            action.setEnabled(sketching)
        self.face_action.setEnabled(backdrop is not None and not backdrop.facing)
        if backdrop is None or backdrop.facing:
            hint = "Right-drag to orbit · N to face the sketch"
        elif backdrop.drawable:
            hint = "At an angle: draw on the sketch's plane, or N to face it"
        else:
            hint = "Turned too far to draw: press N to face the sketch and keep drawing"
        self.sketch_hint.setText(hint)
        self.sketch_bar.adjustSize()
        if drawing:
            self._update_constraint_actions()
        self._update_tool_state()

    def sketch(self) -> None:
        """Sketch on the picked plane, or edit the picked sketch; with neither, say how."""
        if self.mode != "3d":
            return
        plane = self.session.picked_plane
        picked = [i for i in self.session.selection if self._plane_of(i) is not None]
        if plane is not None:
            self.new_sketch(plane)
        elif len(picked) == 1:
            self.edit_sketch(picked[0])
        else:
            self.show_message(
                "Pick a plane (Top, Front, Right), a flat face, or a sketch, then Sketch"
            )
            menu = self.sketch_button.menu()
            if menu is not None and self.sketch_button.isVisible():  # the planes, to pick one
                menu.popup(self.sketch_button.mapToGlobal(self.sketch_button.rect().bottomLeft()))

    def new_sketch(self, plane: Plane | FaceRef) -> None:
        """Start a sketch on `plane`, or on a flat face of the part (ADR 0016), and edit it in
        3D, facing it. Cancel removes it."""
        if self.mode != "3d":
            return
        self._close_sketch()
        entry = self.session.history_position
        result = self.session.execute(CreateSketch(plane=plane))
        if isinstance(result, Applied):
            (sketch,) = result.created_ids
            self._open_sketch(sketch, entry)
            where = place_name(self.session.document, plane)
            self.show_message(f"Sketching on {where}: Finish (✓) when it's done")
        else:
            self.show_message(f"Can't sketch there: {result.errors[0].message}")

    def edit_sketch(self, sketch: EntityId) -> None:
        """Edit `sketch`: in 3D, facing its plane, until Finish or Cancel; in 2D, on the canvas."""
        self.controller.cancel_operation()
        if self.mode == "3d":
            if sketch != self.sketch_open:
                self._close_sketch()
                self._open_sketch(sketch, self.session.history_position)
            return
        self.session.set_active_sketch(sketch)
        self.canvas.zoom_to_fit()

    def _open_picked(self, found: object) -> None:
        """A double-click in the 3D view: sketch on a plane or a face, or edit a sketch."""
        if isinstance(found, Plane | FaceRef):
            self.new_sketch(found)
        elif isinstance(found, str):
            self.edit_sketch(EntityId(found))

    def _open_sketch(self, sketch: EntityId, entry: int) -> None:
        plane = self._plane_of(sketch)
        if plane is None:
            return
        self.controller.cancel_operation()
        self.session.set_active_sketch(sketch)
        self.sketch_open, self._sketch_entry = sketch, entry
        if not self._face_plane(plane, sketch):
            self.sketch_open = None  # its face is gone: nothing to face, so it isn't open
            self._show_views()
            return
        named = titles(self.session.document.features)[sketch]
        where = place_name(self.session.document, plane)
        self.sketch_title.setText(f"{named}  ·  {where}")
        self.features.rebuild()

    def _face_plane(
        self, plane: Plane | FaceRef, sketch: EntityId | None, document: Document | None = None
    ) -> bool:
        """Turn the canvas, over the part, to face `plane` (a plane or a face, where it is in
        `document`, the session's by default), from where the 3D view looks. False, with the
        reason shown, when a face can't be found."""
        document = self.session.document if document is None else document
        frame = Bus(document).queries.plane_frame(plane)
        if isinstance(frame, Error):
            self.show_message(f"Can't face {place_name(document, plane)}: {frame.message}")
            return False
        backdrop = self._backdrop
        if backdrop is not None and backdrop.plane == plane and backdrop.frame == frame:
            backdrop.sketch = sketch
        else:
            camera = self.view3d.camera
            if backdrop is not None:
                camera = backdrop.camera(self.canvas.view, self.views.width(), self.views.height())
            self._backdrop = Backdrop(
                plane, frame, sketch, self.view3d.paint_scene, self.view3d.shows
            )
            look(self.canvas.view, frame, camera, self.views.width(), self.views.height())
        self.sketch_title.setText(f"{place_name(document, plane)}  ·  proposal")
        self._show_views()
        return True

    def _follow_face(self) -> None:
        """The open sketch's face moved (an edit to its extrude, undo, Claude): the backdrop
        follows it, so the sketch stays where its face is (ADR 0016)."""
        backdrop = self._backdrop
        if backdrop is None or self.sketch_open is None or self.session.space is not Space.PART:
            return
        plane = self._plane_of(self.sketch_open)
        if plane is None:
            return
        frame = self.session.queries.plane_frame(plane)
        if isinstance(frame, Error) or (plane == backdrop.plane and frame == backdrop.frame):
            return
        backdrop.plane, backdrop.frame = plane, frame
        self.canvas.update()

    def finish_sketch(self) -> None:
        """Close the sketch being edited in 3D: its changes stay, each its own undo step."""
        sketch = self.sketch_open
        if sketch is None:
            return
        self._close_sketch()
        named = titles(self.session.document.features).get(sketch, sketch)
        self.show_message(f"Finished {named}")

    def cancel_sketch(self) -> None:
        """Close the sketch, undoing everything since it opened: a new one is removed. Redo
        brings it all back."""
        if self.sketch_open is None:
            return
        entry, undone = self._sketch_entry, 0
        self.controller.cancel_operation()
        while self.session.history_position > entry and self.session.bus.undo_label:
            self.session.undo()
            undone += 1
        self._close_sketch()
        steps = f"{undone} change{'s' if undone != 1 else ''}"
        self.show_message(f"Cancelled the sketch: undid {steps} (Redo brings them back)")

    def _close_sketch(self) -> None:
        """Back to the part in 3D, the view where the sketch left it."""
        self._previewing = None
        backdrop = self._backdrop
        if backdrop is None and self.sketch_open is None:
            return
        self.controller.cancel_operation()
        if backdrop is not None:
            self.view3d.camera = backdrop.camera(
                self.canvas.view, self.views.width(), self.views.height()
            )
        self._backdrop, self.sketch_open = None, None
        self._show_views()

    def _face(self) -> None:
        self.canvas.face()
        self._update_sketch_tools()

    def _sketch_gone(self) -> None:
        """The sketch open in 3D was undone or deleted: close it. (The 2D tab's document coming
        in says nothing about the part's sketches.)"""
        if (
            self.session.space is Space.PART
            and self.sketch_open is not None
            and self._plane_of(self.sketch_open) is None
        ):
            self._close_sketch()

    def _plane_of(self, id: EntityId) -> Plane | FaceRef | None:
        """The plane or face of the sketch `id`, or None if the part has no such sketch."""
        return next(
            (
                f.plane
                for f in self.session.document.features
                if isinstance(f, Sketch) and f.id == id
            ),
            None,
        )

    def start_extrude(self) -> None:
        """Open the Extrude form for the picked sketch, or the one last edited. A sketch open
        in 3D is finished first, as extruding it means it's done."""
        if self.mode != "3d":
            return
        picked = [i for i in self.session.selection if self._plane_of(i) is not None]
        if len(picked) == 1:
            self.session.set_active_sketch(picked[0])
        self._close_sketch()
        if self.session.active_sketch is None:
            self.show_message("The part has no sketch to extrude: pick a plane and Sketch")
            return
        old = getattr(self, "extrude_form", None)
        if old is not None:
            old.deleteLater()
        self.extrude_form = ExtrudeForm(self.session, self.views)
        self.extrude_form.extruded.connect(self._extruded)
        self.extrude_form.closed.connect(lambda: self.views.currentWidget().setFocus())
        self.extrude_form.open()

    def _extruded(self, extrude: EntityId) -> None:
        self.show_message(f"Extruded {extrude}: the part is {volume_text(self.session)}")

    def _update_part_labels(self) -> None:
        """The 2D tab's sketch label, and the solid's volume in the status bar."""
        volume = volume_text(self.session)
        show = self.mode == "3d" and volume != "No solid yet"
        self.solid_label.setText(f"Solid {volume}" if show else "")
        sketch = self.session.active_sketch
        if sketch is None:
            self.sketch_label.setText("No sketch")
            return
        plane = self._plane_of(sketch)
        named = titles(self.session.document.features)[sketch]
        where = place_name(self.session.document, plane) if plane is not None else ""
        self.sketch_label.setText(f"Editing {named}  ·  {where}")
        self.sketch_label.adjustSize()

    def _build_sketch_bar(self) -> None:
        """What's being edited in 3D, and how to close it: over the views' top left."""
        bar = QFrame(self.views)
        bar.setObjectName("sketch-bar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(SPACE.m, SPACE.xs, SPACE.m, SPACE.xs)
        row.setSpacing(SPACE.m)
        self.sketch_title = QLabel()
        self.sketch_title.setObjectName("sketch-bar-title")
        self.sketch_hint = QLabel()
        self.sketch_hint.setObjectName("sketch-bar-hint")
        self.finish_button = QPushButton("✓  Finish")
        self.finish_button.setObjectName("finish-sketch")
        self.finish_button.setToolTip("Finish the sketch: its changes stay")
        self.finish_button.clicked.connect(self.finish_sketch)
        self.cancel_button = QPushButton("✗  Cancel")
        self.cancel_button.setObjectName("cancel-sketch")
        self.cancel_button.setToolTip("Close the sketch and undo everything done in it")
        self.cancel_button.clicked.connect(self.cancel_sketch)
        for widget in (self.sketch_title, self.sketch_hint, self.cancel_button, self.finish_button):
            row.addWidget(widget)
        bar.move(SPACE.m, SPACE.m)
        bar.hide()
        self.sketch_bar = bar

    def _sketch_actions(self) -> list[QAction]:
        return [
            *self.tool_actions.values(),
            *self.constraint_actions.values(),
            self.construction_action,
            self.grid_action,
            self.constraints_action,
            self.snap_action,
        ]

    def _fit(self) -> None:
        if self.views.currentWidget() is self.view3d:
            self.view3d.fit()
        else:
            self.canvas.zoom_to_fit()

    def _update_constraint_actions(self) -> None:
        if not self.tool_actions["Select"].isEnabled():
            return  # waiting while nothing is drawn on; refreshed when drawing can go on
        options = constraint_options(self.session)
        for type, action in self.constraint_actions.items():
            option = options.get(type.value)
            usable = option is not None and option.error is None
            if option is None:
                tip = NOTHING_TO_CONSTRAIN
            elif option.error is not None:
                tip = option.error.message
            else:
                tip = f"Add a {type.value} constraint to {len(option.refs)} selected"
            action.setEnabled(usable)
            if action.toolTip() != tip:
                action.setToolTip(tip)
        entities = self.session.document.entities
        self.construction_action.setEnabled(
            any(hasattr(entities.get(i), "construction") for i in self.session.selection)
        )

    def _update_tool_state(self) -> None:
        tool = self.controller.active
        self.tool_actions[tool.name].setChecked(True)
        drawing = self.tool_actions[tool.name].isEnabled()
        self.hint_label.setText(tool.hint if drawing else "")

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
