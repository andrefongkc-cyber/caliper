# Caliper

AI-native engineering platform. The wedge is a geometry core whose API lets an agent check
its own work (measure, query, assert, retry) against real parametric geometry. Current scope:
**V1, 2D sketching.** Where things stand: `WORKPLAN.md` → `docs/workplan/`. How it fits
together: `docs/architecture.md`. Don't load `docs/vision.md` unless asked.

## Stack

Python 3.13 · uv · OCCT via cadquery-ocp (`occt` extra) · planegcs (V1.5, Proposed) ·
PySide6 via pyside6-essentials (`app` extra) · canonical JSON files · pytest, hypothesis,
pytest-qt · ruff · mypy --strict on contracts + engine · GitHub Actions

```bash
uv sync                                  # engine + dev tools
uv sync --extra app --group app-test     # + Qt shell and pytest-qt
uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy
```

## Ownership

| Path | Owner |
|---|---|
| `caliper/engine/`, `bench/`, `tests/` (not `tests/app/`), `docs/workplan/core.md` | Stream A (core), branch `stream/core` |
| `caliper/app/`, `tests/app/`, `docs/workplan/shell.md` | Stream B (shell), branch `stream/shell` |
| `caliper/contracts/` | Joint. Frozen after the Phase 0.5 spike; change only on a `contracts/<topic>` branch reviewed by both. `kernel.py` stays Provisional |
| `CLAUDE.md`, `WORKPLAN.md`, `pyproject.toml`, `uv.lock`, `.github/`, other docs | Maintainers, on `shared/<topic>` branches. Agents: propose, don't edit |

The `boundaries` CI check rejects stream branches that touch files outside their area.

## Invariants

1. `contracts/` and `engine/` never import `app/`, `ai/`, Qt, or OS-specific modules (tested).
2. Every state change is a `Command` sent to the `CommandBus`. Nothing else mutates a document.
3. Commands are typed, bus-validated, serializable, and undone via an automatic `Delta`. No hand-written inverses.
4. Every command works headlessly; `python -m caliper.engine replay` output is byte-identical.
5. The AI layer uses exactly the commands and queries the UI uses. No privileged access.
6. Queries and assertions are designed alongside commands, never bolted on.
7. OCCT stays behind the `Kernel` protocol; only `caliper/engine/geometry/occt_kernel.py` imports `OCP` (tested).
8. Project files store inputs only, never derived values (ADR 0005).

## Conventions

- **No speculative generality.** No plugin systems, factories, or config layers for anything outside the current version. Flag it instead.
- **Invalid input returns `Error` values** with stable `ErrorCode`s. It never raises.
- **Contract dataclasses** are `frozen=True, slots=True, kw_only=True`. Units are mm and degrees, Y-up.
- **Selection, hover, and previews are UI state:** never commands, never in the document.
- **Licenses:** no GPL/AGPL dependencies (ADR 0006). No secrets in git; `.env.example` only.
- **Commits** are small, single-concern conventional commits (`feat(engine): ...`). Rebase onto `main` before every merge.
- **ADRs** are immutable once Accepted. Supersede with a new ADR; don't edit.
- **Workplan:** update your stream's file after every meaningful change, mid-task included. Its first line is `Status: doing X, next: Y`; markers are `[ ]` `[~]` `[x]`.
- **End every response** with a one-paragraph summary, your workplan's `Status:` line, and the next 1–3 tasks with markers.

## ADRs

| # | Decision | Status |
|---|---|---|
| [0001](docs/adr/0001-geometry-kernel-occt.md) | Geometry kernel: OCCT behind a provisional Kernel protocol | Accepted |
| [0002](docs/adr/0002-command-bus-and-snapshot-document.md) | Command bus with automatic deltas; snapshot document | Accepted |
| [0003](docs/adr/0003-constraint-solver-planegcs.md) | Constraint solver: planegcs | Proposed |
| [0004](docs/adr/0004-desktop-shell-pyside6-mac-first.md) | Desktop shell: PySide6, Mac-first | Accepted |
| [0005](docs/adr/0005-file-format-and-schema-versioning.md) | File format and schema versioning | Accepted |
| [0006](docs/adr/0006-license-policy-no-gpl.md) | License policy: no GPL/AGPL | Accepted |
