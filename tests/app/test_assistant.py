"""The assistant in the window: ask in the prompt bar, watch it work, review, apply, undo.

A scripted model stands in for a real one, so these run offline; everything else is real:
the window, the session, the bus, and the document.
"""

import threading
from collections.abc import Callable, Sequence

import pytest

from caliper.ai.agent import Assistant
from caliper.ai.model import Message, ModelError, Reply, Stop, ToolCall, ToolOutcome, ToolSpec
from caliper.app.panels.assistant import step_text
from caliper.app.session import Author
from caliper.contracts.commands import CreateRectangle
from caliper.contracts.document import EntityId, Point2, Rectangle

E1 = EntityId("e1")


class ScriptedModel:
    name = "scripted"

    def __init__(self, script: Sequence[Reply | Callable[[], Reply]]) -> None:
        self.script = list(script)

    def reply(
        self, system: str, conversation: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Reply:
        step = self.script.pop(0)
        return step() if callable(step) else step


def calls(*calls: tuple[str, dict[str, object]]) -> Reply:
    return Reply(
        text="",
        calls=tuple(ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)),
        stop=Stop.TOOLS,
    )


RECTANGLE = ("create_rectangle", {"corner": {"x": 0, "y": 0}, "width": 100, "height": 50})
WIDTH_CHECK = (
    "run_check",
    {"metric": "bbox_width", "expected": 100, "tolerance": 0.001, "ids": ["e1"]},
)


def ask(window, qtbot, text: str) -> None:
    window.prompt_bar.input.setText(text)
    window.prompt_bar.input.returnPressed.emit()
    qtbot.waitUntil(lambda: not window.agent.busy)


def with_model(window, *script: Reply | Callable[[], Reply]) -> Assistant:
    assistant = Assistant(ScriptedModel(script))
    window.agent.set_assistant(assistant)
    return assistant


def test_a_request_creates_real_geometry_once_accepted_and_undoes_normally(window, qtbot) -> None:
    with_model(
        window,
        calls(RECTANGLE),
        calls(WIDTH_CHECK),
        Reply(text="Created a 100 x 50 rectangle at the origin."),
    )
    assert window.prompt_bar.chip.text() == "scripted"
    ask(window, qtbot, "Create a rectangle 100mm wide and 50mm tall.")
    session = window.session
    # Reviewed first: shown as a proposal, the document untouched.
    assert window.proposal_card.isVisible()
    assert window.proposal_card.title.text() == "Create Rectangle"
    assert dict(session.document.entities) == {}
    assert window.browser_tabs.currentWidget() is window.assistant_log
    assert window.assistant_log.lines() == [
        "You: Create a rectangle 100mm wide and 50mm tall.",
        "→ create_rectangle: Create Rectangle (e1)",
        "✓ run_check: 100",
        "scripted: Created a 100 x 50 rectangle at the origin.",
        "Proposed 1 change: accept or reject on the canvas.",
    ]
    window.proposal_card.accept_button.click()
    assert session.document.entities[E1] == Rectangle(
        corner=Point2(x=0.0, y=0.0), width=100.0, height=50.0
    )
    assert session.history[-1].author is Author.AGENT
    assert session.history[-1].commands == (
        CreateRectangle(corner=Point2(x=0.0, y=0.0), width=100.0, height=50.0, id=E1),
    )
    assert window.undo_action.text() == "Undo Create Rectangle"
    window.undo_action.trigger()
    assert dict(session.document.entities) == {}
    window.redo_action.trigger()
    assert E1 in session.document.entities


def test_a_follow_up_refers_to_what_the_last_request_made(window, qtbot) -> None:
    with_model(
        window,
        calls(RECTANGLE),
        Reply(text="Done."),
        calls(("modify_entity", {"id": "e1", "changes": {"width": 140}})),
        Reply(text="Made it 140 wide."),
    )
    ask(window, qtbot, "a 100 by 50 rectangle")
    window.proposal_card.accept_button.click()
    ask(window, qtbot, "make it 140 wide")
    window.proposal_card.accept_button.click()
    assert window.session.document.entities[E1].width == 140.0
    assert [h.label for h in window.session.history] == ["Create Rectangle", "Change Width"]


def test_errors_are_shown_and_nothing_is_proposed(window, qtbot) -> None:
    with_model(
        window,
        calls(("create_circle", {"center": {"x": 0, "y": 0}, "radius": -1})),
        Reply(text="I couldn't make a circle with a negative radius."),
    )
    ask(window, qtbot, "a circle of radius -1")
    assert window.agent.proposal is None
    assert "✗ create_circle: radius must be greater than 0" in window.assistant_log.lines()

    class Offline:
        name = "offline"

        def reply(self, system: object, conversation: object, tools: object) -> Reply:
            raise ModelError("no network")

    window.agent.set_assistant(Assistant(Offline()))
    ask(window, qtbot, "anything")
    assert window.assistant_log.lines()[-1] == "Couldn't get an answer from offline: no network"
    assert "Couldn't get an answer" in window.statusBar().currentMessage()


