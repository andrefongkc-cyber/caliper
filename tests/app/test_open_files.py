"""Opening files: File → Open, Open Recent, and files handed over by another Caliper process
(Finder's double-click, through the launcher app). Settings are per test (conftest)."""

import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QFileDialog

from caliper.app import __main__ as app_main
from caliper.app import mac_launcher
from caliper.app.main_window import RECENT_FILES
from caliper.app.opener import SOCKET_ENV, hand_off
from caliper.contracts.commands import CreateCircle
from caliper.contracts.document import Point2
from caliper.engine.commands.bus import Bus
from caliper.engine.io import snapshot


def drawing(path: Path, radius: float = 5) -> Path:
    bus = Bus()
    bus.execute(CreateCircle(center=Point2(x=0, y=0), radius=radius))
    snapshot.save(bus.document, path)
    return path


def entries(window) -> list[str]:
    window._fill_recent()
    return [a.text() for a in window.recent_menu.actions() if not a.isSeparator()]


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


# --- Open Recent -------------------------------------------------------------------------


def test_opened_and_saved_files_are_listed_newest_first(window, tmp_path: Path) -> None:
    first = drawing(tmp_path / "first.caliper")
    assert entries(window) == ["Clear Menu"]
    assert not window.recent_menu.actions()[-1].isEnabled()
    assert window.open_document(first)
    saved = tmp_path / "second.caliper"
    assert window._save_to(saved)
    assert window.recent_files() == [saved, first]
    assert entries(window) == ["second.caliper", "first.caliper", "Clear Menu"]
    assert window.open_document(first)  # again: back to the top, not listed twice
    assert window.recent_files() == [first, saved]


def test_open_recent_keeps_the_last_ten(window, tmp_path: Path) -> None:
    paths = [drawing(tmp_path / f"part{i}.caliper") for i in range(RECENT_FILES + 2)]
    for path in paths:
        assert window.open_document(path)
    assert window.recent_files() == paths[::-1][:RECENT_FILES]


def test_choosing_a_recent_file_opens_it(window, tmp_path: Path) -> None:
    path = drawing(tmp_path / "bearing.caliper", radius=8)
    assert window.open_document(path)
    window.session.new()
    window._fill_recent()
    window.recent_menu.actions()[0].trigger()
    assert window.session.path == path
    assert window.recent_menu.actions()[0].toolTip() == str(path)


def test_files_with_the_same_name_show_their_folder(window, tmp_path: Path) -> None:
    for folder in ("001-ball-bearing", "002-plate"):
        (tmp_path / folder).mkdir()
        assert window.open_document(drawing(tmp_path / folder / "test.caliper"))
    assert entries(window) == [
        "test.caliper — 002-plate",
        "test.caliper — 001-ball-bearing",
        "Clear Menu",
    ]


def test_a_recent_file_that_has_gone_is_dropped(window, tmp_path: Path) -> None:
    gone = drawing(tmp_path / "gone.caliper")
    kept = drawing(tmp_path / "kept.caliper")
    assert window.open_document(gone)
    assert window.open_document(kept)
    gone.unlink()
    assert not window.open_recent(gone)
    assert window.recent_files() == [kept]
    assert window.statusBar().currentMessage() == f"gone.caliper isn't in {tmp_path} any more"


def test_clear_menu_empties_it(window, tmp_path: Path) -> None:
    assert window.open_document(drawing(tmp_path / "part.caliper"))
    window._fill_recent()
    window.recent_menu.actions()[-1].trigger()
    assert window.recent_files() == []
    assert entries(window) == ["Clear Menu"]


def test_every_window_shares_the_list(window, new_window, tmp_path: Path) -> None:
    path = drawing(tmp_path / "part.caliper")
    assert window.open_document(path)
    assert entries(new_window()) == ["part.caliper", "Clear Menu"]


# --- Handed over by another process (Finder) ---------------------------------------------


@pytest.fixture
def socket_file() -> Iterator[Path]:
    directory = Path(tempfile.mkdtemp(prefix="cal", dir="/tmp"))  # short: socket paths are
    yield directory / "open.sock"
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def serving(window, socket_file: Path):
    assert window.serve_opens(socket_file)
    yield window
    window.opener.close()


