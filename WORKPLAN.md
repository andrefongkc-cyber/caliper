Status: the N phase (finish and harden 2D) merged in #54 and closed out on `shared/n-phase-final`: N1–N8 and N10 done, N1 confirmed on Linux CI, N11 deferred (no API key, by decision), next: Andre's Claude Desktop run of the stress plate (N9, test 005 ready); V2 (F1–F8) starts only after that
# Workplan

Human-edited index. Agents update their stream's file, not this one (this update was asked for).

Markers: `[ ]` not started · `[~]` in progress · `[x]` done

- **Current phase (2026-09-30):** V1, V1.5, the AI layer, Performance V2 and Solver V2.1 (#47), the repeat tools and the time left (#48), the 2D V1 audit (#49), the known-issue fixes (#50), checks saved with the part, one label rule, and authors on every change (#51, ADR 0010 proposed), and the dashed carry-on (#52) are on `main`, landed by #53, and the N phase followed in #54 (`main` at b598af0). The plan for what comes next: the Caliper Engine Plan (N1–N11 to finish 2D, then F1–F8 for V2).
  - **The N phase** (merged in #54, closed out on `shared/n-phase-final`; details in [core.md](docs/workplan/core.md#the-n-phase-finish-and-harden-2d-branch-sharedn-phase-2026-09-30-pr-54), [shell.md](docs/workplan/shell.md#the-n-phase-app-side-branch-sharedn-phase-2026-09-30), [ai.md](docs/workplan/ai.md#the-n-phase-ai-side-branch-sharedn-phase-2026-09-30)):
    - [x] N1, the stress plate's reference output pinned by digest. Pinned on macOS, and confirmed on Linux: CI's `core (Linux)` job passed it on #54, so one digest serves both
    - [x] N2, property tests for stored checks, with two deliberate breaks they catch
    - [x] N3, bench cases for a saved check and a pattern: 9 of 9 cases pass
    - [x] N4, closed profiles from lines and arcs, with holes, on both kernels; the stress plate's area to 1e-9. A slot drawn as a rectangle and arcs isn't one profile (C-13)
    - [x] N5, the full suite with OCCT locally: 1544 passed, 0 skipped
    - [x] N6, #51 and #52 reviewed from the app side: three issues fixed (the card's check list, changes held back while the assistant works, the check edit's History label)
    - [x] N7, checks edited from the Checks panel by keyboard, one undoable "Edit Check"
    - [x] N8, a pixel baseline of the stress plate's corner that fails if the carry-on is drawn solid
    - [~] N9, the stress plate live over MCP on `main`: done from Claude Code (test 004). The Claude Desktop run, with its timing, `report_progress`, tool use, and Save and reopen, is Andre's to run: test 005 has the prompt; Lucas's run not done
    - [x] N10, the saved-check workflow automated through the real window, and an older build's refusal checked against the real older build; not a manual pass
    - [ ] N11, a live in-app assistant request: deferred, by Andre's decision (2026-09-30): no API key will be provided, so nothing is installed or changed for it, and AI-6 stays open
    - Found and fixed on the way: a check refused on a machine without a geometry kernel, and `bench/perf.py`, broken since #51
    - Closed out after #54: 1544 tests passed with OCCT, lint, format, and types clean, the bench 9 of 9, the solver numerics clean, and a performance run saved as the baseline for V2 ([core.md](docs/workplan/core.md#closing-out-2026-09-30-after-54-merged))
  - [ ] The dependency and recomputation graph, the foundation for incremental 2D and the 3D feature tree: planned, not started ([docs/workplan/core.md](docs/workplan/core.md#dependency-and-recomputation-graph-planned-2026-09-28-not-started))
- **Core** (Stream A: engine, contracts, bench, tests): [docs/workplan/core.md](docs/workplan/core.md)
- **Shell** (Stream B: app): [docs/workplan/shell.md](docs/workplan/shell.md)
- **AI** (`caliper/ai`, owner Andre, branches `ai/<topic>`): [docs/workplan/ai.md](docs/workplan/ai.md)
- **Decisions:** [docs/adr/](docs/adr/) · **Architecture:** [docs/architecture.md](docs/architecture.md)
- **Starting a stream session:** [docs/kickoff/](docs/kickoff/)
- **Long-term vision:** [docs/vision.md](docs/vision.md) (don't load every session)
