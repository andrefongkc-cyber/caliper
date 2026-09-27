"""Caliper.app for Finder: double-click a .caliper file and it opens in Caliper.

    uv run --no-sync python -m caliper.app.mac_launcher      # builds ~/Applications/Caliper.app

Caliper runs from this checkout (`python -m caliper.app`), not as an app bundle, so Finder has
nothing to open .caliper files with. This builds a small launcher: an AppleScript applet that
declares the .caliper file type and, when Finder gives it a file, runs this checkout's Python
with it. If a Caliper window is already open, the file opens there (`caliper.app.opener`);
double-clicking the launcher itself opens a new window. It's ad-hoc signed and registered with
Launch Services, so Finder uses it straight away.

It records the checkout's and the virtual environment's paths, so rebuild it after moving
either. To remove it, delete the app. macOS only.
"""

import argparse
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BUNDLE_ID = "io.github.andrefongkc-cyber.caliper.launcher"
DOCUMENT_TYPE = "io.github.andrefongkc-cyber.caliper.document"
LSREGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
    "/Support/lsregister"
)
REPO = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = Path.home() / "Applications" / "Caliper.app"


def applescript(python: Path, repo: Path) -> str:
    """The applet: `on open` for files from Finder, `on run` for the app on its own."""
    command = f"cd {shlex.quote(str(repo))} && {shlex.quote(str(python))} -m caliper.app"
    quiet = " > /dev/null 2>&1 &"  # in the background, so the applet quits at once

    def text(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    return f"""\
on run
	do shell script {text(command + quiet)}
end run

on open dropped
	set target to quoted form of POSIX path of (item 1 of dropped)
	do shell script {text(command + " ")} & target & {text(quiet)}
end open
"""


def document_types() -> dict[str, object]:
    """What the applet's Info.plist gains: its name and id, and the .caliper file type."""
    return {
        "CFBundleName": "Caliper",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Caliper document",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "LSItemContentTypes": [DOCUMENT_TYPE],
            }
        ],
        "UTExportedTypeDeclarations": [
            {
                "UTTypeIdentifier": DOCUMENT_TYPE,
                "UTTypeDescription": "Caliper document",
                "UTTypeConformsTo": ["public.json"],
                "UTTypeTagSpecification": {"public.filename-extension": ["caliper"]},
            }
        ],
    }


def build(target: Path, python: Path, repo: Path, *, register: bool = True) -> None:
    """Build the launcher at `target` (replacing one there), sign it, and tell Finder."""
    if target.suffix != ".app":
        raise ValueError(f"the launcher must end in .app: {target}")
    info = target / "Contents" / "Info.plist"
    if target.exists():
        if bundle_id(info) != BUNDLE_ID:
            raise FileExistsError(f"{target} is another app; choose another place with --to")
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "caliper.applescript"
        source.write_text(applescript(python, repo))
        subprocess.run(["osacompile", "-o", str(target), str(source)], check=True)
    with info.open("rb") as f:
        plist = plistlib.load(f)
    plist.update(document_types())
    with info.open("wb") as f:
        plistlib.dump(plist, f)
    # Editing Info.plist breaks osacompile's signature; sign again, ad hoc, for this Mac.
    signed = subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(target)],
        capture_output=True,
        text=True,
    )
    if signed.returncode != 0:
        raise RuntimeError(f"codesign failed: {signed.stderr.strip()}")
    if register:
        subprocess.run([str(LSREGISTER), "-f", str(target)], check=True)


def bundle_id(info: Path) -> object:
    try:
        with info.open("rb") as f:
            return plistlib.load(f).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException):
        return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", type=Path, default=DEFAULT_TARGET, help="where to put the app")
    args = parser.parse_args(argv[1:])
    if sys.platform != "darwin":
        print("The Finder launcher is for macOS only.", file=sys.stderr)
        return 1
    # sys.executable is this checkout's .venv Python; resolving it would leave the venv.
    build(args.to, Path(sys.executable), REPO)
    print(f"Built {args.to}. Double-click a .caliper file in Finder to open it in Caliper.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
