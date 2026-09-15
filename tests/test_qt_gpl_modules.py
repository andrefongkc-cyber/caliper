"""GPL-only Qt libraries ship inside pyside6-essentials but must never load (ADR 0006).

pyside6-essentials 6.11 bundles libraries from three Qt modules that Qt licenses under the
GPL v3 only (https://doc.qt.io/qt-6/licensing.html): Qt Lottie Animation, Qt Quick
Timeline, and Qt Qml Compiler. The shell is Qt Widgets and never needs them. The license
metadata check in test_licenses.py can't see this, because the package as a whole is
"LGPL-3.0-only OR GPL-...".

This opens the real main window in a fresh interpreter and lists the Qt libraries the
process actually loaded. macOS only: it reads loaded images through dyld. The macOS app CI
job runs it.
"""

import importlib.util
import json
import os
import subprocess
import sys

import pytest

GPL_ONLY_LIBRARIES = (
    "QtCanvasPainter",
    "QtCoap",
    "QtGraphs",
    "QtGrpc",
    "QtHttpServer",
    "QtLottie",
    "QtMqtt",
    "QtNetworkAuth",
    "QtQmlCompiler",
    "QtQuick3D",
    "QtQuickTimeline",
    "QtVirtualKeyboard",
    "QtWaylandCompositor",
)
"""Library name prefixes for the GPL-v3-only modules on Qt's licensing page."""

PROBE = """
import ctypes, json, os, sys
from PySide6.QtWidgets import QApplication
from caliper.app import theme
from caliper.app.main_window import MainWindow
from caliper.app.session import DocumentSession
from caliper.engine.commands.bus import Bus

app = QApplication(sys.argv)
theme.apply(app)
window = MainWindow(DocumentSession(Bus()))
window.show()
app.processEvents()
window.canvas.grab()

dyld = ctypes.CDLL(None)
dyld._dyld_image_count.restype = ctypes.c_uint32
dyld._dyld_get_image_name.restype = ctypes.c_char_p
dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
paths = (dyld._dyld_get_image_name(i).decode() for i in range(dyld._dyld_image_count()))
print(json.dumps(sorted({os.path.basename(p) for p in paths if "PySide6" in p})))
"""


@pytest.mark.skipif(sys.platform != "darwin", reason="reads loaded libraries through dyld")
@pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="the app extra isn't installed"
)
def test_the_shell_never_loads_gpl_only_qt_libraries() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    loaded: list[str] = json.loads(result.stdout.strip().splitlines()[-1])
    assert "QtWidgets" in loaded, f"probe didn't see Qt load at all: {loaded}"
    violations = [name for name in loaded if name.startswith(GPL_ONLY_LIBRARIES)]
    assert not violations, f"the shell loaded GPL-only Qt libraries: {violations}"
