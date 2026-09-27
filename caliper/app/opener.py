"""Open a file in the Caliper window that's already running, rather than in a second window.

Double-clicking a .caliper file in Finder (through the launcher app,
`caliper.app.mac_launcher`) or running `python -m caliper.app file.caliper` starts a new
process. If a Caliper window is open, that process hands it the path over a local socket and
exits, and the file opens in the open window, after its usual "save changes?" prompt. That
window stays the one Claude Desktop is connected to.

The socket is `~/.caliper/open.sock` (`CALIPER_OPEN_SOCKET` overrides it), in the same
user-only directory as the MCP socket (`caliper.ai.bridge`), so only the user's own processes
can reach it. One absolute file path goes one way, "ok" comes back. macOS and Linux only.
"""

import os
import socket
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from caliper.ai.bridge import in_use, prepare_directory, supported

SOCKET_ENV = "CALIPER_OPEN_SOCKET"
TIMEOUT = 2.0
"""Seconds to wait for the open window to say it will open the file."""
MAX_PATH = 4096
OK = b"ok\n"


def socket_path() -> Path:
    override = os.environ.get(SOCKET_ENV, "").strip()
    return Path(override).expanduser() if override else Path.home() / ".caliper" / "open.sock"


def hand_off(path: Path, at: Path, timeout: float = TIMEOUT) -> bool:
    """Ask the Caliper window listening at `at` to open `path`. True if it will; False if no
    window is listening (or this platform has no Unix sockets), so the caller opens it."""
    line = os.fsencode(path.expanduser().absolute())
    if not supported() or b"\n" in line or len(line) > MAX_PATH:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(at))
            s.sendall(line + b"\n")
            reply = b""
            while not reply.endswith(b"\n") and len(reply) < len(OK):
                chunk = s.recv(len(OK))
                if not chunk:
                    break
                reply += chunk
    except OSError:
        return False
    return reply == OK


class OpenServer(QObject):
    requested = Signal(object)
    """A `Path` another Caliper process was asked to open."""

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._connected)

    def start(self) -> bool:
        """Listen for other Caliper processes. False if this platform can't, or another
        Caliper window already is (it gets the files, as before)."""
        if not supported():
            return False
        try:
            prepare_directory(self.path)
        except OSError:
            return False
        if in_use(self.path):  # checked first: listening would take the path from it
            return False
        QLocalServer.removeServer(str(self.path))  # left behind by a Caliper that crashed
        return self._server.listen(str(self.path))

    @property
    def listening(self) -> bool:
        return self._server.isListening()

    def close(self) -> None:
        self._server.close()

    def _connected(self) -> None:
        while (connection := self._server.nextPendingConnection()) is not None:
            buffer = bytearray()
            connection.readyRead.connect(partial(self._read, connection, buffer))
            connection.disconnected.connect(connection.deleteLater)

    def _read(self, connection: QLocalSocket, buffer: bytearray) -> None:
        buffer += connection.readAll().data()
        if b"\n" not in buffer:
            if len(buffer) > MAX_PATH:
                connection.disconnectFromServer()
            return
        line = bytes(buffer).partition(b"\n")[0]
        connection.write(OK)
        connection.flush()
        connection.disconnectFromServer()
        # After answering: opening may ask "save changes?", and the other process shouldn't
        # time out waiting for the user.
        QTimer.singleShot(0, partial(self.requested.emit, Path(os.fsdecode(line))))
