"""The bridge from `caliper-mcp` to the Caliper app: a private socket and one JSON line each way."""

import stat
from pathlib import Path

import pytest

from caliper.ai.bridge import (
    NOT_RUNNING,
    SOCKET_ENV,
    BridgeError,
    Request,
    Response,
    ask,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
    in_use,
    prepare_directory,
    socket_path,
)

RECTANGLE = {"corner": {"x": 0, "y": 0}, "width": 100, "height": 50}


def test_the_socket_lives_in_the_home_directory_unless_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(SOCKET_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TMPDIR", raising=False)  # Claude Desktop doesn't pass it on
    assert socket_path() == tmp_path / ".caliper" / "mcp.sock"
    monkeypatch.setenv(SOCKET_ENV, "/tmp/elsewhere.sock")
    assert socket_path() == Path("/tmp/elsewhere.sock")


def test_requests_and_responses_survive_the_wire() -> None:
    request = Request(client="claude-ai", tool="create_rectangle", arguments=RECTANGLE)
    line = encode_request(request)
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert decode_request(line.rstrip(b"\n")) == request
    response = Response({"applied": True, "note": "a\nb"}, is_error=False, note="Accepted.")
    line = encode_response(response)
    assert line.count(b"\n") == 1  # newlines inside strings are escaped
    assert decode_response(line) == response


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        (b"not json", "not JSON"),
        (b"[1, 2]", "a request must be a JSON object"),
        (b'{"arguments": {}}', "needs a tool name"),
        (b'{"tool": "undo", "arguments": [1]}', "arguments must be an object"),
        (b'{"tool": "undo", "client": 3}', "client must be a string"),
        (b"\xff\xfe", "not JSON"),
        (b'"' + b"x" * (5 * 1024 * 1024) + b'"', "too long"),
    ],
)
def test_malformed_requests_are_refused_with_a_reason(line: bytes, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        decode_request(line)


def test_a_garbled_response_is_a_bridge_error() -> None:
    with pytest.raises(BridgeError, match="unusably"):
        decode_response(b"<html>")
    with pytest.raises(BridgeError, match="not a response"):
        decode_response(b'{"content": 1}')


def test_asking_when_caliper_is_not_running_says_so(socket_file: Path) -> None:
    with pytest.raises(BridgeError) as raised:
        ask(Request(client="t", tool="solve_status", arguments={}), socket_file)
    assert str(raised.value) == NOT_RUNNING
    assert not in_use(socket_file)


def test_a_call_reaches_caliper_and_its_answer_comes_back(caliper, socket_file: Path) -> None:
    assert in_use(socket_file)
    response = ask(Request(client="t", tool="create_rectangle", arguments=RECTANGLE), socket_file)
    assert not response.is_error
    assert response.note is None
    assert isinstance(response.content, dict)
    assert response.content["created"] == ["e1"]
    assert caliper.requests == [Request(client="t", tool="create_rectangle", arguments=RECTANGLE)]
    assert dict(caliper.bus.document.entities) == {}  # a draft until the user accepts


def test_the_socket_directory_is_private(socket_file: Path) -> None:
    path = socket_file.parent / "caliper" / "mcp.sock"
    prepare_directory(path)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    path.parent.chmod(0o755)  # loosened by hand: tightened again
    prepare_directory(path)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_socket_path_too_long_for_the_system_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="too long"):
        prepare_directory(tmp_path / ("x" * 120) / "mcp.sock")
