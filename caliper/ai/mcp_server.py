"""Caliper as an MCP server: how Claude Desktop, or any MCP client, works in Caliper.

    uv run caliper-mcp        # speaks MCP on stdin/stdout; Claude Desktop starts it

It lists Caliper's tools, the same `TOOLS` the in-app assistant uses plus `PROGRESS` for the
Timing panel's time left, and forwards each call to the running Caliper app over
`caliper.ai.bridge`. There the call runs in a draft
(`caliper.ai.draft`) on a copy of the document, through Caliper's own command bus, and the
user accepts or rejects the result in Caliper. The client is the model, so this server needs
no API key and reads none. It exposes Caliper's tools and nothing else: no shell, files, code,
or network. The MCP SDK (the `mcp` extra) is imported only when the server runs, so importing
`caliper.ai` never needs it. Setup: docs/mcp.md.
"""

import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from caliper.ai.bridge import BridgeError, Request, Response, ask
from caliper.ai.model import ToolSpec
from caliper.ai.tools import CONVENTIONS, TOOLS

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

NAME = "caliper"

INSTRUCTIONS = f"""\
Caliper is a parametric 2D sketcher running on the user's computer. These tools work on the \
sketch open in the Caliper window: one tool per Caliper command, plus tools to inspect the \
sketch, measure, and check. Caliper validates every command; a rejected command changes \
nothing and says why, so read the error, fix the arguments, and try again.

{CONVENTIONS}

Your changes are not applied straight away. They collect as a proposal on the Caliper canvas, \
and the user accepts or rejects them there, as one step; you can't accept them. Work in small \
steps: act, look at the result, then continue. When a request states a size, position, or \
distance, confirm it with run_check. A tool result may begin with a note saying the user \
accepted, rejected, or overtook your earlier changes: read it before continuing. For a task \
of more than about ten calls, call report_progress before you start, with the number of calls \
you plan, so Caliper can show the user the time left. When you are done, call it with 0, then \
tell the user to review the proposal in Caliper.\
"""

PROGRESS = ToolSpec(
    name="report_progress",
    description=(
        "Say how many more Caliper tool calls you expect this task to take, so Caliper can show "
        "the user the time left. Call it once before you start a task of more than about ten "
        "calls, again only if your plan changes a lot, and with 0 when you are done, just "
        "before your final message. Give repeats and checks too if you know them: mirrors and "
        "patterns take longer than other calls, checks less. It changes nothing in the sketch."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "calls_left": {"type": "integer", "minimum": 0, "description": "All of them."},
            "repeats": {
                "type": "integer",
                "minimum": 0,
                "description": "How many of them are mirror or pattern calls.",
            },
            "checks": {
                "type": "integer",
                "minimum": 0,
                "description": "How many of them are run_check calls.",
            },
        },
        "required": ["calls_left"],
        "additionalProperties": False,
    },
)
"""Over MCP only: the Timing panel is where the time left shows (`caliper.app.agent.timing`)."""

READ_ONLY = frozenset(
    {
        "inspect_document",
        "inspect_entities",
        "measure_distance",
        "solve_status",
        "applicable_constraints",
        PROGRESS.name,
    }
)
"""Tools that never change the sketch. Not run_check or remove_check: a check is stored in the
sketch (C-1), so they add to the proposal like any change."""

MCP_TOOLS = (*TOOLS, PROGRESS)

Forward = Callable[[Request], Response]


def build(forward: Forward = ask) -> "Server[Any]":
    """The MCP server. `forward` carries a call to Caliper; tests pass their own."""
    import anyio.to_thread
    import mcp.types as types
    from mcp.server.context import ServerRequestContext
    from mcp.server.lowlevel import Server

    tools = [
        types.Tool(
            name=spec.name,
            description=spec.description,
            input_schema=dict(spec.input_schema),
            annotations=types.ToolAnnotations(
                read_only_hint=spec.name in READ_ONLY, open_world_hint=False
            ),
        )
        for spec in MCP_TOOLS
    ]
    names = frozenset(spec.name for spec in MCP_TOOLS)

    async def list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        def text(value: str) -> types.TextContent:
            return types.TextContent(text=value)

        if params.name not in names:
            return types.CallToolResult(
                content=[text(f"Caliper has no tool named {params.name!r}.")], is_error=True
            )
        request = Request(
            client=_client_name(ctx), tool=params.name, arguments=params.arguments or {}
        )
        try:
            response = await anyio.to_thread.run_sync(forward, request)
        except BridgeError as e:
            return types.CallToolResult(content=[text(str(e))], is_error=True)
        content: list[types.ContentBlock] = [text(json.dumps(response.content, sort_keys=True))]
        if response.note is not None:
            # Its own block, ended by a blank line: some clients join blocks with nothing
            # between them, which glued the note to the result's JSON (AI-4).
            content.insert(0, text(f"Note: {response.note}\n\n"))
        return types.CallToolResult(content=content, is_error=response.is_error)

    return Server(
        NAME,
        version=_version(),
        title="Caliper",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def main() -> None:
    """`caliper-mcp`: serve MCP over stdin and stdout until the client disconnects."""
    try:
        import anyio
        import mcp.server.stdio
    except ImportError:
        sys.exit("caliper-mcp needs the MCP SDK: uv sync --extra mcp")

    async def serve() -> None:
        server = build()
        async with mcp.server.stdio.stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(serve)


def _client_name(ctx: Any) -> str:
    # The client's own name ("claude-ai" for Claude Desktop), when the protocol version has it.
    params = getattr(ctx.session, "client_params", None)
    info = getattr(params, "client_info", None)
    name = getattr(info, "name", None)
    return name if isinstance(name, str) and name else "MCP client"


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("caliper-cad")
    except PackageNotFoundError:
        return "0"


if __name__ == "__main__":
    main()
