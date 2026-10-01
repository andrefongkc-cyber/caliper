Status: V2's F1 to F8 done on `shared/v2-milestone` (local, not pushed; ADRs 0011 to 0014 Proposed, for Lucas); next: Lucas's review, then a PR when Andre says; N9's Claude Desktop run still open
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
  - **V2, the foundation** (the Caliper Engine Plan's F1–F8, in order; F1 details in [core.md](docs/workplan/core.md#v2-f1-the-part-and-the-v2-document-contract-branch-sharedv2-f1-document-2026-09-30-local-not-pushed), the app-side review in [shell.md](docs/workplan/shell.md#v2-f7-the-contract-reviewed-from-the-apps-side-branch-sharedv2-milestone-2026-10-01-local-not-pushed)):
    - [x] F1, [ADR 0011](docs/adr/0011-a-part-of-ordered-features-and-sketches-on-planes.md) and the contract: a document is one part, features in order, sketches on XY, XZ, or YZ, file schema 4 with every V1 file migrated into one sketch on XY. Done and tested on `shared/v2-f1-document`: every schema-3 golden migrates byte for byte, every replay is unchanged, and N1's digests of `main`'s files still match. The ADR stays Proposed until Lucas confirms F7's answers
    - [x] F2, the kernel grows solids (`shared/v2-milestone`): extrude, union, cut, volume, and meshes on both kernels, held to formulas by one conformance suite
    - [x] F3, extrude as the first feature, recomputed only when needed ([ADR 0013](docs/adr/0013-solids-extrude-and-recomputing-only-what-changed.md), Proposed): the milestone runs headlessly on both kernels (60,000 → 70,000 → undo 60,000, save, reopen, byte-identical replay); a width change rebuilds one prism, a label nothing
    - [x] F4, a persistent-naming spike ([ADR 0014](docs/adr/0014-persistent-naming-by-history.md), Proposed): names by history (a feature's caps, and the side each sketch edge sweeps; edges by the faces they join) survive every rebuild tried, and names by position don't; a cut across a face splits a name, which will be refused as ambiguous. No naming code yet: the first feature that refers to a face adds it
    - [x] F5, the 3D view and a 2D/3D switch ([ADR 0012](docs/adr/0012-the-3d-viewport-our-own-renderer-first.md), Proposed): our own QPainter renderer, no new dependency; orbit, pan, zoom, fit; one document behind both views; frames 0.39 ms for the milestone plate, 19 ms for 2,604 triangles
    - [x] F6, sketch mode, the Part panel, and Extrude, on the real engine: the milestone clicked through the window on both kernels; a tidier toolbar and panels
    - [x] F7, the contract reviewed from the app's side ([shell.md](docs/workplan/shell.md#v2-f7-the-contract-reviewed-from-the-apps-side-branch-sharedv2-milestone-2026-10-01-local-not-pushed)): no contract change needed; four app bugs found and fixed (the assistant's and Claude Desktop's drawing with two sketches, the sketch-size checks, the proposal preview, an extrude's label). ADR 0011's questions answered as built; Lucas to confirm
    - [x] F8, V2's tests ([core.md](docs/workplan/core.md#v2-f8-v2s-tests-end-to-end-branch-sharedv2-milestone-2026-10-01-local-not-pushed)): the milestone end to end through the window on both kernels, from the rectangle to a byte-identical headless replay, with the 3D view's mesh measured at each step; coverage of V2's files measured (91–100%) and the gaps filled. 1754 passed with OCCT; 1688 passed and 54 skipped without it
  - [ ] The dependency and recomputation graph, the foundation for incremental 2D and the 3D feature tree: planned, not started ([docs/workplan/core.md](docs/workplan/core.md#dependency-and-recomputation-graph-planned-2026-09-28-not-started))
- **Core** (Stream A: engine, contracts, bench, tests): [docs/workplan/core.md](docs/workplan/core.md)
- **Shell** (Stream B: app): [docs/workplan/shell.md](docs/workplan/shell.md)
- **AI** (`caliper/ai`, owner Andre, branches `ai/<topic>`): [docs/workplan/ai.md](docs/workplan/ai.md)
- **Decisions:** [docs/adr/](docs/adr/) · **Architecture:** [docs/architecture.md](docs/architecture.md)
- **Starting a stream session:** [docs/kickoff/](docs/kickoff/)
- **Long-term vision:** [docs/vision.md](docs/vision.md) (don't load every session)
