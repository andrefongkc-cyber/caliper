"""Headless command line: python -m caliper.engine <command>.

    replay SCRIPT [-o OUTPUT]   run a command script and write the resulting .caliper file

`inspect` and `export` land in V1 (docs/workplan/core.md).
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from caliper.contracts.commands import Rejected
from caliper.engine.commands.bus import Bus
from caliper.engine.io import script, snapshot
from caliper.engine.io.canonical import LoadError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m caliper.engine", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay", help="run a command script and write the document")
    replay.add_argument("script", type=Path)
    replay.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    args = parser.parse_args(argv)
    return _replay(args.script, args.output)


def _replay(script_path: Path, output: Path | None) -> int:
    try:
        commands = script.load(script_path)
    except (OSError, LoadError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    bus = Bus()
    for index, command in enumerate(commands):
        result = bus.execute(command)
        if isinstance(result, Rejected):
            for error in result.errors:
                where = f" {error.field}" if error.field else ""
                message = f"[{error.code}] {error.message}"
                print(f"error: commands[{index}] {command.kind}:{where} {message}", file=sys.stderr)
            return 1
    if output is None:
        # Bytes, not text: text mode would translate newlines on Windows.
        sys.stdout.buffer.write(snapshot.dumps(bus.document).encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        snapshot.save(bus.document, output)
    return 0
