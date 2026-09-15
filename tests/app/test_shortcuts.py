"""Every action is reachable from the keyboard, and no two actions share a key."""

from PySide6.QtWidgets import QDialog

from caliper.app import shortcuts


def test_no_two_actions_share_a_shortcut(window) -> None:
    assert shortcuts.duplicate_shortcuts(window.actions()) == {}


def test_duplicate_detection_works(window) -> None:
    window.fit_action.setShortcut("L")  # the Line tool's key
    assert "L" in shortcuts.duplicate_shortcuts(window.actions())


def test_sheet_lists_every_action_with_a_shortcut(window) -> None:
    listed = {name for _, rows in shortcuts.sections(window.menus) for name, _ in rows}
    with_keys = {
        a.text().replace("&", "") for menu in window.menus for a in menu.actions() if a.shortcuts()
    }
    assert with_keys <= listed
    assert {"Rectangle", "Zoom to Fit", "Command Palette…", "Pan"} <= listed


def test_sheet_follows_a_changed_shortcut(window) -> None:
    window.grid_action.setShortcut("Shift+G")
    rows = dict(row for _, section in shortcuts.sections(window.menus) for row in section)
    assert rows["Show Grid"] != "G"


def test_sheet_opens_from_the_help_menu(window, monkeypatch) -> None:
    opened: list[QDialog] = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    window.shortcuts_action.trigger()
    (sheet,) = opened
    assert sheet.tree.topLevelItemCount() == len(shortcuts.sections(window.menus))


def test_tool_bar_tooltips_name_the_shortcut(window) -> None:
    assert window.tool_actions["Rectangle"].toolTip() == "Rectangle (R)"
    assert window.fit_action.toolTip() == "Zoom to Fit (F)"
