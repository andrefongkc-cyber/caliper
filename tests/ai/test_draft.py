"""The draft: an MCP client's changes collect for review, and it hears what became of them."""

from caliper.ai.draft import Draft, Ended
from caliper.contracts.commands import CreateCircle, CreateRectangle
from caliper.contracts.document import EntityId, Point2
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot

RECTANGLE = {"corner": {"x": 0, "y": 0}, "width": 100, "height": 50}
CIRCLE = {"center": {"x": 10, "y": 10}, "radius": 5}
WIDTH_CHECK = {"metric": "bbox_width", "expected": 100, "tolerance": 0.001, "ids": ["e1"]}


def accept(bus: Bus, draft: Draft) -> None:
    """What the shell does on Accept: the draft's commands, as one undo step."""
    with bus.transaction(draft.label):
        for command in draft.commands:
            bus.execute(command)
    draft.end(Ended.ACCEPTED)


def test_a_change_starts_a_draft_and_leaves_the_document_alone() -> None:
    document, draft = Bus().document, Draft()
    answer = draft.call(document, (), "create_rectangle", RECTANGLE)
    assert not answer.outcome.is_error
    assert answer.changed
    assert answer.note is None
    assert draft.base is document
    assert dict(document.entities) == {}
    assert draft.commands == (
        CreateRectangle(corner=Point2(x=0.0, y=0.0), width=100.0, height=50.0, id=EntityId("e1")),
    )
    assert draft.label == "Create Rectangle"


def test_looking_opens_no_draft_and_sees_the_draft_once_there_is_one() -> None:
    document, draft = Bus().document, Draft()
    answer = draft.call(document, (), "inspect_document", {})
    assert not answer.changed
    assert draft.workspace is None
    draft.call(document, (), "create_rectangle", RECTANGLE)
    seen = draft.call(document, (), "inspect_entities", {"ids": ["e1"]})
    assert isinstance(seen.outcome.content, dict)
    assert seen.outcome.content["e1"]["width"] == 100.0
    assert not seen.changed


def test_a_rejected_change_opens_no_draft() -> None:
    document, draft = Bus().document, Draft()
    answer = draft.call(document, (), "create_rectangle", {**RECTANGLE, "width": -1})
    assert answer.outcome.is_error
    assert not answer.changed
    assert answer.outcome.content == {
        "rejected": [
            {
                "code": "value.not_positive",
                "field": "width",
                "ids": [],
                "message": "width must be greater than 0",
            }
        ]
    }
    assert draft.workspace is None


def test_changes_collect_until_the_user_decides() -> None:
    document, draft = Bus().document, Draft()
    draft.call(document, (), "create_rectangle", RECTANGLE)
    answer = draft.call(document, (), "create_circle", CIRCLE)
    assert answer.changed
    assert [type(c) for c in draft.commands] == [CreateRectangle, CreateCircle]
    assert draft.label == "Assistant Changes"
    assert draft.base is document


def test_checks_join_the_draft() -> None:
    document, draft = Bus().document, Draft()
    draft.call(document, (), "create_rectangle", RECTANGLE)
    answer = draft.call(document, (), "run_check", WIDTH_CHECK)
    assert answer.changed
    assert answer.outcome.content == {"passed": True, "actual": 100.0, "error": None}
    assert len(draft.checks) == 1
    assert not draft.call(document, (), "run_check", WIDTH_CHECK).changed  # already there


def test_undoing_everything_leaves_nothing_pending() -> None:
    document, draft = Bus().document, Draft()
    draft.call(document, (), "create_rectangle", RECTANGLE)
    answer = draft.call(document, (), "undo", {})
    assert answer.changed
    assert not answer.outcome.is_error
    assert draft.workspace is None
    nothing = draft.call(document, (), "undo", {})
    assert nothing.outcome.is_error
    assert not nothing.changed


def test_the_next_call_after_accepting_says_so_once_and_starts_fresh() -> None:
    bus, draft = Bus(), Draft()
    draft.call(bus.document, (), "create_rectangle", RECTANGLE)
    accepted_from = bus.document
    accept(bus, draft)
    assert draft.workspace is None
    first = draft.call(bus.document, (), "inspect_document", {})
    assert first.note == Ended.ACCEPTED.value
    assert draft.call(bus.document, (), "inspect_document", {}).note is None
    # The accepted change is one ordinary undo step, and redo brings it back.
    after = bus.document
    bus.undo()
    assert bus.document == accepted_from
    bus.redo()
    assert bus.document == after


def test_a_change_made_underneath_the_draft_drops_it() -> None:
    bus, draft = Bus(), Draft()
    draft.call(bus.document, (), "create_rectangle", RECTANGLE)
    bus.execute(CreateCircle(center=Point2(x=50, y=50), radius=3))  # the user, meanwhile
    answer = draft.call(bus.document, (), "create_rectangle", RECTANGLE)
    assert answer.note == Ended.CHANGED.value
    assert draft.base is bus.document  # a fresh draft on the sketch as it is now
    assert len(draft.commands) == 1
    assert answer.outcome.content["created"] == ["e2"]  # type: ignore[index, call-overload]


def test_the_latest_reason_wins_and_nothing_pending_means_nothing_to_say() -> None:
    document, draft = Bus().document, Draft()
    draft.end(Ended.CLOSED)
    assert draft.call(document, (), "solve_status", {}).note is None
    draft.call(document, (), "create_rectangle", RECTANGLE)
    draft.end(Ended.CLOSED)
    draft.end(Ended.OPENED)
    assert draft.call(document, (), "solve_status", {}).note == Ended.OPENED.value


def test_the_same_calls_give_the_same_results_and_replay_to_the_same_bytes() -> None:
    calls = [
        ("create_rectangle", RECTANGLE),
        ("create_circle", CIRCLE),
        ("run_check", WIDTH_CHECK),
        (
            "measure_distance",
            {
                "a": {"entity": "e1", "feature": "corner"},
                "b": {"entity": "e2", "feature": "center"},
            },
        ),
    ]

    def run() -> tuple[list[object], str]:
        bus, draft = Bus(), Draft()
        contents = [draft.call(bus.document, (), n, a).outcome.content for n, a in calls]
        accept(bus, draft)
        return contents, snapshot.dumps(bus.document)

    first, second = run(), run()
    assert first == second
    # Accepting runs exactly the draft's resolved commands: replaying them from the empty
    # document gives the same file, and the file reads back to the same document.
    bus, draft = Bus(), Draft()
    for name, arguments in calls:
        draft.call(bus.document, (), name, arguments)
    replay = Bus()
    for command in draft.commands:
        replay.execute(command)
    assert snapshot.dumps(replay.document) == first[1]
    assert snapshot.dumps(snapshot.loads(first[1])) == first[1]
