"""Run the shell: `uv run python -m caliper.app [file.caliper]`.

Claude Desktop can reach the window over MCP (docs/mcp.md) unless CALIPER_MCP=off.
"""

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from caliper.ai.bridge import socket_path
from caliper.app import theme
from caliper.app.main_window import MainWindow


def main(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("Caliper")
    app.setOrganizationName("Caliper")
    theme.apply(app)
    window = MainWindow()
    window.show()
    if os.environ.get("CALIPER_MCP", "").strip().lower() not in {"off", "0", "false", "no"}:
        window.serve_mcp(socket_path())
    if len(argv) > 1:
        window.load(Path(argv[1]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
