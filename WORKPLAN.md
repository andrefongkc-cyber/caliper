# Workplan

Human-edited index. Agents update their stream's file, not this one.

- **Current phase (2026-09-27):** V1 and V1.5 on `main` (PRs #26–#31), contract frozen. The AI layer is on `main` too: the in-app assistant (#32), Claude Desktop over MCP with its stress-test fixes (#34, #35), and the follow-ups through #46: MCP run timing and the Timing panel, test-run records, opening files, collapsible browser groups, and the selection-hang fix ([docs/workplan/ai.md](docs/workplan/ai.md), setup in [docs/mcp.md](docs/mcp.md)). In flight:
  - `shared/performance-v2`, PR #47: Performance V2 and Solver V2.1, done and in review. The stress plate's Caliper time went from 45 s to 0.8 s (Accept 25 s → 0.02 s). One decision is open: stored values in sketches with fixed geometry can differ from `main` in the last digits ([docs/workplan/core.md](docs/workplan/core.md))
- **Core** (Stream A: engine, contracts, bench, tests): [docs/workplan/core.md](docs/workplan/core.md)
- **Shell** (Stream B: app): [docs/workplan/shell.md](docs/workplan/shell.md)
- **AI** (`caliper/ai`, owner Andre, branches `ai/<topic>`): [docs/workplan/ai.md](docs/workplan/ai.md)
- **Decisions:** [docs/adr/](docs/adr/) · **Architecture:** [docs/architecture.md](docs/architecture.md)
- **Starting a stream session:** [docs/kickoff/](docs/kickoff/)
- **Long-term vision:** [docs/vision.md](docs/vision.md) (don't load every session)
