Status: mirror, linear and circular pattern, and the Timing panel's time left, in PR #48, stacked on Performance V2's PR #47, next: review of both, then a live Claude Desktop run on them; planned: the dependency and recomputation graph
# Workplan

Human-edited index. Agents update their stream's file, not this one (this update was asked for).

Markers: `[ ]` not started · `[~]` in progress · `[x]` done

- **Current phase (2026-09-28):** V1 and V1.5 on `main` (PRs #26–#31), contract frozen. The AI layer is on `main` too: the in-app assistant (#32), Claude Desktop over MCP with its stress-test fixes (#34, #35), and the follow-ups through #46: MCP run timing and the Timing panel, test-run records, opening files, collapsible browser groups, the selection-hang fix, and the CAD practices for AI ([docs/workplan/ai.md](docs/workplan/ai.md), setup in [docs/mcp.md](docs/mcp.md)). In flight:
  - [x] `shared/performance-v2`, PR #47 (rebased onto `main` 2026-09-28): Performance V2 and Solver V2.1, done and in review. The stress plate's Caliper time went from 45 s to 0.8 s (Accept 25 s → 0.02 s). One decision is open: stored values in sketches with fixed geometry can differ from `main` in the last digits ([docs/workplan/core.md](docs/workplan/core.md))
  - [x] `shared/mirror-pattern-time-left`, PR #48 (stacked on #47): `mirror_entities`, `linear_pattern`, and `circular_pattern`, each one call of Caliper's own commands; the Timing panel's time left, priced by kind of call and smoothed, and a timer that stops by itself; test records in `test-runs-andre/`. Test 003, the stress plate on these tools from Claude Code: 4m 40s and 162 calls, against 9m 17s and 281 ([docs/workplan/ai.md](docs/workplan/ai.md))
  - [ ] A live run from Claude Desktop on both branches: does it use the tools, the practices, and `report_progress` unprompted?
  - [ ] The dependency and recomputation graph, the foundation for incremental 2D and the 3D feature tree: planned, not started ([docs/workplan/core.md](docs/workplan/core.md#dependency-and-recomputation-graph-planned-2026-09-28-not-started))
- **Core** (Stream A: engine, contracts, bench, tests): [docs/workplan/core.md](docs/workplan/core.md)
- **Shell** (Stream B: app): [docs/workplan/shell.md](docs/workplan/shell.md)
- **AI** (`caliper/ai`, owner Andre, branches `ai/<topic>`): [docs/workplan/ai.md](docs/workplan/ai.md)
- **Decisions:** [docs/adr/](docs/adr/) · **Architecture:** [docs/architecture.md](docs/architecture.md)
- **Starting a stream session:** [docs/kickoff/](docs/kickoff/)
- **Long-term vision:** [docs/vision.md](docs/vision.md) (don't load every session)
