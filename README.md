# Caliper

An AI-native engineering platform, built around a geometry core that can check its own work.

Most text-to-CAD tools produce geometry nobody can verify. Caliper is designed for the loop
real engineering needs: make a change, measure the result, assert it's right, and iterate,
against structured, parametric geometry that people, scripts, and AI all edit through the
same commands.

> **Status: Phase 0 (foundation).** The contracts, CI, and docs are in place; the app isn't
> runnable yet. The first milestone: open the app, draw a rectangle, change its width to 120,
> save, reopen, and it's still 120. See [WORKPLAN.md](WORKPLAN.md).

## Getting started

You need macOS (Linux works for the engine) and [uv](https://docs.astral.sh/uv/getting-started/installation/).
uv installs the right Python (3.13) for you.

```bash
git clone <repo-url> caliper
cd caliper
uv sync
uv run pytest
```

Working on the desktop shell:

```bash
uv sync --extra app --group app-test
```

Working on the OpenCascade kernel (large download):

```bash
uv sync --extra occt
```

Checks CI runs:

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```

## Repository layout

```text
caliper/
  contracts/   types and protocols every layer shares (commands, document, queries, kernel)
  engine/      headless core: command bus, file I/O, queries, geometry kernels, CLI
  app/         PySide6 desktop shell
  ai/          AI tool layer (empty in V1)
bench/         evaluation cases
tests/         test suite (tests/app/ holds shell tests)
docs/          architecture, decisions (adr/), workplans, vision
```

## Documentation

- [Architecture](docs/architecture.md): how the pieces fit together. Start here.
- [Decision records](docs/adr/): why things are the way they are.
- [Contributing](CONTRIBUTING.md): workflow, branches, reviews, conventions.
- [Vision](docs/vision.md): where this is headed after V1.

## License

No license has been chosen yet; all rights reserved. Dependencies must follow the
[no-GPL policy](docs/adr/0006-license-policy-no-gpl.md).
