"""The chrome that folds away (2026-10-03): each side's panels behind a strip on the view's
edge, Timing popped out in a window of its own, the agent's prompt behind a button, and the
tools in a tray that slides out to the right of the line after the 2D/3D switch."""

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from caliper.app.collapse import POINT_LEFT, POINT_RIGHT, SLIDE_MS
from caliper.app.main_window import LEFT, MainWindow

LEFT_BUTTON = Qt.MouseButton.LeftButton


def right_docks(window: MainWindow) -> list:  # type: ignore[type-arg]
    return [window.properties_dock, window.commands_dock, window.checks_dock]


@pytest.fixture
def served(window: MainWindow) -> Iterator[MainWindow]:
    """The window with Claude Desktop able to connect, so Timing shows."""
    directory = Path(tempfile.mkdtemp(prefix="cal", dir="/tmp"))  # a socket path stays short
    assert window.serve_mcp(directory / "mcp.sock")
    yield window
    assert window.mcp is not None
    window.mcp.close()
    shutil.rmtree(directory, ignore_errors=True)


# --- The side panels ------------------------------------------------------------------------


def test_the_left_panel_folds_away_and_comes_back_as_wide(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    browser = window.browser_dock
    width, views = browser.width(), window.views.width()
    assert window.left_edge.text() == POINT_LEFT
    assert "Hide the left panel" in window.left_edge.toolTip()
    qtbot.mouseClick(window.left_edge, LEFT_BUTTON)
    assert browser.isHidden()
    assert not window.left_panel_action.isChecked()
    assert window.left_edge.text() == POINT_RIGHT
    assert "Show the left panel" in window.left_edge.toolTip()
    qtbot.waitUntil(lambda: window.views.width() >= views + width)  # the view takes the room
    qtbot.mouseClick(window.left_edge, LEFT_BUTTON)
    assert not browser.isHidden()
    qtbot.waitUntil(lambda: window.views.width() == views)
    assert browser.width() == width


def test_the_right_panel_folds_away_and_comes_back_in_order(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    before = [dock.geometry() for dock in right_docks(window)]
    views = window.views.width()
    assert window.right_edge.text() == POINT_RIGHT
    window.right_panel_action.trigger()  # ⌘⌥B
    assert all(dock.isHidden() for dock in right_docks(window))
    assert window.right_edge.text() == POINT_LEFT
    qtbot.waitUntil(lambda: window.views.width() >= views + before[0].width())
    window.right_panel_action.trigger()
    assert not any(dock.isHidden() for dock in right_docks(window))
    qtbot.waitUntil(lambda: [dock.geometry() for dock in right_docks(window)] == before)
    assert window.views.width() == views  # each as tall as it was, not shared out again
    assert window.timing_dock.isHidden()  # hidden before (no Claude Desktop), and still


def test_a_panel_moved_to_the_other_side_folds_with_that_side(window: MainWindow) -> None:
    window.addDockWidget(LEFT, window.checks_dock)
    window.left_panel_action.trigger()
    assert window.checks_dock.isHidden()
    assert not window.properties_dock.isHidden()
    window.left_panel_action.trigger()
    assert not window.checks_dock.isHidden()


def test_a_panel_hidden_before_folding_stays_hidden_after(window: MainWindow) -> None:
    window.commands_dock.hide()
    window.right_panel_action.trigger()
    window.right_panel_action.trigger()
    assert window.commands_dock.isHidden()
    assert not window.properties_dock.isHidden()


def test_the_folds_are_in_the_view_menu_and_the_palette(window: MainWindow) -> None:
    view = next(menu for menu in window.menus if menu.title() == "View")
    shown = view.actions()
    for action in (window.left_panel_action, window.right_panel_action, window.tray_action):
        assert action in shown
    titles = window.command_panel.visible_titles()
    assert {"Show Left Panel", "Show Right Panel", "Show Tool Tray", "Show Agent Prompt"} <= set(
        titles
    )
    assert window.left_panel_action.shortcut().toString() == "Ctrl+B"
    assert window.right_panel_action.shortcut().toString() == "Ctrl+Alt+B"


# --- Timing, popped out ---------------------------------------------------------------------


def test_timing_pops_out_and_stays_while_the_right_panel_is_folded(
    served: MainWindow,
) -> None:
    window = served
    dock = window.timing_dock
    assert not dock.isHidden()
    assert not dock.isFloating()
    assert window.pop_timing_action.isEnabled()
    window.pop_timing_action.trigger()
    assert dock.isFloating()
    assert dock.isVisible()
    seen = window.views.rect()
    seen.moveTopLeft(window.views.mapToGlobal(QPoint(0, 0)))
    assert seen.contains(dock.geometry().topRight())  # over the part's top right corner
    window.right_panel_action.trigger()  # fold
    assert window.properties_dock.isHidden()
    assert dock.isVisible()  # popped out, it stays
    dock.setFloating(False)  # docked again by its own button, on the folded side
    assert not window.pop_timing_action.isChecked()
    assert dock.isHidden()
    window.right_panel_action.trigger()  # unfold
    assert not dock.isHidden()
    assert not dock.isFloating()


def test_docked_timing_folds_with_the_right_panel(served: MainWindow) -> None:
    window = served
    window.right_panel_action.trigger()
    assert window.timing_dock.isHidden()
    window.right_panel_action.trigger()
    assert not window.timing_dock.isHidden()


def test_claude_desktop_connecting_while_folded_waits_for_the_panel(window: MainWindow) -> None:
    window.right_panel_action.trigger()
    directory = Path(tempfile.mkdtemp(prefix="cal", dir="/tmp"))
    try:
        assert window.serve_mcp(directory / "mcp.sock")
        assert window.timing_dock.isHidden()
        window.right_panel_action.trigger()
        assert not window.timing_dock.isHidden()
    finally:
        assert window.mcp is not None
        window.mcp.close()
        shutil.rmtree(directory, ignore_errors=True)


def test_without_claude_desktop_timing_cant_pop_out(window: MainWindow) -> None:
    assert not window.pop_timing_action.isEnabled()


# --- The agent's prompt ---------------------------------------------------------------------


def test_the_agents_prompt_is_hidden_until_its_button_shows_it(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    bar = window.prompt_bar
    assert bar.isHidden()
    assert window.prompt_button.text() == "Agent"
    height = window.views.height()
    qtbot.mouseClick(window.prompt_button, LEFT_BUTTON)
    assert bar.isVisible()
    assert window.prompt_action.isChecked()
    qtbot.waitUntil(lambda: window.views.height() < height)
    qtbot.mouseClick(window.prompt_button, LEFT_BUTTON)
    assert bar.isHidden()
    qtbot.waitUntil(lambda: window.views.height() == height)


def test_cmd_l_opens_the_prompt_to_type_and_hiding_it_gives_the_view_the_keys(
    window: MainWindow,
) -> None:
    window.ask_action.trigger()
    assert window.prompt_bar.isVisible()
    assert QApplication.focusWidget() is window.prompt_bar.input
    window.prompt_action.trigger()
    assert window.prompt_bar.isHidden()
    assert QApplication.focusWidget() is window.views.currentWidget()


def test_a_status_message_never_covers_the_agent_button(window: MainWindow) -> None:
    window.show_message("Saved part.caliper")
    assert window.prompt_button.isVisible()


# --- The tool tray --------------------------------------------------------------------------


def wide(window: MainWindow, qtbot) -> int:  # type: ignore[no-untyped-def]
    """A window wide enough for every tool, labelled: the tools' full width."""
    window.resize(1500, 800)
    tray = window.tray
    full = tray.bar.sizeHint().width()
    qtbot.waitUntil(lambda: tray.width() == full)
    return full


def test_the_tools_slide_back_into_the_line_and_their_keys_still_work(
    window: MainWindow,
    driver,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    tray = window.tray
    full = wide(window, qtbot)
    rectangle = tray.bar.widgetForAction(window.tool_actions["Rectangle"])
    assert rectangle.isVisible()
    assert tray.handle.text() == POINT_LEFT
    assert tray.handle.x() < tray.x()  # the line, then the tools
    qtbot.mouseClick(tray.handle, LEFT_BUTTON)
    assert not window.tray_action.isChecked()
    assert tray.handle.text() == POINT_RIGHT
    assert "Slide the tools out" in tray.handle.toolTip()
    qtbot.waitUntil(lambda: not tray.sliding and tray.width() == 0)
    assert not rectangle.isVisible()
    driver.canvas.setFocus()
    driver.key(Qt.Key.Key_R)  # the window's shortcut, not the hidden button's
    assert window.controller.active.name == "Rectangle"
    qtbot.mouseClick(tray.handle, LEFT_BUTTON)
    qtbot.waitUntil(lambda: not tray.sliding and tray.width() == full)
    assert rectangle.isVisible()


def test_halfway_out_the_tools_come_from_behind_the_line(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    tray = window.tray
    full = wide(window, qtbot)
    left = tray.x()
    window.tray_action.setChecked(False)
    qtbot.waitUntil(lambda: not tray.sliding and tray.width() == 0)
    window.tray_action.setChecked(True)
    tray._slide.pause()
    tray._slide.setCurrentTime(SLIDE_MS // 2)
    assert 0.0 < tray.out < 1.0
    qtbot.waitUntil(lambda: tray.width() == round(tray.out * full))
    assert tray.x() == left  # the tray starts at the line, and grows to the right
    assert tray.bar.x() == tray.width() - full < 0  # the tools' right end at its edge
    assert tray.bar.width() == full
    tray._slide.resume()
    qtbot.waitUntil(lambda: not tray.sliding and tray.width() == full)
    assert tray.bar.x() == 0


def overflow(window: MainWindow) -> bool:
    """Whether the tray's » menu shows, holding the tools that don't fit."""
    return any(
        button.isVisible()
        for button in window.tray.bar.children()
        if button.objectName() == "qt_toolbar_ext_button"
    )


def test_in_a_narrower_window_the_tray_takes_whats_left(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    """Too narrow for every tool: the tray fills the bar, the first tools stay in it, and the
    rest wait behind its » menu; narrower still, labels drop and they all fit again."""
    tray = window.tray
    full = wide(window, qtbot)
    assert not overflow(window)
    window.resize(1000, 700)
    qtbot.waitUntil(lambda: 0 < tray.width() < full)
    assert tray.isVisible()
    assert tray.bar.x() == 0
    assert tray.bar.width() == tray.width()
    assert window.sketch_button.isVisible()
    qtbot.waitUntil(lambda: overflow(window))
    window.resize(800, 700)
    qtbot.waitUntil(lambda: tray.bar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly)
    qtbot.waitUntil(lambda: tray.width() == tray.bar.sizeHint().width() < full)
    assert not overflow(window)


# --- Remembered between launches ---------------------------------------------------------------


def test_what_was_folded_away_is_folded_away_at_the_next_launch(
    window: MainWindow, new_window, qtbot
) -> None:  # type: ignore[no-untyped-def]
    window.left_panel_action.trigger()
    window.tray_action.trigger()
    window.prompt_action.trigger()
    again = new_window()
    assert not again.left_panel_action.isChecked()
    assert again.browser_dock.isHidden()
    assert again.left_edge.text() == POINT_RIGHT  # the strip offers to bring it back
    assert again.right_panel_action.isChecked()  # left alone, so as it was
    assert not any(dock.isHidden() for dock in right_docks(again))
    assert not again.tray_action.isChecked()
    assert again.tray.out == 0.0  # shut from the start:
    assert not again.tray.sliding  # nothing slides at launch
    assert again.tray.bar.isHidden()
    assert again.prompt_action.isChecked()
    assert again.prompt_bar.isVisible()


def test_a_fresh_install_starts_with_everything_out_but_the_prompt(window: MainWindow) -> None:
    assert window.left_panel_action.isChecked()
    assert window.right_panel_action.isChecked()
    assert window.tray_action.isChecked()
    assert not window.prompt_action.isChecked()


def test_panels_folded_at_launch_come_back_at_a_size_to_use(
    window: MainWindow, new_window, qtbot
) -> None:  # type: ignore[no-untyped-def]
    """Folded before the window was ever laid out, panels have only the widths they were
    built with and no heights yet: they come back that wide and sharing the height, much as
    a window that never folded them lays them out, none squeezed to a sliver."""
    fresh = [dock.geometry() for dock in right_docks(window)]
    width = window.browser_dock.width()
    window.left_panel_action.trigger()
    window.right_panel_action.trigger()
    again = new_window()
    assert again.browser_dock.isHidden()
    assert all(dock.isHidden() for dock in right_docks(again))
    again.left_panel_action.trigger()
    again.right_panel_action.trigger()
    assert not again.browser_dock.isHidden()
    qtbot.waitUntil(lambda: again.browser_dock.width() == width)

    def as_it_starts() -> bool:
        return all(
            dock.width() == start.width() and abs(dock.height() - start.height()) <= 16
            for dock, start in zip(right_docks(again), fresh, strict=True)
        )

    qtbot.waitUntil(as_it_starts)


def test_showing_it_again_is_remembered_too(window: MainWindow, new_window) -> None:  # type: ignore[no-untyped-def]
    window.right_panel_action.trigger()
    window.right_panel_action.trigger()
    again = new_window()
    assert again.right_panel_action.isChecked()
    assert not any(dock.isHidden() for dock in right_docks(again))