def test_the_prompt_waits_while_the_model_works(window, qtbot) -> None:
    release = threading.Event()

    def slow() -> Reply:
        release.wait(5)
        return Reply(text="Here.")

    with_model(window, slow)
    window.prompt_bar.input.setText("take your time")
    window.prompt_bar.input.returnPressed.emit()
    assert window.agent.busy
    assert not window.prompt_bar.input.isEnabled()
    window.agent.ask("another")  # a second request while busy is turned away
    assert "Still working" in window.statusBar().currentMessage()
    release.set()
    qtbot.waitUntil(lambda: not window.agent.busy)
    assert window.prompt_bar.input.isEnabled()


def test_opening_another_document_forgets_the_conversation_and_drops_a_late_answer(
    window, qtbot
) -> None:
    release = threading.Event()

    def late() -> Reply:
        release.wait(5)
        return calls(RECTANGLE)

    assistant = with_model(window, late, Reply(text="Done."))
    window.prompt_bar.input.setText("a rectangle")
    window.prompt_bar.input.returnPressed.emit()
    window.new_action.trigger()
    assert assistant.conversation == []
    release.set()
    qtbot.waitUntil(lambda: not window.agent.busy)
    assert window.agent.proposal is None
    assert dict(window.session.document.entities) == {}


def test_without_an_assistant_the_scripted_agent_answers_as_before(window, qtbot) -> None:
    assert window.agent.assistant is None
    assert window.prompt_bar.chip.text() == "Scripted agent"
    window.prompt_bar.input.setText("rectangle 120 x 50")
    window.prompt_bar.input.returnPressed.emit()
    assert window.proposal_card.title.text() == "Add Rectangle"


@pytest.mark.parametrize(
    ("name", "content", "is_error", "line"),
    [
        (
            "create_line",
            {"label": "Create Line", "created": ["e2"]},
            False,
            "→ create_line: Create Line (e2)",
        ),
        (
            "modify_entity",
            {"applied": True, "changed": "nothing"},
            False,
            "→ modify_entity: nothing changed",
        ),
        ("run_check", {"passed": False, "actual": 50.0}, False, "✗ run_check: 50"),
        (
            "solve_status",
            {"state": "under", "dof": 3},
            False,
            "→ solve_status: under, 3 degrees of freedom left",
        ),
        ("undo", {"undone": "Create Line"}, False, "→ undo: took back Create Line"),
        (
            "create_line",
            {"rejected": [{"message": "a line needs distinct endpoints"}]},
            True,
            "✗ create_line: a line needs distinct endpoints",
        ),
        ("nope", {"error": "no tool named 'nope'"}, True, "✗ nope: no tool named 'nope'"),
        ("inspect_document", {"entities": []}, False, "→ inspect_document"),
    ],
)
def test_each_step_reads_as_one_line(name: str, content: object, is_error: bool, line: str) -> None:
    outcome = ToolOutcome(
        call=ToolCall(id="c", name=name, arguments={}), content=content, is_error=is_error
    )  # type: ignore[arg-type]
    assert step_text(outcome) == line


def test_an_edit_made_while_the_model_works_makes_its_proposal_stale(window, qtbot) -> None:
    release = threading.Event()

    def slow() -> Reply:
        release.wait(5)
        return calls(RECTANGLE)

    with_model(window, slow, Reply(text="Done."))
    window.prompt_bar.input.setText("a rectangle")
    window.prompt_bar.input.returnPressed.emit()
    window.session.execute(CreateRectangle(corner=Point2(x=500, y=0), width=10, height=10))
    release.set()
    qtbot.waitUntil(lambda: not window.agent.busy)
    window.agent.accept()
    assert list(window.session.document.entities) == [E1]  # only the user's own rectangle
    assert window.session.document.entities[E1].corner == Point2(x=500.0, y=0.0)
    assert "changed since that proposal" in window.statusBar().currentMessage()


def test_every_metric_the_assistant_can_check_is_described() -> None:
    # The assistant can run any Metric; the proposal card and Checks panel name each one.
    from caliper.app.panels.checks import describe
    from caliper.contracts.document import Feature, Ref
    from caliper.contracts.queries import Expectation, Metric

    corner = Ref(entity=E1, feature=Feature.BOTTOM_LEFT)
    for metric in Metric:
        refs = (corner,) if metric.value.startswith("position") else (corner, corner)
        line = describe(
            Expectation(metric=metric, expected=20.0, tolerance=0.001, refs=refs, ids=(E1,))
        )
        assert line.endswith("= 20 ± 0.001")
    assert describe(
        Expectation(metric=Metric.POSITION_X, expected=20.0, tolerance=0.001, refs=(corner,))
    ).startswith("Position x of ")
