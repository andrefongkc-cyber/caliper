"""Tool and action icons: every tool has one, tints follow state, and they render sharply."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon

from caliper.app import icons, theme


def test_every_tool_and_toolbar_action_has_an_icon(window) -> None:
    for name, action in window.tool_actions.items():
        assert name.lower() in icons.NAMES
        assert not action.icon().isNull(), name
    for action in (window.undo_action, window.redo_action, window.fit_action):
        assert not action.icon().isNull()


def test_icon_strokes_use_the_token_colour() -> None:
    markup = icons.svg("rectangle", theme.ACCENT).decode()
    assert theme.ACCENT.name() in markup
    assert "currentColor" not in markup


def test_checked_icons_are_tinted_with_the_accent(qapp) -> None:
    icon = icons.icon("line")
    on = icon.pixmap(QSize(20, 20), QIcon.Mode.Normal, QIcon.State.On).toImage()
    off = icon.pixmap(QSize(20, 20), QIcon.Mode.Normal, QIcon.State.Off).toImage()
    painted_on = {
        on.pixelColor(x, y).rgb()
        for x in range(on.width())
        for y in range(on.height())
        if on.pixelColor(x, y).alpha() == 255
    }
    painted_off = {
        off.pixelColor(x, y).rgb()
        for x in range(off.width())
        for y in range(off.height())
        if off.pixelColor(x, y).alpha() == 255
    }
    assert theme.ACCENT.rgb() in painted_on
    assert theme.TEXT.rgb() in painted_off


def test_pixmaps_carry_the_device_pixel_ratio(qapp) -> None:
    image = icons.pixmap("circle", 20, theme.TEXT, ratio=2.0)
    assert (image.width(), image.height()) == (40, 40)
    assert image.devicePixelRatio() == 2.0
    assert image.deviceIndependentSize().toSize() == QSize(20, 20)


def test_every_icon_draws_something(qapp) -> None:
    for name in icons.NAMES:
        image = icons.pixmap(name, 20, theme.TEXT, 2.0).toImage()
        opaque = sum(
            1
            for x in range(image.width())
            for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 128
        )
        assert opaque > 30, name


def test_tool_bar_drops_labels_when_the_window_is_narrow(window, qtbot) -> None:
    window.resize(1200, 700)
    qtbot.wait(20)
    assert window.tool_bar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    window.resize(800, 700)
    qtbot.wait(20)
    assert window.tool_bar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert window.tool_actions["Line"].toolTip() == "Line (L)"
