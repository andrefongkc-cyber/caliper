"""Run the shell: `uv run python -m caliper.app [file.caliper]`."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from caliper.app import theme
from caliper.app.main_window import MainWindow


def main(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("Caliper")
    app.setOrganizationName("Caliper")
    theme.apply(app)
    window = MainWindow()
    window.show()
    if len(argv) > 1:
        window.load(Path(argv[1]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
