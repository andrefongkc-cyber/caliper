"""Stand-ins for the AI tests: a model that replays replies written in advance, and a Caliper
app without Qt for the MCP bridge to reach."""

import shutil
import socketserver
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

from caliper.ai.bridge import Request, Response, decode_request, encode_response, prepare_directory
from caliper.ai.draft import Draft, Ended
from caliper.ai.model import Message, Reply, Stop, ToolCall, ToolSpec
from caliper.engine.commands.bus import Bus

type Script = Sequence[Reply | Callable[[Sequence[Message]], Reply]]


class ScriptedModel:
    """Answers with each scripted reply in turn. A callable sees the conversation so far,
    so a reply can depend on what the tools returned."""

    name = "scripted"

    def __init__(self, script: Script) -> None:
        self.script = list(script)
        self.requests: list[tuple[str, list[Message], list[ToolSpec]]] = []

    def reply(
        self, system: str, conversation: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Reply:
        self.requests.append((system, list(conversation), list(tools)))
        if not self.script:
            raise AssertionError("the model was asked more often than the test expected")
        step = self.script.pop(0)
        return step(conversation) if callable(step) else step

    @staticmethod
    def calls(*calls: tuple[str, dict[str, object]], text: str = "") -> Reply:
        """A reply calling tools: `calls(("create_rectangle", {...}), ...)`."""
        return Reply(
            text=text,
            calls=tuple(
                ToolCall(id=f"call{i}", name=name, arguments=arguments)
                for i, (name, arguments) in enumerate(calls)
            ),
            stop=Stop.TOOLS,
        )

    @staticmethod
    def answer(text: str) -> Reply:
        return Reply(text=text)


@pytest.fixture
def scripted() -> type[ScriptedModel]:
    return ScriptedModel


# --- A Caliper app without Qt, for the MCP bridge -----------------------------------------


class FakeCaliper:
    """Serves the bridge like the app does (`caliper.app.agent.mcp_host`), without Qt: each
    request runs in a `Draft` on its bus's document. `accept` is the user pressing Accept."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.bus = Bus()
        self.draft = Draft()
        self.requests: list[Request] = []
        self._lock = threading.Lock()
        fake = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                for line in self.rfile:
                    self.wfile.write(fake.answer(line.rstrip(b"\n")))

        prepare_directory(path)
        self._server = socketserver.UnixStreamServer(str(path), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def answer(self, line: bytes) -> bytes:
        with self._lock:
            try:
                request = decode_request(line)
            except ValueError as e:
                return encode_response(Response({"error": str(e)}, is_error=True))
            self.requests.append(request)
            answer = self.draft.call(self.bus.document, (), request.tool, request.arguments)
            outcome = answer.outcome
            return encode_response(Response(outcome.content, outcome.is_error, answer.note))

    def accept(self) -> None:
        with self._lock, self.bus.transaction(self.draft.label):
            for command in self.draft.commands:
                self.bus.execute(command)
            self.draft.end(Ended.ACCEPTED)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def socket_file() -> Iterator[Path]:
    """A socket path short enough for macOS (pytest's tmp_path can exceed its 104 bytes)."""
    directory = Path(tempfile.mkdtemp(prefix="cal", dir="/tmp"))
    yield directory / "mcp.sock"
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def caliper(socket_file: Path) -> Iterator[FakeCaliper]:
    fake = FakeCaliper(socket_file)
    yield fake
    fake.close()


@pytest.fixture
def make_caliper() -> Iterator[Callable[[], FakeCaliper]]:
    """Makes stand-ins on fresh sockets, for tests that need more than one."""
    made: list[FakeCaliper] = []

    def make() -> FakeCaliper:
        directory = Path(tempfile.mkdtemp(prefix="cal", dir="/tmp"))
        made.append(FakeCaliper(directory / "mcp.sock"))
        return made[-1]

    yield make
    for fake in made:
        fake.close()
        shutil.rmtree(fake.path.parent, ignore_errors=True)
