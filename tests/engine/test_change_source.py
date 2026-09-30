"""Who asked for a change travels with it (known issue C-4): `Change.source`."""

from caliper.contracts.commands import Change, ChangeReason, CreateCircle, ModifyEntity
from caliper.contracts.document import EntityId, Point2
from caliper.engine.commands.bus import Bus


def listening(bus: Bus) -> list[Change]:
    heard: list[Change] = []
    bus.subscribe(heard.append)
    return heard


def circle(x: float = 0.0) -> CreateCircle:
    return CreateCircle(center=Point2(x=x, y=0), radius=2)


def test_an_execute_says_who_asked_and_an_unnamed_one_says_nothing() -> None:
    bus = Bus()
    heard = listening(bus)
    bus.execute(circle(), source="bracket-script")
    bus.execute(circle(10))
    assert [(c.reason, c.source) for c in heard] == [
        (ChangeReason.EXECUTE, "bracket-script"),
        (ChangeReason.EXECUTE, None),
    ]


def test_undo_and_redo_say_whose_step_they_take_back() -> None:
    bus = Bus()
    bus.execute(circle(), source="Agent")
    bus.execute(ModifyEntity(id=EntityId("e1"), changes={"radius": 3.0}), source="You")
    heard = listening(bus)
    bus.undo()
    bus.undo()
    bus.redo()
    assert [(c.reason, c.label, c.source) for c in heard] == [
        (ChangeReason.UNDO, "Change Radius", "You"),
        (ChangeReason.UNDO, "Create Circle", "Agent"),
        (ChangeReason.REDO, "Create Circle", "Agent"),
    ]


def test_a_transaction_names_its_commands_and_its_commit() -> None:
    bus = Bus()
    heard = listening(bus)
    with bus.transaction("Add Holes", source="Agent"):
        bus.execute(circle())
        bus.execute(circle(10), source="You")  # its own name wins
        with bus.transaction("Inner", source="ignored"):  # the outermost's name wins
            bus.execute(circle(20))
    assert [(c.reason, c.source) for c in heard] == [
        (ChangeReason.EXECUTE, "Agent"),
        (ChangeReason.EXECUTE, "You"),
        (ChangeReason.EXECUTE, "Agent"),
        (ChangeReason.COMMIT, "Agent"),
    ]
    heard.clear()
    bus.undo()
    assert heard[0].source == "Agent"


def test_a_rolled_back_transaction_says_whose_it_was() -> None:
    bus = Bus()
    heard = listening(bus)
    with bus.transaction("Try", source="optimizer") as tx:
        bus.execute(circle())
        tx.rollback()
    assert heard[-1].reason is ChangeReason.ROLLBACK
    assert heard[-1].source == "optimizer"


def test_a_merged_run_keeps_its_source() -> None:
    bus = Bus()
    bus.execute(circle(), source="You")
    for radius in (3.0, 4.0, 5.0):
        bus.execute(
            ModifyEntity(id=EntityId("e1"), changes={"radius": radius}),
            merge_key="e1.radius",
            source="You",
        )
    heard = listening(bus)
    bus.undo()
    assert (heard[0].label, heard[0].source) == ("Change Radius", "You")
