"""How the MCP server reaches the Caliper app: one JSON line each way over a local socket.

Claude Desktop starts `caliper-mcp` as a process of its own, but the user's document lives in
the Caliper app. The two meet at a Unix domain socket, `~/.caliper/mcp.sock` by default
(`CALIPER_MCP_SOCKET` overrides it), in a directory only the user can open, so only the user's
own processes can reach it. The path comes from the home directory, not the temp directory,
because Claude Desktop starts servers with only a few environment variables (HOME, PATH, ...).

A request names one of Caliper's tools (`caliper.ai.tools.TOOLS`) and its arguments; the
response is that tool's outcome, plus any note from the draft (`caliper.ai.draft`). Nothing
else travels: no credentials, no files, no code.
"""

import json
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from caliper.engine.io.canonical import JSON

SOCKET_ENV = "CALIPER_MCP_SOCKET"
MAX_MESSAGE = 4 * 1024 * 1024
"""Bytes in one request or response line. Tool calls and results are far smaller."""
TIMEOUT = 60.0
"""Seconds to wait for Caliper's answer to one call."""
MAX_SOCKET_PATH = 100
"""Bytes in a Unix socket path; macOS allows 104 including the terminator."""

NOT_RUNNING = (
    "Caliper isn't running, or its MCP bridge is off. Open Caliper (uv run python -m "
    "caliper.app), then try again."
)


@dataclass(frozen=True, slots=True)
class Request:
    client: str
    """Who is calling, e.g. "claude-ai", for the assistant log."""
    tool: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Response:
    content: JSON
    is_error: bool = False
    note: str | None = None


class BridgeError(RuntimeError):
    """Caliper couldn't be reached or answered unusably. The message says why, for display."""


def socket_path() -> Path:
    override = os.environ.get(SOCKET_ENV, "").strip()
    return Path(override).expanduser() if override else Path.home() / ".caliper" / "mcp.sock"


def supported() -> bool:
    """Whether this platform has Unix domain sockets (macOS and Linux do)."""
    return hasattr(socket, "AF_UNIX") and os.name == "posix"


def prepare_directory(path: Path) -> None:
    """Make the socket's directory, readable by the user only. Raises OSError."""
    if len(os.fsencode(path)) > MAX_SOCKET_PATH:
        raise OSError(f"socket path is too long for this system: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)


def in_use(path: Path) -> bool:
    """Whether something is answering at `path`, e.g. another Caliper window."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(str(path))
    except OSError:
        return False
    return True


# --- The wire ---------------------------------------------------------------------------


def encode_request(request: Request) -> bytes:
    return _line({"client": request.client, "tool": request.tool, "arguments": request.arguments})


def decode_request(line: bytes) -> Request:
    """Raises ValueError, saying what's wrong, for anything but a well-formed request."""
    data = _json(line)
    if not isinstance(data, dict):
        raise ValueError("a request must be a JSON object")
    client, tool, arguments = (
        data.get("client", "MCP client"),
        data.get("tool"),
        data.get("arguments", {}),
    )
    if not isinstance(client, str) or not isinstance(tool, str):
        raise ValueError("a request needs a tool name, and client must be a string")
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    return Request(client=client, tool=tool, arguments=arguments)


def encode_response(response: Response) -> bytes:
    return _line(
        {"content": response.content, "is_error": response.is_error, "note": response.note}
    )


def decode_response(line: bytes) -> Response:
    try:
        data = _json(line)
    except ValueError as e:
        raise BridgeError(f"Caliper answered unusably: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("is_error"), bool):
        raise BridgeError("Caliper answered unusably: not a response")
    note = data.get("note")
    content: JSON = data.get("content")
    return Response(
        content=content, is_error=data["is_error"], note=note if isinstance(note, str) else None
    )


def ask(request: Request, path: Path | None = None, *, timeout: float = TIMEOUT) -> Response:
    """Send one request to the Caliper app and wait for its response. Raises `BridgeError`."""
    if not supported():
        raise BridgeError("Caliper's MCP bridge needs Unix domain sockets (macOS or Linux).")
    path = socket_path() if path is None else path
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(encode_request(request))
            line = _read_line(s)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise BridgeError(NOT_RUNNING) from e
    except TimeoutError as e:
        raise BridgeError(f"Caliper didn't answer within {timeout:g} seconds.") from e
    except OSError as e:
        raise BridgeError(f"Couldn't reach Caliper: {e.strerror or e}") from e
    return decode_response(line)


def _line(data: Mapping[str, object]) -> bytes:
    # json.dumps escapes newlines inside strings, so the only newline ends the message.
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _json(line: bytes) -> object:
    if len(line) > MAX_MESSAGE:
        raise ValueError("message too long")
    try:
        return json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not JSON: {e}") from e


def _read_line(s: socket.socket) -> bytes:
    buffer = bytearray()
    while b"\n" not in buffer:
        chunk = s.recv(65536)
        if not chunk:
            raise BridgeError("Caliper closed the connection without answering.")
        buffer += chunk
        if len(buffer) > MAX_MESSAGE:
            raise BridgeError("Caliper's answer was too long.")
    return bytes(buffer.split(b"\n", 1)[0])