def in_background(qtbot, work: Callable[[], Any]) -> Any:
    box: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: box.update(value=work()), daemon=True)
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=10000)
    return box["value"]


def test_with_no_caliper_open_nothing_takes_the_file(socket_file: Path, tmp_path) -> None:
    assert not hand_off(tmp_path / "part.caliper", socket_file)


def test_a_file_handed_over_opens_in_the_open_window(
    serving, qtbot, socket_file: Path, tmp_path: Path
) -> None:
    path = drawing(tmp_path / "part.caliper")
    assert in_background(qtbot, lambda: hand_off(path, socket_file))
    qtbot.waitUntil(lambda: serving.session.path == path)
    assert serving.recent_files() == [path]


def test_the_handover_is_answered_before_any_save_prompt(
    serving, qtbot, socket_file: Path, tmp_path: Path, monkeypatch
) -> None:
    opened: list[Path] = []

    def slow_open(path: Path) -> bool:  # a "save changes?" dialog the user takes a while over
        time.sleep(0.6)
        opened.append(path)
        return True

    monkeypatch.setattr(serving, "open_document", slow_open)
    path = tmp_path / "part.caliper"
    assert in_background(qtbot, lambda: hand_off(path, socket_file, timeout=0.3))
    qtbot.waitUntil(lambda: opened == [path])


def test_a_second_window_leaves_the_files_to_the_first(serving, new_window, socket_file) -> None:
    assert not new_window().serve_opens(socket_file)
    assert serving.opener.listening


def test_starting_caliper_with_a_file_hands_it_to_the_open_window(
    serving, qtbot, socket_file: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(SOCKET_ENV, str(socket_file))
    path = drawing(tmp_path / "part.caliper")
    # `python -m caliper.app part.caliper`, as the launcher runs it: it returns before
    # making a window of its own.
    assert in_background(qtbot, lambda: app_main.main(["caliper", str(path)])) == 0
    qtbot.waitUntil(lambda: serving.session.path == path)


# --- The Finder launcher -----------------------------------------------------------------


def test_the_launcher_quotes_paths_for_the_shell_and_applescript() -> None:
    script = mac_launcher.applescript(Path("/Users/a b/.venv/bin/python"), Path('/Users/a b/"x"'))
    assert "on open dropped" in script
    assert "quoted form of POSIX path of (item 1 of dropped)" in script
    assert "cd '/Users/a b/\\\"x\\\"' && '/Users/a b/.venv/bin/python' -m caliper.app" in script


def test_the_launcher_owns_the_caliper_file_type() -> None:
    types = mac_launcher.document_types()
    (declared,) = types["UTExportedTypeDeclarations"]
    assert declared["UTTypeTagSpecification"] == {"public.filename-extension": ["caliper"]}
    (document,) = types["CFBundleDocumentTypes"]
    assert document["LSItemContentTypes"] == [declared["UTTypeIdentifier"]]
    assert document["LSHandlerRank"] == "Owner"


mac_only = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("osacompile") is None, reason="macOS only"
)


@mac_only
def test_the_launcher_builds_a_signed_app_and_rebuilds_its_own(tmp_path: Path) -> None:
    target = tmp_path / "Caliper.app"
    mac_launcher.build(target, Path(sys.executable), mac_launcher.REPO, register=False)
    with (target / "Contents" / "Info.plist").open("rb") as f:
        info = plistlib.load(f)
    assert info["CFBundleIdentifier"] == mac_launcher.BUNDLE_ID
    assert info["CFBundleDocumentTypes"][0]["CFBundleTypeRole"] == "Editor"
    subprocess.run(["codesign", "--verify", str(target)], check=True)
    mac_launcher.build(target, Path(sys.executable), mac_launcher.REPO, register=False)


@mac_only
def test_the_launcher_wont_replace_another_app(tmp_path: Path) -> None:
    other = tmp_path / "Caliper.app"
    (other / "Contents").mkdir(parents=True)
    with (other / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.example.other"}, f)
    with pytest.raises(FileExistsError):
        mac_launcher.build(other, Path(sys.executable), mac_launcher.REPO, register=False)
    assert (other / "Contents" / "Info.plist").exists()
