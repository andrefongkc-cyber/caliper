"""The Dimension tool on the V1.5 engine: pick, place, type the value; and editing labels."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from caliper.app.dimension_layout import layout
from caliper.contracts.commands import (
    CreateCircle,
    CreateDimension,
    CreateLine,
    CreateRectangle,
    ModifyEntity,
)
from caliper.contracts.document import (
    AngleDimension,
    Circle,
    DistanceDimension,
    DistanceOrientation,
    Feature,
    Line,
    Point2,
    RadialDimension,
    RadialMeasure,
    Ref,
)
from tests.app.test_canvas_editing import double_click

P = Point2


def add(window, command) -> str:
    (id,) = window.session.execute(command).created_ids
    window.session.bus.sent.clear()
    return id


def entry_text() -> str:
    return QApplication.focusWidget().text()


def type_and_return(qtbot, text: str | None = None) -> None:
    field = QApplication.focusWidget()
    if text is not None:
        field.selectAll()
        qtbot.keyClicks(field, text)
    qtbot.keyClick(field, Qt.Key.Key_Return)


def created(window) -> list:
    before = window.session.bus.sent
    return [c for c in before if isinstance(c, CreateDimension)]


def only_new(window, *existing: str):
    (id,) = [i for i in window.session.document.entities if i not in existing]
    return id, window.session.document.entities[id]


def test_a_line_placed_above_is_a_driving_length_at_its_size(window, driver, qtbot) -> None:
    line = add(window, CreateLine(start=P(x=0, y=20), end=P(x=60, y=20)))
    driver.tool("Dimension")
    driver.click(20, 20)  # on the line, away from its midpoint (a point feature of its own)
    driver.click(30, 40)
    assert entry_text() == "60"  # the measured value, ready to accept or overwrite
    type_and_return(qtbot)
    _, dim = only_new(window, line)
    assert isinstance(dim, DistanceDimension)
    assert dim.value == 60.0
    geo = layout(dim.orientation, P(x=0, y=20), P(x=60, y=20), dim.offset)
    assert geo.start.y == pytest.approx(40)  # drawn where it was placed
    assert window.undo_action.text().startswith("Undo Create")


def test_a_horizontal_dimension_of_a_slanted_pair_lands_where_it_was_placed(
    window, driver, qtbot
) -> None:
    rect = add(window, CreateRectangle(corner=P(x=0, y=0), width=100, height=40))
    driver.tool("Dimension")
    driver.click(0, 0)
    driver.click(100, 40)
    driver.click(50, 60)
    type_and_return(qtbot)
    id, dim = only_new(window, rect)
    assert isinstance(dim, DistanceDimension)
    assert dim.orientation is DistanceOrientation.HORIZONTAL
    assert dim.offset == pytest.approx(40)  # midpoint y 20 to placement y 60
    assert window.session.queries.dimension_value(id) == pytest.approx(100)


def test_typing_another_value_resizes_the_line(window, driver, qtbot) -> None:
    line = add(window, CreateLine(start=P(x=0, y=20), end=P(x=60, y=20)))
    driver.tool("Dimension")
    driver.click(20, 20)  # on the line, away from its midpoint (a point feature of its own)
    driver.click(30, 40)
    type_and_return(qtbot, "80")
    entity = window.session.document.entities[line]
    assert isinstance(entity, Line)
    assert entity.end.x - entity.start.x == pytest.approx(80)


def test_typing_shows_what_the_value_would_do_before_committing(window, driver, qtbot) -> None:
    add(window, CreateLine(start=P(x=0, y=20), end=P(x=60, y=20)))
    driver.tool("Dimension")
    driver.click(20, 20)  # on the line, away from its midpoint (a point feature of its own)
    driver.click(30, 40)
    qtbot.keyClicks(QApplication.focusWidget(), "8")  # replaces the selected "60"
    tool = window.controller.active
    assert tool.typed is not None
    assert tool.typed.bus.document != window.session.document
    assert window.session.bus.sent == []  # nothing committed yet


def test_a_circle_placed_outside_is_a_diameter(window, driver, qtbot) -> None:
    circle = add(window, CreateCircle(center=P(x=40, y=40), radius=10))
    driver.tool("Dimension")
    driver.click(50, 40)
    driver.click(70, 40)
    assert entry_text() == "20"
    type_and_return(qtbot, "30")
    _, dim = only_new(window, circle)
    assert isinstance(dim, RadialDimension)
    assert dim.measure is RadialMeasure.DIAMETER
    entity = window.session.document.entities[circle]
    assert isinstance(entity, Circle)
    assert entity.radius == pytest.approx(15)


def test_two_lines_make_an_angle(window, driver, qtbot) -> None:
    a = add(window, CreateLine(start=P(x=0, y=0), end=P(x=60, y=0)))
    b = add(window, CreateLine(start=P(x=0, y=0), end=P(x=0, y=60)))
    driver.tool("Dimension")
    driver.click(40, 0)
    driver.click(0, 40)
    driver.click(15, 15)
    assert entry_text() == "90"
    type_and_return(qtbot)
    _, dim = only_new(window, a, b)
    assert isinstance(dim, AngleDimension)
    assert dim.value == 90.0


def test_a_size_already_fixed_is_added_as_driven_and_says_why(window, driver, qtbot) -> None:
    line = add(window, CreateLine(start=P(x=0, y=20), end=P(x=60, y=20)))
    curve = Ref(entity=line, feature=Feature.CURVE)
    first = add(window, CreateDimension(refs=(curve,), placement=P(x=30, y=30), value=60.0))
    driver.tool("Dimension")
    driver.click(20, 20)  # on the line, away from its midpoint (a point feature of its own)
    driver.click(30, 45)
    type_and_return(qtbot)
    _, dim = only_new(window, line, first)
    assert isinstance(dim, DistanceDimension)
    assert dim.value is None
    assert window.statusBar().currentMessage() == (
        f"Added as a driven dimension: {first} already fix this size"
    )


def test_esc_cancels_the_dimension(window, driver, qtbot) -> None:
    add(window, CreateLine(start=P(x=0, y=20), end=P(x=60, y=20)))
    driver.tool("Dimension")
    driver.click(20, 20)  # on the line, away from its midpoint (a point feature of its own)
    driver.click(30, 40)
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert window.session.bus.sent == []
    assert window.controller.active.name == "Dimension"
    assert not window.controller.active.busy


def test_an_empty_first_click_says_what_to_click(window, driver) -> None:
    driver.tool("Dimension")
    driver.click(300, 300)
    assert window.session.bus.sent == []
    assert window.statusBar().currentMessage() == (
        "Click a point, line, circle, or arc to dimension"
    )


def test_double_clicking_a_driven_label_makes_it_drive(window, driver, bus, qtbot) -> None:
    line = add(window, CreateLine(start=P(x=0, y=20), end=P(x=60, y=20)))
    curve = Ref(entity=line, feature=Feature.CURVE)
    dim = add(window, CreateDimension(refs=(curve,), placement=P(x=30, y=40)))
    double_click(driver, 30, 40)
    assert entry_text() == "60"
    type_and_return(qtbot, "90")
    assert bus.sent == [ModifyEntity(id=dim, changes={"value": 90.0})]
    entity = window.session.document.entities[line]
    assert isinstance(entity, Line)
    assert entity.end.x - entity.start.x == pytest.approx(90)


def test_an_emptied_entry_adds_a_driven_dimension(window, driver, qtbot) -> None:
    line = add(window, CreateLine(start=P(x=0, y=20), end=P(x=60, y=20)))
    driver.tool("Dimension")
    driver.click(20, 20)
    driver.click(30, 40)
    field = QApplication.focusWidget()
    field.selectAll()
    qtbot.keyClick(field, Qt.Key.Key_Delete)
    qtbot.keyClick(field, Qt.Key.Key_Return)
    _, dim = only_new(window, line)
    assert isinstance(dim, DistanceDimension)
    assert dim.value is None
