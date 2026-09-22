"""The properties panel turns field edits into ModifyEntity commands."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit

from caliper.app.properties import format_number, parse_number
from caliper.contracts.commands import (
    CreateConstraint,
    CreateDimension,
    CreateDistanceDimension,
    CreateLine,
    CreateRectangle,
    ModifyEntity,
)
from caliper.contracts.document import (
    ConstraintType,
    DistanceOrientation,
    Feature,
    Line,
    Point2,
    Ref,
)


@pytest.fixture
def rect(window) -> str:
    result = window.session.execute(
        CreateRectangle(corner=Point2(x=10, y=20), width=100, height=50)
    )
    (rect_id,) = result.created_ids
    window.session.set_selection(frozenset({rect_id}))
    window.session.bus.sent.clear()
    return rect_id


def type_into(qtbot, field: QLineEdit, text: str) -> None:
    field.setFocus()
    field.selectAll()
    qtbot.keyClicks(field, text)
    qtbot.keyClick(field, Qt.Key.Key_Return)


def test_fields_mirror_the_entity(window, rect) -> None:
    fields = window.properties.fields
    assert set(fields) == {"corner.x", "corner.y", "width", "height", "construction"}
    assert fields["corner.x"].text() == "10"
    assert fields["height"].text() == "50"


def test_editing_a_point_component_keeps_the_other(window, qtbot, bus, rect) -> None:
    type_into(qtbot, window.properties.fields["corner.y"], "-5.5")
    assert bus.sent == [ModifyEntity(id=rect, changes={"corner": Point2(x=10.0, y=-5.5)})]


def test_an_unchanged_value_sends_nothing(window, qtbot, bus, rect) -> None:
    type_into(qtbot, window.properties.fields["width"], "100.0")
    assert bus.sent == []


def test_rejected_edit_marks_the_field_and_changes_nothing(window, qtbot, rect) -> None:
    before = window.session.document
    type_into(qtbot, window.properties.fields["width"], "-5")
    assert window.session.document == before
    assert window.properties.fields["width"].property("invalid") is True
    assert window.properties.error.text() == "width must be greater than 0"
    assert not window.properties.error.isHidden()


def test_text_that_isnt_a_number_is_caught_before_the_bus(window, qtbot, bus, rect) -> None:
    type_into(qtbot, window.properties.fields["width"], "wide")
    assert bus.sent == []
    assert window.properties.error.text() == "Enter a number"


def test_undo_refreshes_the_fields(window, qtbot, rect) -> None:
    type_into(qtbot, window.properties.fields["width"], "120")
    window.canvas.setFocus()
    window.undo_action.trigger()
    assert window.properties.fields["width"].text() == "100"
    assert window.redo_action.text() == "Redo Change Width"


def test_multiple_selection_shows_a_count(window) -> None:
    ids = [
        window.session.execute(
            CreateLine(start=Point2(x=0, y=i), end=Point2(x=10, y=i))
        ).created_ids[0]
        for i in range(3)
    ]
    window.session.set_selection(frozenset(ids))
    assert window.properties.fields == {}
    assert window.properties.entity_id is None


def test_enum_fields_are_combo_boxes(window, bus) -> None:
    session = window.session
    (rect,) = session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=10, height=10)
    ).created_ids
    (dim,) = session.execute(
        CreateDistanceDimension(
            a=Ref(entity=rect, feature=Feature.BOTTOM_LEFT),
            b=Ref(entity=rect, feature=Feature.BOTTOM_RIGHT),
            orientation=DistanceOrientation.ALIGNED,
            offset=-5,
        )
    ).created_ids
    session.set_selection(frozenset({dim}))
    combo = window.properties.fields["orientation"]
    assert isinstance(combo, QComboBox)
    combo.setCurrentText("horizontal")
    assert bus.sent[-1] == ModifyEntity(
        id=dim, changes={"orientation": DistanceOrientation.HORIZONTAL}
    )


@pytest.mark.parametrize(("value", "text"), [(120.0, "120"), (0.1, "0.1"), (-2.5, "-2.5")])
def test_number_formatting_round_trips(value: float, text: str) -> None:
    assert format_number(value) == text
    assert parse_number(text) == value


@pytest.mark.parametrize("text", ["", "abc", "nan", "inf", "1e999"])
def test_parse_number_rejects_non_finite_and_garbage(text: str) -> None:
    assert parse_number(text) is None


def test_selecting_does_not_resize_the_canvas(window, qtbot) -> None:
    before = window.canvas.width()
    result = window.session.execute(
        CreateRectangle(corner=Point2(x=0, y=0), width=12345.678, height=50)
    )
    window.session.set_selection(frozenset(result.created_ids))
    qtbot.wait(50)
    assert window.properties.fields["width"].isVisible()
    assert window.canvas.width() == before


# --- V1.5 fields --------------------------------------------------------------------------


@pytest.fixture
def length(window) -> tuple[str, str]:
    """A 100 mm line with a driven length dimension, the dimension selected."""
    s = window.session
    (line,) = s.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=0))).created_ids
    (dim,) = s.execute(
        CreateDimension(
            refs=(Ref(entity=line, feature=Feature.CURVE),), placement=Point2(x=50, y=10)
        )
    ).created_ids
    s.set_selection(frozenset({dim}))
    s.bus.sent.clear()
    return line, dim


def test_construction_is_a_checkbox_that_sends_one_command(window, qtbot, bus, rect) -> None:
    box = window.properties.fields["construction"]
    assert isinstance(box, QCheckBox)
    assert not box.isChecked()
    box.click()
    assert bus.sent == [ModifyEntity(id=rect, changes={"construction": True})]
    assert window.undo_action.text() == "Undo Change Construction"
    window.undo_action.trigger()
    assert not window.properties.fields["construction"].isChecked()


def test_a_driven_value_is_empty_and_shows_the_measurement(window, length) -> None:
    field = window.properties.fields["value"]
    assert isinstance(field, QLineEdit)
    assert field.text() == ""
    assert field.placeholderText() == "100 (driven)"


def test_typing_a_value_drives_the_geometry(window, qtbot, bus, length) -> None:
    line, dim = length
    type_into(qtbot, window.properties.fields["value"], "120")
    assert bus.sent == [ModifyEntity(id=dim, changes={"value": 120.0})]
    entity = window.session.document.entities[line]
    assert isinstance(entity, Line)
    assert entity.end.x - entity.start.x == pytest.approx(120)


def test_undoing_back_to_driven_refreshes_instead_of_crashing(window, qtbot, length) -> None:
    type_into(qtbot, window.properties.fields["value"], "120")
    window.canvas.setFocus()
    window.undo_action.trigger()
    field = window.properties.fields["value"]
    assert field.text() == ""
    assert field.placeholderText() == "100 (driven)"


def test_clearing_a_driving_value_makes_it_driven(window, qtbot, bus, length) -> None:
    _, dim = length
    type_into(qtbot, window.properties.fields["value"], "120")
    bus.sent.clear()
    field = window.properties.fields["value"]
    field.setFocus()
    field.selectAll()
    qtbot.keyClick(field, Qt.Key.Key_Delete)
    qtbot.keyClick(field, Qt.Key.Key_Return)
    assert bus.sent == [ModifyEntity(id=dim, changes={"value": None})]


def test_an_empty_driven_value_sends_nothing(window, qtbot, bus, length) -> None:
    field = window.properties.fields["value"]
    field.setFocus()
    qtbot.keyClick(field, Qt.Key.Key_Return)
    assert bus.sent == []


def test_constraint_references_read_as_text(window) -> None:
    s = window.session
    (a,) = s.execute(CreateLine(start=Point2(x=0, y=0), end=Point2(x=100, y=1))).created_ids
    (c,) = s.execute(
        CreateConstraint(
            type=ConstraintType.HORIZONTAL, refs=(Ref(entity=a, feature=Feature.CURVE),)
        )
    ).created_ids
    s.set_selection(frozenset({c}))
    texts = [label.text() for label in window.properties.findChildren(QLabel)]
    assert f"{a} curve" in texts
