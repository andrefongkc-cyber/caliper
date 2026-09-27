"""Opening files: File → Open."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from caliper.contracts.commands import CreateCircle
from caliper.contracts.document import Point2
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot


def drawing(path: Path, radius: float = 5) -> Path:
    bus = Bus()
    bus.execute(CreateCircle(center=Point2(x=0, y=0), radius=radius))
    snapshot.save(bus.document, path)
    return path


# --- File → Open -------------------------------------------------------------------------


def test_file_open_opens_the_chosen_file(window, tmp_path: Path, monkeypatch) -> None:
    path = drawing(tmp_path / "part.caliper")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(path), ""))
    window.open_action.trigger()  # the menu's way in: it once passed False as the path
    assert window.session.path == path
    assert len(window.session.document.entities) == 1


def test_cancelling_file_open_changes_nothing(window, monkeypatch) -> None:
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: ("", ""))
    window.open_action.trigger()
    assert window.session.path is None
