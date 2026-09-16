"""The Fillet tool: click two lines, type a radius, see the engine's own preview."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.app.tools.fillet import FilletPhase
from caliper.contracts.commands import CreateCircle, CreateLine, FilletCorner
from caliper.contracts.document import Arc, Line, Point2


@pytest.fixture
def corner(window) -> tuple[str, str]:
    session = window.session
    (across,) = session.execute(
        CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=0))
    ).created_ids
    (up,) = session.execute(
        CreateLine(start=Point2(x=100, y=0), end=Point2(x=100, y=80))
    ).created_ids
    session.bus.sent.clear()
    return across, up


def type_keys(qtbot, text: str) -> None:
    for char in text:
        widget = QApplication.focusWidget()
        if char == "\n":
            qtbot.keyClick(widget, Qt.Key.Key_Return)
        else:
            qtbot.keyClicks(widget, char)


def pick_both(driver) -> None:
    driver.canvas.setFocus()
    driver.tool("Fillet")
    driver.click(50, 0)  # the across line
    driver.click(100, 40)  # the up line


def test_two_clicks_then_a_radius_rounds_the_corner(window, driver, bus, qtbot, corner) -> None:
    across, up = corner
    pick_both(driver)
    tool = window.controller.active
    assert tool.phase is FilletPhase.RADIUS
    type_keys(qtbot, "20\n")
    assert bus.sent == [FilletCorner(a=across, b=up, radius=20.0)]
    entities = window.session.document.entities
    arcs = [e for e in entities.values() if isinstance(e, Arc)]
    assert len(arcs) == 1
    assert arcs[0].radius == 20.0
    assert entities[across] == Line(start=Point2(x=0, y=0), end=Point2(x=80, y=0))
    assert window.undo_action.text() == "Undo Fillet Corner"
    assert not tool.busy


def test_the_preview_is_the_engine_s_own_answer(window, driver, bus, corner) -> None:
    pick_both(driver)
    tool = window.controller.active
    assert tool.preview is not None
    arcs = [e for e in tool.preview.entities.values() if isinstance(e, Arc)]
    assert len(arcs) == 1
    assert arcs[0].radius == pytest.approx(5.0)  # the default, before anything is typed
    assert bus.sent == []  # nothing has touched the real document
    assert len(window.session.document.entities) == 2


def test_typing_updates_the_preview_live(window, driver, qtbot, corner) -> None:
    pick_both(driver)
    type_keys(qtbot, "30")
    tool = window.controller.active
    (arc,) = [e for e in tool.preview.entities.values() if isinstance(e, Arc)]
    assert arc.radius == 30.0
    assert not tool.session.document.entities.keys() - {c for c in corner}


def test_a_radius_that_doesnt_fit_explains_the_limit(window, driver, bus, qtbot, corner) -> None:
    pick_both(driver)
    type_keys(qtbot, "500")
    tool = window.controller.active
    assert tool.preview is None
    assert "must stay under 80" in window.statusBar().currentMessage()
    type_keys(qtbot, "\n")
    assert bus.sent == []  # Return with an impossible radius sends nothing
    assert tool.busy


def test_lines_that_dont_meet_are_refused_by_the_engine(window, driver, bus, qtbot) -> None:
    session = window.session
    session.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=0)))
    session.execute(CreateLine(start=Point2(x=0, y=60), end=Point2(x=100, y=60)))
    session.bus.sent.clear()
    driver.canvas.setFocus()
    driver.tool("Fillet")
    driver.click(50, 0)
    driver.click(50, 60)
    assert window.controller.active.preview is None
    assert "share exactly one endpoint" in window.statusBar().currentMessage()


def test_clicking_anything_but_a_line_says_so(window, driver, bus) -> None:
    window.session.execute(CreateCircle(center=Point2(x=0, y=0), radius=20))
    window.session.bus.sent.clear()
    driver.canvas.setFocus()
    driver.tool("Fillet")
    driver.click(20, 0)
    assert window.controller.active.phase is FilletPhase.FIRST
    assert (
        window.statusBar().currentMessage() == "Click a line: a fillet rounds where two lines meet"
    )


def test_escape_cancels_without_changing_anything(window, driver, bus, corner) -> None:
    pick_both(driver)
    driver.key(Qt.Key.Key_Escape)
    tool = window.controller.active
    assert tool.phase is FilletPhase.FIRST
    assert tool.preview is None
    assert bus.sent == []
    assert len(window.session.document.entities) == 2


def test_the_hint_walks_you_through_it(window, driver, corner) -> None:
    driver.canvas.setFocus()
    driver.tool("Fillet")
    assert "click one line" in window.hint_label.text()
    driver.click(50, 0)
    assert "the other line" in window.hint_label.text()
    driver.click(100, 40)
    assert "type a radius" in window.hint_label.text()


def test_the_preview_is_drawn_on_the_canvas(window, driver, qtbot, corner) -> None:
    from caliper.app import theme

    window.canvas.zoom_to_fit()

    def preview_pixels() -> int:
        image = window.canvas.grab().toImage()
        t = theme.PREVIEW
        return sum(
            1
            for x in range(image.width())
            for y in range(image.height())
            if abs(image.pixelColor(x, y).red() - t.red()) < 40
            and abs(image.pixelColor(x, y).green() - t.green()) < 40
            and abs(image.pixelColor(x, y).blue() - t.blue()) < 40
        )

    before = preview_pixels()
    pick_both(driver)
    type_keys(qtbot, "30")
    assert preview_pixels() > before + 30


def test_picked_lines_and_the_preview_are_told_apart(window, driver, qtbot, corner) -> None:
    """The chosen lines use the selection colour; only the result uses the preview colour."""
    from caliper.app import theme

    assert theme.SELECTED.name() != theme.PREVIEW.name()
    window.canvas.zoom_to_fit()
    pick_both(driver)
    image = window.canvas.grab().toImage()
    colours = {
        image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height())
    }
    assert theme.SELECTED.name() in colours
    assert theme.PREVIEW.name() in colours
