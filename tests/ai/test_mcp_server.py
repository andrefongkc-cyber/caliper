"""Caliper's MCP server, driven by the MCP SDK's own client: in-process, and as the separate
process Claude Desktop starts. Calls cross a real socket to a Caliper stand-in without Qt
(`FakeCaliper`) that runs them the way the app does; tests/app/test_mcp.py uses the real app.
"""

import json
import sys
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult, TextContent

from caliper.ai.bridge import NOT_RUNNING, SOCKET_ENV, BridgeError, Request, Response, ask
from caliper.ai.draft import Ended
from caliper.ai.mcp_server import INSTRUCTIONS, READ_ONLY, build
from caliper.ai.tools import CONVENTIONS, QUERY_TOOLS, TOOLS
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot
from caliper.engine.io.codec import COMMAND_KINDS

RECTANGLE = {"corner": {"x": 0, "y": 0}, "width": 100, "height": 50}
CIRCLE = {"center": {"x": 10, "y": 10}, "radius": 5}
WIDTH_CHECK = {"metric": "bbox_width", "expected": 100, "tolerance": 0.001, "ids": ["e1"]}


def run(body: Callable[[Client], Awaitable[None]], forward: object) -> None:
    async def main() -> None:
        async with Client(build(forward)) as client:  # type: ignore[arg-type]
            await body(client)

    anyio.run(main)


def texts(result: CallToolResult) -> list[str]:
    return [block.text for block in result.content if isinstance(block, TextContent)]


def data(result: CallToolResult) -> object:
    """The tool's own result: the last block, after any note."""
    return json.loads(texts(result)[-1])


# --- What it offers -----------------------------------------------------------------------


def test_it_offers_exactly_the_tools_the_in_app_assistant_uses() -> None:
    async def body(client: Client) -> None:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert list(listed) == [spec.name for spec in TOOLS]
        for spec in TOOLS:
            assert listed[spec.name].description == spec.description
            assert listed[spec.name].input_schema == spec.input_schema
            annotations = listed[spec.name].annotations
            assert annotations is not None
            assert annotations.open_world_hint is False
            assert annotations.read_only_hint is (spec.name in READ_ONLY)
        assert client.server_info is not None
        assert client.server_info.name == "caliper"
        assert client.instructions == INSTRUCTIONS
        assert CONVENTIONS in INSTRUCTIONS

    run(body, lambda request: Response(None))


def test_it_exposes_caliper_and_nothing_else() -> None:
    names = {spec.name for spec in TOOLS}
    assert names == set(COMMAND_KINDS) | {spec.name for spec in QUERY_TOOLS}
    assert len(names) == 21
    for word in ("shell", "exec", "file", "python", "eval", "http", "fetch", "system", "terminal"):
        assert not [name for name in names if word in name], word
    assert names >= READ_ONLY
    assert not READ_ONLY & COMMAND_KINDS.keys()


def test_an_unknown_tool_is_refused_without_reaching_caliper(caliper, socket_file: Path) -> None:
    async def body(client: Client) -> None:
        result = await client.call_tool("run_shell", {"command": "ls"})
        assert result.is_error
        assert texts(result) == ["Caliper has no tool named 'run_shell'."]

    run(body, partial(ask, path=socket_file))
    assert caliper.requests == []


# --- Changes go through Caliper and wait for review -----------------------------------------


def test_a_change_runs_through_caliper_and_waits_for_the_user(caliper, socket_file: Path) -> None:
    async def body(client: Client) -> None:
        result = await client.call_tool("create_rectangle", RECTANGLE)
        assert not result.is_error
        assert len(result.content) == 1
        assert data(result)["created"] == ["e1"]  # type: ignore[index]
        check = await client.call_tool("run_check", WIDTH_CHECK)
        assert data(check) == {"passed": True, "actual": 100.0, "error": None}

    run(body, partial(ask, path=socket_file))
    assert dict(caliper.bus.document.entities) == {}  # untouched until accepted
    assert len(caliper.draft.commands) == 1
    assert len(caliper.draft.checks) == 1
    assert caliper.requests[0] == Request(
        client="mcp", tool="create_rectangle", arguments=RECTANGLE
    )


@pytest.mark.parametrize(
    ("tool", "arguments", "error"),
    [
        (
            "create_rectangle",
            {**RECTANGLE, "width": -1},
            {
                "rejected": [
                    {
                        "code": "value.not_positive",
                        "field": "width",
                        "ids": [],
                        "message": "width must be greater than 0",
                    }
                ]
            },
        ),
        ("delete_entities", {"ids": ["e99"]}, "entity.not_found"),
        ("create_circle", {"center": {"x": 0}, "radius": 5}, "center"),
        ("create_line", {"start": "origin", "end": {"x": 1, "y": 1}}, "start"),
    ],
)
def test_invalid_commands_are_rejected_by_caliper(
    caliper, socket_file: Path, tool: str, arguments: dict[str, object], error: object
) -> None:
    async def body(client: Client) -> None:
        result = await client.call_tool(tool, arguments)
        assert result.is_error
        if isinstance(error, str):
            assert error in texts(result)[-1]
        else:
            assert data(result) == error

    run(body, partial(ask, path=socket_file))
    assert caliper.draft.workspace is None
    assert dict(caliper.bus.document.entities) == {}


