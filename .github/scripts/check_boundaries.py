"""Fail a pull request whose branch touches files outside its stream's area.

This is one of two layers keeping the parallel streams from colliding. The other is
code-owner review. Rules are keyed on the PR's head branch name:

    stream/core[/topic]    Stream A: engine, bench, tests (not tests/app/ or tests/ai/)
    stream/shell[/topic]   Stream B: app, tests/app/
    ai[/topic]             the assistant: caliper/ai/, tests/ai/
    contracts/<topic>      joint contract change, reviewed by both: unrestricted
    shared/<topic>         repo-wide files (pyproject, CLAUDE.md, CI, ...): unrestricted
    phase-0/<topic>        one-time bootstrap: unrestricted

Any other branch name fails, so the rules can't be sidestepped by accident.

Usage:
    check_boundaries.py --branch NAME --base SHA --head SHA   # compare two commits
    check_boundaries.py --branch NAME FILE...                 # check an explicit file list

Standard library only, so CI can run it without installing the project.
"""

from __future__ import annotations  # also runs on macOS's system Python 3.9

import argparse
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Area:
    name: str
    allowed: tuple[str, ...]
    denied: tuple[str, ...] = ()

    def permits(self, path: str) -> bool:
        return path.startswith(self.allowed) and not path.startswith(self.denied)


CORE = Area(
    name="Stream A (core)",
    allowed=("caliper/engine/", "bench/", "tests/", "docs/workplan/core.md", "docs/adr/"),
    denied=("tests/app/", "tests/ai/"),
)
SHELL = Area(
    name="Stream B (shell)",
    allowed=("caliper/app/", "tests/app/", "docs/workplan/shell.md", "docs/adr/"),
)

AI = Area(
    name="AI (assistant)",
    allowed=("caliper/ai/", "tests/ai/", "docs/workplan/ai.md", "docs/adr/"),
)

STREAM_BRANCHES = {"stream/core": CORE, "stream/shell": SHELL, "ai": AI}
UNRESTRICTED_PREFIXES = ("contracts/", "shared/", "phase-0/")


class UnknownBranchError(ValueError):
    pass


def area_for(branch: str) -> Area | None:
    """The area a branch is confined to, or None if it is unrestricted."""
    for name, area in STREAM_BRANCHES.items():
        if branch == name or branch.startswith(name + "/"):
            return area
    if branch.startswith(UNRESTRICTED_PREFIXES):
        return None
    raise UnknownBranchError(
        f"branch {branch!r} doesn't follow the naming convention. Use stream/core, "
        "stream/shell, ai/<topic>, contracts/<topic>, or shared/<topic> (see CONTRIBUTING.md)."
    )


def violations(branch: str, paths: list[str]) -> list[str]:
    area = area_for(branch)
    if area is None:
        return []
    return sorted(p for p in paths if not area.permits(p))


def changed_paths(base: str, head: str) -> list[str]:
    # --no-renames lists both sides of a move, so moving a file out of another
    # stream's directory counts as touching it.
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--branch", required=True)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    if args.base and args.head:
        paths = changed_paths(args.base, args.head)
    elif not (args.base or args.head):
        paths = args.files
    else:
        parser.error("--base and --head must be given together")

    try:
        area = area_for(args.branch)
        bad = violations(args.branch, paths)
    except UnknownBranchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if area is None:
        print(f"{args.branch}: unrestricted branch, {len(paths)} file(s) changed.")
        return 0
    if bad:
        print(f"{args.branch} belongs to {area.name} but touches files outside its area:")
        for path in bad:
            print(f"  {path}")
        print("Split the change, or use a contracts/ or shared/ branch (see CONTRIBUTING.md).")
        return 1
    print(f"{args.branch}: all {len(paths)} changed file(s) are inside {area.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
