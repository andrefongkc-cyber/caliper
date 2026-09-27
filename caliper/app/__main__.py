"""Run the shell: `uv run python -m caliper.app [file.caliper]`.

Claude Desktop can reach the window over MCP (docs/mcp.md) unless CALIPER_MCP=off. Given a
file while a Caliper window is already open, it opens the file in that window and exits
(`caliper.app.opener`); that's how double-clicking in Finder works (`caliper.app.mac_launcher`).
"""

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from caliper.ai.bridge import socket_path
from caliper.app import theme
from caliper.app.main_window import MainWindow
from caliper.app.opener import hand_off
from caliper.app.opener import socket_path as open_socket_path


def main(argv: list[str]) -> int:
    if len(argv) > 1 and hand_off(Path(argv[1]), open_socket_path()):
        return 0  # the Caliper window that's already open opens it
    app = QApplication(argv)
    app.setApplicationName("Caliper")
    app.setOrganizationName("Caliper")
    theme.apply(app)
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()  # started from Finder or a shell, it still comes to the front
    window.serve_opens(open_socket_path())
    if os.environ.get("CALIPER_MCP", "").strip().lower() not in {"off", "0", "false", "no"}:
        window.serve_mcp(socket_path())
    if len(argv) > 1:
        window.load(Path(argv[1]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
