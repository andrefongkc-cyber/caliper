# Workplan

Human-edited index. Agents update their stream's file, not this one.

- **Current phase (2026-09-26):** V1 and V1.5 on `main` (PRs #26–#31), contract frozen. The AI layer is on `main` too: the in-app assistant (#32) and Claude Desktop over MCP with its stress-test fixes (#34, #35) ([docs/workplan/ai.md](docs/workplan/ai.md), setup in [docs/mcp.md](docs/mcp.md)). In flight:
  - `stream/core/performance-v2` (local, not pushed): engine performance, paused after item 1 ([docs/workplan/core.md](docs/workplan/core.md))
- **Core** (Stream A: engine, contracts, bench, tests): [docs/workplan/core.md](docs/workplan/core.md)
- **Shell** (Stream B: app): [docs/workplan/shell.md](docs/workplan/shell.md)
- **AI** (`caliper/ai`, owner Andre, branches `ai/<topic>`): [docs/workplan/ai.md](docs/workplan/ai.md)
- **Decisions:** [docs/adr/](docs/adr/) · **Architecture:** [docs/architecture.md](docs/architecture.md)
- **Starting a stream session:** [docs/kickoff/](docs/kickoff/)
- **Long-term vision:** [docs/vision.md](docs/vision.md) (don't load every session)
