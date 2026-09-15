# Caliper

An AI-native engineering platform built around a geometry core that can check its own work.

> **Status: Phase 0 (foundation).** Nothing user-facing yet. See [WORKPLAN.md](WORKPLAN.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/). uv installs the correct Python (3.13) for you.

```bash
uv sync
```

Then run the checks:

```bash
uv run pytest
```

Optional extras:

| Command | Adds |
|---|---|
| `uv sync --extra app` | Qt desktop shell (PySide6) |
| `uv sync --extra occt` | OpenCascade geometry kernel (large download) |