def test_accepted_changes_undo_and_redo_and_the_client_hears_of_it(
    caliper, socket_file: Path
) -> None:
    async def before(client: Client) -> None:
        await client.call_tool("create_rectangle", RECTANGLE)

    async def after(client: Client) -> None:
        result = await client.call_tool("inspect_entities", {"ids": ["e1"]})
        assert texts(result)[0] == f"Note: {Ended.ACCEPTED.value}"
        assert data(result)["e1"]["width"] == 100.0  # type: ignore[index]
        again = await client.call_tool("solve_status", {})
        assert len(again.content) == 1  # said once

    run(before, partial(ask, path=socket_file))
    empty = caliper.bus.document
    caliper.accept()
    accepted = caliper.bus.document
    assert "e1" in accepted.entities
    run(after, partial(ask, path=socket_file))
    caliper.bus.undo()
    assert caliper.bus.document == empty
    caliper.bus.redo()
    assert caliper.bus.document == accepted


def test_the_same_requests_give_the_same_results_and_the_same_file(make_caliper) -> None:
    calls = [
        ("create_rectangle", RECTANGLE),
        ("create_circle", CIRCLE),
        ("run_check", WIDTH_CHECK),
        ("inspect_document", {}),
    ]

    def session() -> tuple[list[list[str]], str, tuple[object, ...]]:
        caliper = make_caliper()
        results: list[list[str]] = []

        async def body(client: Client) -> None:
            for name, arguments in calls:
                results.append(texts(await client.call_tool(name, arguments)))

        run(body, partial(ask, path=caliper.path))
        commands = caliper.draft.commands
        caliper.accept()
        return results, snapshot.dumps(caliper.bus.document), commands

    first, second = session(), session()
    assert first == second
    # What was accepted, replayed from the empty document, writes the same bytes.
    replay = Bus()
    for command in first[2]:
        replay.execute(command)  # type: ignore[arg-type]
    assert snapshot.dumps(replay.document) == first[1]


# --- When Caliper can't be reached, and credentials ---------------------------------------


def test_without_caliper_running_it_still_lists_tools_and_says_what_to_do(
    socket_file: Path,
) -> None:
    async def body(client: Client) -> None:
        assert len((await client.list_tools()).tools) == 21
        result = await client.call_tool("inspect_document", {})
        assert result.is_error
        assert texts(result) == [NOT_RUNNING]

    run(body, partial(ask, path=socket_file))


def test_a_bridge_failure_is_reported_as_a_tool_error() -> None:
    def broken(request: Request) -> Response:
        raise BridgeError("Caliper didn't answer within 60 seconds.")

    async def body(client: Client) -> None:
        result = await client.call_tool("solve_status", {})
        assert result.is_error
        assert texts(result) == ["Caliper didn't answer within 60 seconds."]

    run(body, broken)


def test_it_needs_no_api_key_and_never_repeats_one(
    caliper, socket_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-ant-test-0000-not-a-real-key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    seen: list[str] = []

    async def body(client: Client) -> None:
        seen.append(str(client.instructions))
        seen.append(repr((await client.list_tools()).tools))
        for name, arguments in [
            ("create_rectangle", RECTANGLE),
            ("create_rectangle", {**RECTANGLE, "width": "wide"}),
            ("no_such_tool", {}),
            ("inspect_document", {}),
        ]:
            seen.append(repr(await client.call_tool(name, arguments)))

    run(body, partial(ask, path=socket_file))
    assert not [text for text in seen if secret in text]
    assert not [r for r in caliper.requests if secret in repr(r)]


def test_the_server_starts_as_its_own_process_like_claude_desktop_starts_it(
    caliper, socket_file: Path
) -> None:
    # Claude Desktop passes only a few variables (HOME, PATH, ...): no API key, no TMPDIR.
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "caliper.ai.mcp_server"],
        env={SOCKET_ENV: str(socket_file)},
    )

    async def main() -> None:
        async with Client(server) as client:
            assert client.server_info is not None
            assert client.server_info.name == "caliper"
            assert len((await client.list_tools()).tools) == 21
            result = await client.call_tool("create_rectangle", RECTANGLE)
            assert not result.is_error
            assert data(result)["label"] == "Create Rectangle"  # type: ignore[index]

    anyio.run(main)
    assert caliper.draft.label == "Create Rectangle"
    assert dict(caliper.bus.document.entities) == {}
