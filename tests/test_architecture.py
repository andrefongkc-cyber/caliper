"""Architecture invariants, enforced as tests rather than conventions.

Invariant 1: caliper.contracts and caliper.engine never import the UI layer,
Qt, or OS-specific modules.
Invariant 7: only caliper/engine/geometry/occt_kernel.py may import OCP.
Layering: caliper.contracts depends on the standard library and itself only.
"""

import ast
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "caliper"
CORE_PACKAGES = ("contracts", "engine")
OCCT_KERNEL = PACKAGE_ROOT / "engine" / "geometry" / "occt_kernel.py"

FORBIDDEN_IN_CORE = frozenset(
    {
        "caliper.app",
        "caliper.ai",
        # UI toolkits
        "PySide6",
        "shiboken6",
        "PyQt5",
        "PyQt6",
        # OS-specific
        "AppKit",
        "Cocoa",
        "Foundation",
        "objc",
        "_winapi",
        "msvcrt",
        "winreg",
        "fcntl",
        "grp",
        "pwd",
        "resource",
        "termios",
    }
)


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _module_name(path: Path) -> str:
    parts = path.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path) -> Iterator[str]:
    """Yield every module a file imports, with relative imports resolved.

    `from a import b` yields both `a` and `a.b`, so `from PySide6 import QtCore`
    and `from caliper import app` are caught.
    """
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                base = ".".join([*base_parts, node.module] if node.module else base_parts)
            else:
                base = node.module or ""
            yield base
            yield from (f"{base}.{alias.name}" for alias in node.names)


def _core_files() -> list[Path]:
    return sorted(p for pkg in CORE_PACKAGES for p in (PACKAGE_ROOT / pkg).rglob("*.py"))


@pytest.mark.parametrize("path", _core_files(), ids=lambda p: str(p.relative_to(PACKAGE_ROOT)))
def test_core_has_no_forbidden_imports(path: Path) -> None:
    forbidden = set(FORBIDDEN_IN_CORE)
    if path != OCCT_KERNEL:
        forbidden.add("OCP")
    violations = sorted({m for m in _imports(path) for f in forbidden if _matches(m, f)})
    assert not violations, f"{path.name} imports {violations}"


@pytest.mark.parametrize(
    "path",
    sorted((PACKAGE_ROOT / "contracts").rglob("*.py")),
    ids=lambda p: str(p.relative_to(PACKAGE_ROOT)),
)
def test_contracts_depend_only_on_stdlib(path: Path) -> None:
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    violations = sorted(
        m
        for m in _imports(path)
        if m.split(".")[0] not in allowed
        and not _matches(m, "caliper.contracts")
        # `from caliper.contracts import x` also yields `caliper`; that's fine.
        and m != "caliper"
    )
    assert not violations, f"{path.name} imports {violations}"


def test_importing_core_does_not_load_forbidden_modules() -> None:
    """Catch forbidden modules pulled in indirectly, e.g. through a third-party library.

    Runs in a fresh interpreter so pytest's own imports don't pollute sys.modules.
    Most meaningful on the macOS CI job, where PySide6 is actually installed.
    """
    script = """
import importlib, json, pkgutil, sys
import caliper.contracts, caliper.engine
for pkg in (caliper.contracts, caliper.engine):
    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        if info.name != "caliper.engine.geometry.occt_kernel":
            importlib.import_module(info.name)
print(json.dumps(sorted(sys.modules)))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    loaded = json.loads(result.stdout)
    forbidden = FORBIDDEN_IN_CORE | {"OCP"}
    violations = sorted({m for m in loaded for f in forbidden if _matches(m, f)})
    assert not violations, f"importing the core loaded {violations}"
