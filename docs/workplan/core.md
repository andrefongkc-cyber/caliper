Status: faster solve status, suggestions, and picking done on `stream/core/solve-status-and-suggestion-speed` (#28 items 2 and 3), next: Andre's review of the PR, and a decision on limiting parallel/perpendicular suggestions to nearby geometry
# Core workplan — Stream A

Owns `caliper/engine/`, `bench/`, `tests/` (except `tests/app/`), and this file. `caliper/contracts/` is frozen for V1 (PR #22): changes go through a joint `contracts/` PR.

Markers: `[ ]` not started · `[~]` in progress · `[x]` done

## Tangency at a joint (branch `shared/mcp-stress-fixes`, 2026-09-25)

Found by the first large MCP session: a fillet's tangency was rejected as redundant once the arc's end was coincident with the line, though DOF showed it wasn't implied.

- [x] **Root cause.** `_tangent_line` says "the centre is `r` from the line". Where the arc's end is joined to the line, that equation's gradient equals the coincidence's (their difference is `r(cos t - 1)`, zero to first order), so `_redundancy`'s rank test saw nothing new, although tangency fixes the arc's end angle. The same held for two arcs joined end to end.
- [x] **Fix.** `sketch._joints` finds coincident constraints putting an arc's end on a line or another arc or circle; `_compile` then writes that pair's tangency with `relations.tangent_at_joint`: the radius at the joint perpendicular to the line (or collinear with the other radius). With the joint held it is the same set, and independent at first order, so redundancy, conflicts, and DOF all see it. Free tangency (no joint) is unchanged. Redundancy detection itself is unchanged
- [x] Tests: `tests/engine/constraints/test_tangent_at_a_joint.py` (fillet both sides, tangency before the joint, a joint on the line itself, moving lines keep it tangent, two arcs, and really-implied tangency still rejected). Disabling the joint detection fails 6 of 7

## Query speed: solve status, suggestions, picking (branch `stream/core/solve-status-and-suggestion-speed`, 2026-09-23)

User request (2026-09-23), from #28 items 2 and 3: `solve_status` redid every cluster on every edit, and `suggest_constraints` took 3.4 s for one line, too slow to suggest while drawing. No contract change, and no answer changes: equivalence tests compare every changed query with the code it replaced.

- [x] **`solve_status` redoes only what an edit touched.** `sketch.status` keeps the last document's clusters and per-cluster results. The next document is compared by identity (documents share the entity objects an edit didn't change): if no constraint or driving dimension changed, the clusters keep their members and only the touched ones are solved again; otherwise the clusters are regrouped and every one whose entities are unchanged is reused
- [x] **A grid of geometry** (`caliper/engine/spatial.py`), one per document, built on first use: every geometry entity by its covering box (outline, inside, and every point feature). It only narrows searches; each caller still runs its exact test
- [x] **Picking and box selection use it:** `entity_at_point`, `nearest_feature`, `reference_at_point`, `entities_in_box`. This is the spatial index #17 item 4 asked about
- [x] **Suggestions search from the entities in scope,** through the grid for coincident, midpoint, point-on-curve, and tangent. Parallel and perpendicular don't depend on distance, so every straight is still a partner, but only for straights in scope. `implied` joins the candidate's clusters once per call instead of regrouping the whole document per candidate
- [x] Tests: the old all-pairs `suggest` and the check-everything pickers are kept in the tests as references, and random sketches must give identical answers (300 examples each); the status after every step of a random constrained session (commands, undo, redo) must equal one computed from scratch; a line tilted under an unchanged constraint, alone or with a constraint added elsewhere, must be solved again. Each was checked by breaking the code on purpose (13 ways); every break failed a test
- [x] Benchmark: `uv run python tests/engine/bench_queries.py` builds `bench_canvas.py --constrained`'s sketch without Qt

| Median ms, this Mac | 2,000 before | 2,000 after | 10,000 before | 10,000 after |
|---|---|---|---|---|
| `solve_status` after an edit | 10 | **0.13** | 52 | **0.7** |
| `solve_status`, first sight of a document | 10 | 12 | 55 | 60 |
| `entity_at_point` / `nearest_feature` / `reference_at_point` | 1.0 / 2.1 / 4.5 | **0.002 to 0.004** | 4.8 / 10.3 / 22 | **0.002 to 0.004** |
| First pick after an edit (builds the grid) | n/a | 1.5 | n/a | 8.0 |
| `suggest_constraints`, a constrained line (#28) | 3,650 | **109** | not measured | **539** |
| `suggest_constraints`, a new line drawn on a rectangle | n/a | 26 | n/a | 141 |

- [ ] **Decision needed (a contract change):** what's left of suggestion time is parallel and perpendicular, which the contract offers for every straight in the sketch however far away. A new line on a rectangle gets 2,103 suggestions at 2,000 entities (10,503 at 10,000), almost all parallel or perpendicular to distant lines, and a constrained line needs a redundancy check against each. Limiting them to geometry near the scope, as the positional kinds already are, measured 1.3 ms at 2,000 and 7 ms at 10,000, with 7 suggestions for the new line. It changes what `suggest_constraints` returns, so it needs a joint `contracts/` PR and Lucas's view; not done here
- Not done: the grid is rebuilt for each new document (8 ms at 10,000, paid by the first pick after an edit). Updating it from the previous document would make that about 1 ms if it ever shows up in a profile

## Post-V1 contract fixes (branch `contracts/post-v1-fixes`, 2026-09-22)

User request (2026-09-22): finish the post-V1 contract cleanup before `DragFeature` or the AI work: the three PR #22 decisions recommended "yes", then the next ready gap in the `Change` and undo bookkeeping. Joint `contracts/` branch, so Lucas reviews. No constraint, solver, or shell code changed.

**State:** merged as PR #29 on 2026-09-23 (Lucas approved). 880 passed, 16 skipped (the OCCT extra isn't installed here); ruff, format, and mypy clean; bench 7/7.

- [x] **Position metrics** (gap 10, PR #22 decision 5). `Metric.POSITION_X` and `POSITION_Y`: `refs=(point,)`, the point feature's signed coordinate, with `feature_point`'s validation and errors on `refs[0]`. Two metrics, not one, because `Expectation.expected` is one float; they mirror `DISTANCE_X` and `DISTANCE_Y`. `bench/cases/move-right-30` now checks where the rectangle ended up, and a test shows a 25 mm move used to pass on width and height alone
- [x] **`Arc.start_angle` in [0, 360)** (decision 2). `build_entity` reduces it with `canonical_angle`, which also catches `%` rounding a tiny negative angle up to exactly 360.0, so commands, resolved commands, replay, and file loading agree. `handle()` does the same for solved geometry, which skips `build_entity`. No schema bump: every reader already accepts these values, and loading normalizes old files (fixture `v1/arcs.caliper` loads to `arcs.caliper`). A bump is a reviewer decision if wanted anyway
- [x] **`LoadError` in `contracts/errors.py`** (decision 3). Same class, message, and `errors`. `caliper.engine.io.canonical.LoadError` is still the same class, re-exported, so the shell works unchanged
- [x] **Commit notice** (core gap 9, issue #17 item 2). `ChangeReason.COMMIT`, sent once by the outermost transaction when it recorded an entry or cleared the stacks, after the labels changed. Its delta is empty, not the net delta #17 proposed: every Change's delta is what changed since the previous one, and the commands inside already announced theirs. With the net delta, `SketchBrowser._apply` would insert every entity a transaction created a second time. The state machine test now keeps a subscriber that mirrors the document from deltas and the labels from Changes, and checks both after every step
- Not taken: an author on `Change` (decision 4) is new API with a working shell stamp; a revision counter (decision 6) has no profile asking for it. Both stay deferred

For Lucas (Stream B files, not edited here):
- `caliper/app/main_window.py:38` can import `LoadError` from `caliper.contracts.errors`; `session.py:242`'s docstring names the old module
- `DocumentSession._on_change` clears the flagged (red) ids on every Change, so a COMMIT would clear a rejection flagged inside a transaction that still commits. Neither shell transaction can reject today (agent proposals apply only when error-free; Toggle Construction only flips a flag); skipping `ChangeReason.COMMIT` there would keep it that way
- `caliper/app/panels/checks.py` `describe()` has no case for the position metrics, so a position check reaching the Checks panel would fail on an unbound `subject`. Nothing in the shell creates one yet

Found, not fixed (the solver is out of scope here):
- `caliper/engine/constraints/sketch.py:434` writes `new %= 360.0`, which stores exactly 360.0 for a tiny negative angle; the constraint property tests found one. `_arcs_in_range` in `handlers.py` covers it now. The proper fix is `canonical_angle` there, after which `_arcs_in_range` can go

## V1.5 — Sketch constraints and dimensions (branch `contracts/sketch-constraints`, started 2026-09-22)

User request (2026-09-22): the full constraint and dimensioning engine, engine first, minimal debug surface. Joint `contracts/` branch because it changes the frozen contract; Lucas reviews. Answers the eight decisions in issue #18.

**Picking this up in a fresh session.** The work is done and pushed; nothing is half-finished in the tree.

- **Where it is:** on `main`, merged as PR #26 on 2026-09-22 with "Rebase and merge", after flattening out a merge commit (the flattened tree was identical to the approved head)
- **What it is:** `caliper/engine/constraints/` holds the solver (`relations.py` is the table of constraint types, `sketch.py` the solving and status, `dimensions.py` the seven dimension kinds, `suggest.py` the inference). Commands solve through `handlers.py`; the queries are on `DocumentQueries`
- **See it work:** `uv run python -m caliper.engine inspect tests/engine/fixtures/constraints.caliper` prints a solved plate with its constraints, degrees of freedom and driving values. `uv run pytest tests/engine/constraints` runs the 129 tests for it
- **Don't re-litigate:** the solver is ours rather than planegcs (Andre's decision, ADR 0008); constraints are one generic entity, not one type each (ADR 0009); conflicting and redundant additions are refused rather than stored
- **Still open:** ADRs 0008 and 0009 are marked Proposed in their files. Talking to Lucas means posting from Andre's account, so ask Andre first

Decisions so far:
- **Solver: our own, pure Python** (user decision 2026-09-22), superseding ADR 0003's planegcs. Reasons: no native build on every Mac, engine stays dependency-free, deterministic replay, DOF per entity. New ADR 0008 (Proposed); ADR 0003 itself is left as written
- **Constraints are one generic entity** (`Constraint(type, refs)`) with a registry of relation specs in the engine, not one dataclass per kind (differs from #18 section 1; the palette can list types from the applicability query instead of the `Command` union)
- **Dimensions keep the two V1 types** (the shell draws and edits them) plus `AngleDimension`; all three get `value: float | None` (None = driven, a number = driving), as #18 section 2 proposed. One engine-side system infers, classifies, measures and solves all seven kinds (length, distance, horizontal, vertical, radius, diameter, angle); `CreateDimension(refs, placement)` infers the kind like Onshape
- **Solved positions are stored** (ADR 0005's open V1.5 choice, #18 section 6). The solve runs inside each command, so the solve is part of the automatic delta and undo needs nothing new
- **Conflicting or redundant new constraints are `Rejected`**, naming the constraints involved in `Error.ids` (#18 section 4, stricter on redundancy)
- **Debug surface is the CLI** (`inspect` shows constraints, DOF and conflicts), not `caliper/app/`, which is Stream B's

Plan (markers updated as work lands):
- [x] Contract: `Point`, `construction` flag, curve/edge features (`CURVE`, rectangle sides), `Constraint` + `ConstraintType` (14), driving `value` on dimensions, `AngleDimension`, `CreatePoint` / `CreateConstraint` / `CreateAngleDimension` / `CreateDimension`, `Error.ids`, six error codes, queries `solve_status` / `applicable_constraints` / `infer_dimension` / `dimension_type` / `suggest_constraints` / `constraints_on` / `reference_at_point`
- [x] Solver core (`caliper/engine/constraints/`): dual-number derivatives (`ad.py`), one rank-revealing Gram-Schmidt for steps, rank, and "implied by" (`linalg.py`), unknowns per entity with feature formulas identical to queries (`model.py`), weighted minimum-norm Newton with backtracking, cluster splitting (`sketch.py`)
- [x] Relation registry (`relations.py`): every constraint type is a table of accepted selections with their equations; applicability, canonical order, which reference moves, and whether it turns lines all come from it
- [x] Bus integration (`handlers.py`): creates, edits, moves, and fillets solve the clusters they touch, in stages (nothing moves → the named mover or the edited entity's followers → more → the cluster; then a nudged retry). Deletes cascade and never solve. Fillet drops the corner coincidence it replaces
- [x] Solver behaviour tuned against random sketches: reshaping costs 100× moving (free shapes translate, not shrink); steps never pass through invalid geometry; near-collapse is failure; direction constraints turn lines (lengths held on the first attempts) instead of shrinking them; a nudged retry gets off 90° saddles
- [x] Conflicts: QuickXplain finds the smallest set of existing constraints that can't hold with the change (6.5 s → 0.34 s at 160 unknowns; every conflict in 400 random chained sketches names its culprits). Redundant additions rejected with the constraints that imply them. Impossible rectangle-side requests refused up front as not applicable
- [x] DOF and solve status: per cluster (unknowns − rank) and per entity (null-space rank), four states, cached per document
- [x] Dimension system (`dimensions.py`): classify / measure / options / infer from selection + placement; `dimension_value` goes through it
- [x] Suggestions (`suggest.py`) + `Suggestions` session object (reject, accept, ignore, disable), never in the document
- [x] File format v2, migration 1→2 with a fixture test; schema-1 files kept in `tests/engine/fixtures/v1/`
- [x] CLI `inspect`: sketch status, per-entity DOF, driving/construction/conflict markers, constraints; `replay` errors list the ids involved
- [x] Tests (534 engine-side, all green; `tests/engine/constraints/` has 129): every constraint type, all seven dimension kinds, applicability, DOF and the four states, conflicts and redundancy, suggestions, construction, picking, fillets, files, transactions, solver building blocks, and hypothesis property tests over random constrained sketches (200 examples each). Golden replay fixture `constraints.*` (byte-identical: lines and circles only). Bench case `constrained-plate-width-120`; bench 7/7
- [x] ADR 0008 (our own solver, supersedes 0003's choice) and ADR 0009 (constraints in the document; settles ADR 0005's V1.5 storage question), both Proposed
- [x] Shell follow-up, on this branch so PR #26's checks pass (Stream B's files, so Lucas reviews it): the palette leaves a field it can't render at its default instead of dropping the whole command (the `construction` flag had hidden Create Line/Circle/Arc/Rectangle, failing 11 palette tests), the Sketch browser falls back to an icon for kinds it doesn't know, and angle dimensions reuse the dimension icon. All 841 tests pass. Open for Lucas: whether `CreateConstraint` and `CreateDimension` belong in the palette at all, and icons for points and constraints
- [x] CLAUDE.md: stack line, ADR table rows for 0008/0009, and the V1.5 pointer (maintainer file, changed on Andre's instruction)

Partial or blocked, deliberately:
- **Pierce**: needs 3D curves referenced from outside the sketch. The type exists and is reported `constraint.unsupported` everywhere (applicability, commands); no equations
- **Curvature**: real G2 for line-line (collinear and joined) and arc-arc (same circle, joined); refused for line-arc (impossible). Its real use, splines, doesn't exist yet
- **Performance**: dense linear algebra in Python. Commands are fast to ~160 unknowns per cluster (56 ms per edit); a 60 Hz drag preview would fit ~100. Sparse elimination or planegcs behind the same seam if profiles demand it
- **Local solver**: a change needing a large jump can fail where a global method would succeed; it then reports a conflict with ids, never a wrong result
- **Cross-platform bits**: solves with lines and circles are bit-identical everywhere; with arcs or angles, identical on a platform and possibly different in the last bits across platforms (ADR 0008)
- **Not built**: a `DragFeature` command (issue #18 decision 6), expressions/variables for dimension values (the `value` field leaves room), constraint glyph placement (UI state by design), a spatial index

Next, in order:
- [x] Lucas's review of PR #26: approved, and merged on 2026-09-22
- [x] Before merging: flatten the branch onto `main` (done 2026-09-22; same tree as the approved head)
- [~] ADRs 0008 and 0009 from Proposed to Accepted (maintainer files): PR #30, on `shared/adr-0008-0009-accepted`
- [ ] Maintainers (propose only): `docs/architecture.md` gains a "Constraints" section (relation registry → solve in commands → status queries)
- [ ] After review: `DragFeature` + a drag-preview timing budget, then sparse elimination if clusters past ~100 unknowns show up
## Start here (2026-09-21)

**Where things stand (updated 2026-09-22).** V1.5 constraints are on `main` (PR #26); the
post-V1 fixes at the top are the current work. What follows describes `main` before V1.5. The V1 engine is
finished and on `main`: every command (create, modify, move, delete, fillet), transactions and merge keys, the full query API including `check`, OCCTKernel behind the `occt` extra (used by default when installed), the optional file history, and the `replay` / `inspect` / `export` CLI. The contract is frozen (PR #22). CI runs the OCCT conformance suite (PR #12); ADRs 0003 and 0007 are Accepted. On `main` with every extra installed: 698 passed, 1 failed (below), bench 6/6.

Branch names in the history below (`stream/core/queries` and so on) are historical: all of them merged through PR #15 and were deleted.

**Loose end:** issue #21. `tests/app/test_panels.py::test_area_isnt_offered_without_a_geometry_kernel` assumes no kernel is installed, so it fails on any machine with the `occt` extra (CI's app job doesn't install it). A tested fix is in the issue; it's Lucas's file, and he hasn't replied yet.

**Next, in order.** The six contract decisions deferred from PR #22, each with a recommendation there, plus the next milestone:

- [x] **A position `Metric`** (contract gap 10). Done on `contracts/post-v1-fixes`: `POSITION_X` and `POSITION_Y`
- [x] **Normalize `Arc.start_angle` to [0, 360)** (deferred decision 2). Done on `contracts/post-v1-fixes`, no schema bump
- [x] **Move `LoadError` into `contracts/`** (decision 3). Done on `contracts/post-v1-fixes`; the old engine name still works
- [x] **Announce a transaction commit** (core gap 9, issue #17 item 2). Done on `contracts/post-v1-fixes`: `ChangeReason.COMMIT` with an empty delta
- [ ] The other three deferred decisions, recommended "not yet" in PR #22: `Error` returns for the pickers, an author on `Change`, a document revision counter
- [x] **V1.5: constraints.** Done in PR #26, with our own solver instead of planegcs (ADRs 0008 and 0009, both Proposed). Lucas's - [x] Spatial index for hit-testing: the grid in `caliper/engine/spatial.py` (see Query speed at the top)all in `nearest_feature` + `entity_at_point`

**How work lands.** Engine work on `stream/core/<topic>` branches; anything touching `caliper/contracts/` on a `contracts/<topic>` branch reviewed by both. Every PR needs Lucas's approval and "Rebase and merge"; branches must be linear (no merge commits) or GitHub offers no way to merge. Talking to Lucas means a GitHub issue or comment posted from Andre's account, so ask Andre first.

## Phase 0 — Foundation (single session, before the streams split)

Steps are numbered 1–8 to avoid confusion with Phase 0.5, the milestone spike.

- [x] Step 1: Architecture review. Decisions agreed; recorded as ADRs 0001–0006 in step 4
- [x] Step 2: Scaffold
  - [x] git repo, `main` + `phase-0/foundation` branch
  - [x] `pyproject.toml` (uv; extras `app` = pyside6-essentials, `occt`; groups `dev`, `app-test`), `.gitignore`, `.env.example`, `.python-version` 3.13
  - [x] ruff (incl. GPL-only Qt module ban), mypy strict on contracts + engine
  - [x] Import-boundary test (invariants 1 and 7, contracts stdlib-only); verified it fails on violations
  - [x] CI: `core.yml` (Linux, every PR) + `app.yml` (macOS, main + shell-affecting PRs). Not yet run on GitHub: no remote
- [x] Step 3: `contracts/`. All ten decisions approved by the user (2026-09-14)
  - [x] `errors.py` (ErrorCode, Error), `document.py`, `queries.py`, `commands.py`, `kernel.py` (Provisional); mypy strict clean
  - [x] `tests/contracts/test_contracts.py`: frozen/slots/kw_only, unique kinds, create commands mirror entities, ModifyEntity covers every field
  - [x] ADR 0002 (command bus, deltas, undo) and ADR 0005 (file format)
- [x] Step 4: Collaboration setup + docs (branch protection waits for the remote)
  - [x] CI boundary check: stream branches may only touch their own areas (`tests/app/` belongs to Stream B)
  - [x] Dependency license test + `licenses` workflow (ADR 0006)
  - [x] `.github/CODEOWNERS` with placeholder usernames (user fills in before the first PR)
  - [x] ADRs 0001, 0003 (Proposed), 0004, 0006
  - [x] architecture.md, vision.md, CLAUDE.md (+ nested engine/app), README, CONTRIBUTING (incl. branch protection settings)
- [x] Step 5: `FakeKernel` + shared kernel conformance suite
  - [x] `caliper/engine/geometry/fake_kernel.py`: analytic Rectangle/Circle faces; mypy strict clean
  - [x] `tests/engine/geometry/test_kernel_conformance.py`: hypothesis property tests with size-scaled tolerances; OCCT cases skip until OCCTKernel exists. Verified it fails on a wrong formula and a 10 µm bbox error
  - [x] `tests/conftest.py`: hypothesis `ci` profile (derandomized) when `CI` is set
- [x] Step 6: Headless vertical slice: the milestone without UI (create 100×50 → width 120 → save → load → replay byte-identical)
  - [x] `engine/document/delta.py`: diff + checked apply (stale deltas fail loudly)
  - [x] `engine/commands/validation.py` + `handlers.py`: all create commands and ModifyEntity; Move/Delete raise NotImplementedError until V1
  - [x] `engine/commands/bus.py`: execute, undo/redo (bounded by count), labels, subscribe; transactions/merge_key/queries raise NotImplementedError until V1
  - [x] `engine/io/`: canonical JSON (strict parse), structural codec, snapshot save (atomic)/load with validation + migration chain, command scripts
  - [x] `python -m caliper.engine replay` + committed golden file compared on Linux and macOS CI; `.gitattributes` keeps files LF
- [x] Rewrite Part 2 / Part 3 kickoff prompts → `docs/kickoff/stream-a-core.md`, `docs/kickoff/stream-b-shell.md`
- [x] Step 7: Bench stub: `bench/run.py` + cases `rectangle-100x50`, `resize-width-120`
  - [x] Reference solver, byte-exact snapshot comparison; expectations report "pending" until queries land (V1)
  - [x] `tests/test_bench.py` confirms wrong results and rejected solutions fail
- [x] Step 8: Check in with the user, then open PR `phase-0/foundation` → `main`
  - [x] CODEOWNERS: Stream A @andrefongkc-cyber, Stream B @lucassnam
  - [x] `app (macOS)` runs on every PR and is a required check (public repo: macOS runners are free)
  - [x] Rewrote all 31 commits to the GitHub no-reply address (trees and dates unchanged); repo-local `user.email` set
  - [x] Installed `gh` 2.100.0 (checksum verified); user signed in via device flow (scopes repo, workflow)
  - [x] Created empty public repo https://github.com/andrefongkc-cyber/caliper; `origin` set; invited @lucassnam (write)
  - [x] Pushed `main` and `phase-0/foundation`
  - [x] Branch protection on `main`: 1 approval + code owners, stale approvals dismissed, required checks `core (Linux)` / `app (macOS)` / `boundaries` (strict), linear history, admins included
  - [x] Opened https://github.com/andrefongkc-cyber/caliper/pull/1; fixed CI setup (pinned setup-uv to a commit, disabled pytest-qt in the licenses job). All four checks green
  - [x] Lucas approved and merged PR #1 (2026-09-15) as a squash: one commit `9c7b5ee`, tree identical to `phase-0/foundation`. Future PRs use "Rebase and merge"

## Phase 0.5 — Milestone spike (4-day timebox; shell side in shell.md)

- [x] planegcs build test on Apple Silicon (2026-09-15). ADR 0003 steps 1–3 pass; proposing Accepted (maintainers edit the ADR)
  - Setup: Xcode 26.6 / Apple clang 21, Homebrew `eigen@3` 3.4.1 + `boost` 1.92.0 (header-only use). CMake 4.4.3 and pybind11 3.1.0 come from PyPI via scikit-build-core
  - Build: `CMAKE_PREFIX_PATH="/opt/homebrew/opt/eigen@3;/opt/homebrew/opt/boost" uv pip install --no-binary planegcs planegcs==0.8.0` on Python 3.13: 15 s, no errors. Upstream suite: 227 passed, 1 skipped
  - Solve: 4 lines + 4 coincident + H/V + width/height distances + fixed corner → DOF 0, no conflicts/redundancy, exact (0,0)–(100,50); 5 re-solves and a fresh build are bit-identical; width 100→120 re-solves to (120,50). Unfixed (DOF 2) also converges, but a width change moves both sides, so V1.5 must pin what stays put. Over-constraint is reported as conflicting
  - Wheel: `MACOSX_DEPLOYMENT_TARGET=14.0 uv build --wheel` gives `macosx_14_0_arm64`, links only libc++/libSystem (self-contained). Default target is the host OS (26.0), so CI must set it. Step 4 (cibuildwheel in CI) not run: needs a `.github/` workflow, to propose on a `shared/` branch
- [x] Verify `pyside6-essentials` contains no GPL-only modules (ADR 0006), 2026-09-15. **It does ship some; the shell never loads them**
  - 6.11.2 bundles libraries from 3 modules Qt licenses GPL-3-only (doc.qt.io/qt-6/licensing.html): Qt Lottie Animation (`QtLottie`, `QtLottieVectorImageGenerator`, `QtLottieVectorImageHelpers`), Qt Quick Timeline (`QtQuickTimeline`, `QtQuickTimelineBlendTrees`), Qt Qml Compiler (`QtQmlCompiler`, used by the bundled `qmllint`/`qmlls`/`qmlformat`). Its Qt tools (Designer, Linguist, Assistant, qml*) are GPL-3 with the Qt GPL exception. None of the Python modules it ships are GPL-only
  - The running shell loads only QtCore, QtGui, QtWidgets, QtDBus, and the platform plugin; `caliper/app` imports no QtQml/QtQuick. Guard: `tests/test_qt_gpl_modules.py` (macOS app job) fails if the main window loads any GPL-only Qt library; verified it catches a forbidden library
  - For maintainers: `pyproject.toml`'s comment ("essentials excludes ... where the GPL-only Qt modules live") is inaccurate; V1 packaging must leave these libraries and tools out of the app bundle; consider banning `PySide6.QtQml`/`QtQuick` imports in ruff, since Timeline and Lottie are only reachable through QML
- [x] `Queries` for the spike: `caliper/engine/queries.py` (`DocumentQueries`), returned by `bus.queries`
  - `bounding_box` and `entity_at_point` for line, circle, arc, rectangle; other methods `NotImplementedError`
  - Hit-testing measures distance to the outline (a rectangle's interior misses); arcs use endpoints + axis crossings for tight bounds
  - Bench marks each unimplemented `check` as pending; both cases still pass with 2 pending expectations each
  - Tests: `tests/engine/test_queries.py` (unit + hypothesis: tight arc bounds, translation, edge hits); verified they fail on a dropped axis crossing and on interior hits
- [x] Contract gaps the spike surfaces (both sides; resolved or deferred in the freeze, PR #22):
  1. `entity_at_point` / `nearest_feature` / `entities_in_box` can't return `Error`, so invalid input (NaN point, negative tolerance, inverted box) has no way to say so. Engine returns "no match" for now. Decide: add `| Error`, or document "invalid input matches nothing"
  2. `entity_at_point` doesn't say outline vs interior. Engine uses the outline; the docstring should say so
  3. "Ties broken by id" is string order, so `e10` sorts before `e2`. Say so explicitly
  4. `bounding_box` doesn't specify: a document with no geometry (engine: `SELECTION_EMPTY`), an annotation id (engine: `ENTITY_WRONG_KIND`), duplicate ids (engine: allowed). `Error.field` is `"ids"` with no index
  5. Bounds exclude annotations, so zoom-to-fit clips dimension text unless the shell adds label extents
  6. `nearest_feature` doesn't say how ties break or what distance means. Engine: distance to feature points (not outlines), ties to the lower entity id (the shell's test fake agrees)
  7. `entities_in_box` "touching" is unspecified for closed shapes. Engine (after interior-picking): the inside of a circle or rectangle counts, matching `entity_at_point`
  8. `area_properties` needs a kernel, but `CommandBus` has no say in which. Engine: optional `kernel=` on `Bus`, `kernel.unavailable` without one
- [x] PR #2 merged 2026-09-15 (Lucas, squashed into `7ac0543`); PR #3 (milestone + V1 shell) rebase-merged the same day. Tests on `main` with the app extra: 293 passed
- [x] PR #4 (PR summary template, "Rebase and merge" rule) rebase-merged 2026-09-15 as `88a23df`. Deleted the old `stream/core` branch (local and remote; its content is `7ac0543` on main). Stream A now works on `stream/core/<topic>` branches
- [ ] Engine pieces the shell already calls through `caliper/app/engine_gaps.py`, in its order (shell.md):
  - [x] `feature_point` + `nearest_feature` (snapping, dimension tool, drawing distance dimensions). Merged as PR #8, 2026-09-16
    - Engine side done: every POINT_FEATURES entry for all four types (arc points exact at multiples of 90°); bad refs reuse the command validator's errors (`ref.entity` / `ref.feature`); tests include a hypothesis round-trip (arc feature → snap → same point, on the arc), verified to fail on a wrong arc mid, a reversed tie-break, and a wrong rectangle center
    - Blocked on Stream B: `tests/app/test_canvas.py` has 3 tests that assert these queries are missing (`test_paints_every_entity_kind` expects 1 hidden dimension, `test_without_point_queries_the_pointer_snaps_to_the_grid`, `test_dimension_tool_reports_missing_point_picking`). They fail on this branch, and `app (macOS)` is required. `CompletedQueries` in `tests/app/conftest.py` can drop its two stand-ins once this lands
    - Sent Lucas issue #7 (2026-09-15) with a tested patch: a `missing_point_queries=True` bus marker for those 3 tests. Verified 81 app tests pass with it on `main` (`88a23df`) and on this branch. Dropping the stand-ins must wait until this PR merges: on current `main` that breaks 3 `complete_queries` tests. Not pushing until the fix is on `main`
  - [x] `dimension_value`, plus `measure_distance`, `area_properties`, `entities_in_box`, `check`: every Queries method now answers (branch `stream/core/queries`, stacked on `qt-license-check` → `feature-queries`, local)
    - `area_properties` goes through the kernel: `Bus(kernel=...)` / `DocumentQueries(document, kernel)`, `kernel.unavailable` without one. Default kernel wiring waits for OCCTKernel
    - `entities_in_box`: window = bounds inside the box; crossing = any outline point in the box (consistent with `entity_at_point`)
    - `check`: every Metric; bad expectations and unmeasurable metrics fail with the `Error` (`refs[1].entity`, `ids`, `tolerance`, ...). Bench now evaluates expectations (2 passed per case)
    - Flips one more shell test: `tests/app/test_selection.py::test_box_select_reports_the_missing_engine_piece`
  - [x] `MoveEntities`, `DeleteEntities` (branch `stream/core/move-delete`, stacked on `queries`, local)
    - Move skips annotations; delete cascades to distance and radial dimensions in the same delta. Ids deduplicated, labels "Move Rectangle" / "Delete 3 Entities", overflow and collapsed lines rejected
    - Property tests: moving everything shifts bounds and keeps dimension values; any delete reloads cleanly and undoes exactly. New golden replay fixture `tests/engine/fixtures/edits.*`
    - Flips one more shell test: `tests/app/test_selection.py::test_delete_reports_the_missing_engine_piece_instead_of_crashing` (5 in total; list below)
  - Shell tests pinned to engine gaps, all needing the `missing_*` treatment from issue #7 before the matching engine PR can go green: `test_canvas.py::test_paints_every_entity_kind`, `::test_without_point_queries_the_pointer_snaps_to_the_grid`, `::test_dimension_tool_reports_missing_point_picking` (feature-queries); `test_selection.py::test_box_select_reports_the_missing_engine_piece` (queries); `::test_delete_reports_the_missing_engine_piece_instead_of_crashing` (move-delete)
- User decisions (2026-09-15):
  - [x] Clicking inside a closed shape selects it (branch `stream/core/interior-picking`). Ranking: outline within tolerance first (nearest, then id), else the smallest enclosing circle/rectangle, then id. Crossing box selection includes insides too
    - Contract gap 2 resolved this way; `queries.py` docstring ("Nearest ... ties broken by id") needs rewording in the freeze PR
    - Shell impact (Stream B): `test_selection.py::test_click_selects_the_outline_and_empty_space_clears` and `::test_hover_follows_the_pointer_in_select_mode_only` assert inside-clicks miss, so they fail on this branch; a drag starting inside a shape now moves it instead of box-selecting
  - [x] Rectangle resize keeps the bottom-left corner fixed: already how ModifyEntity works (`test_changing_width_keeps_the_corner`). V1.5 solver default: pin that corner unless a constraint says otherwise
  - [x] Zoom-to-fit must not clip dimension labels. Engine `bounding_box` stays geometry-only, because `check`/bench BBOX metrics use it and text size is a rendering detail. Done shell-side in PR #15: Zoom to Fit measures labels at the fitted scale and fits again
- [x] `FilletCorner` (user request, 2026-09-15). Landed with the shell's Fillet tool in PR #15
  - Contract: `FilletCorner(a, b, radius, id=None)` in `commands.py`, so this is a joint change that needs Stream B's review; a `contracts/` branch is unrestricted by the boundaries check
  - V1 rounds a drawn corner: the two lines must already share an endpoint (exact match). Lines that would only meet if extended are rejected, per the user's decision
  - Math in `handlers.py`: tangent distance from dot/cross (exact on right angles), centre a radius inside the corner, both lines trimmed to the tangent points, arc added the short way round CCW. Undo is one automatic delta
  - Rejects: non-positive/non-finite radius, missing/malformed/duplicate/non-line ids, no shared endpoint, collinear lines, and a radius needing as much of a line as it has (a radius exactly equal to a line's length would leave a zero-length line)
  - Shell impact: Lucas's `test_palette.py::test_every_command_type_is_listed_or_deliberately_left_out` walks the `Command` union, so the palette must list or deliberately skip `fillet_corner`. That test fails on this branch until he does
  - Tests: exact right-angle values, all four shared-endpoint orders, undo/redo, resolved-command replay, a golden headless replay fixture, every error case, and a 1,000-example property test (tangency, sweep = 180° - corner angle, far ends kept). Verified the tests catch 5 deliberate breaks. Bench case `fillet-corner-10mm`
- [x] PR #15 (Lucas, 2026-09-17) landed the whole stack plus interior picking, FilletCorner, and the shell's V1 work in one joint PR, rebased to a linear 41 commits. On main with every extra installed: 679 passed, 1 failed (below); bench 6/6; the three replay fixtures are byte-identical
  - `tests/app/test_panels.py::test_area_isnt_offered_without_a_geometry_kernel` assumes no kernel is installed, so it fails wherever the occt extra is (CI's app job doesn't install it). Tested fix: make the test pin "no kernel" itself
  - PRs #12 (OCCT CI job), #13 (ADR 0007, pyproject correction, QML ban) and #14 (ADR 0003 Accepted) were closed by Lucas before #15 and weren't in it. Reopened, rebased, and merged 2026-09-17
- [x] Exit: freeze `commands.py`, `document.py`, `queries.py`, `errors.py` → split streams. PR #22 merged 2026-09-17
  - PR #22 did it as documentation only: both gap lists (core 1-10, shell 1-12) written into the four files, each marked frozen. No signatures or behaviour changed
  - Six gaps needed a real decision and are deferred with a recommendation each (now listed under Start here): `Error` returns for the pickers, normalizing `Arc.start_angle`, moving `LoadError` into contracts, an author on `Change`, a position `Metric`, a document revision counter
  - Issue #21 asks Lucas why #12-#14 were closed and carries the tested fix for the one test that fails outside CI (no reply as of 2026-09-21)

## V1 — Core

- [x] Document model: entities, IDs (no spatial index; revisit if hit-testing shows up in a profile). Dirty tracking lives in the shell's session
- [x] Commands: create/move/delete line, circle, rectangle, arc; modify; fillet. "Compound" is transactions, below
- [x] Command bus: validation, execution, automatic deltas, batch transactions, unrecorded mode (branch `stream/core/transactions`, local)
  - Net-delta commit, rollback (immediate, and never commits afterwards), exceptions roll back, nesting folds into the outermost (inner rollback reverts only its block), undo/redo inside a transaction raise
  - `merge_key`: one entry per run of same-key executes; dragging back to the start leaves none
  - Stateful hypothesis test over commands/undo/redo/merge keys/nested transactions; 1,000 extra histories run locally. No new shell test flips
  - Contract gap 9: committing a transaction changes `undo_label` but sends no `Change` (no COMMIT reason), so a shell undo menu can go stale until the next change
- [x] Undo/redo: bounded stack (count + bytes: `undo_bytes`, compact JSON size of each delta, newest entry always kept), display labels
- [x] `OCCTKernel` behind the `occt` extra; passes the shared conformance suite unchanged (branch `stream/core/occt-kernel`, local)
  - cadquery-ocp 8.0.1 (Apache-2.0) installs from the existing lock. First OCP import on this Mac took 28 s (cold), 0.3 s warm
  - Faces: polygon wire (rectangle) or circular edge (circle) → planar face; GProp area/centroid/inertia (products of inertia negated back); `AddOptimal` bounds; `BRepCheck` validity
  - Conformance property tests also pass at 3,000 examples each (verified 3,000 faces built). A test runs `area_properties` through `Bus(kernel=OCCTKernel())`
  - OCP has no type information; one `from OCP import (...)  # type: ignore[import-not-found, import-untyped, unused-ignore]` keeps mypy clean with and without the extra (checked both)
  - CI: the `occt (Linux)` job runs `tests/engine/geometry` against real OCCT on every PR (PR #12)
  - [x] Default kernel (user decision, 2026-09-15; branch `stream/core/default-kernel`): `caliper.engine.geometry.default_kernel()` returns OCCTKernel when the extra is installed, else None; cached, and only looked up by queries that need a kernel. `Bus(kernel=...)` / `DocumentQueries` default to it; `kernel=None` means none
- [x] Serialization: snapshot save/load, schema version, migration framework, history section off by default + stripped on export (branch `stream/core/files-cli`, local)
  - The migration framework already existed (chain, newer-file refusal, ordering test). Added a guard that every version below `SCHEMA_VERSION` has a migration
  - `snapshot.read` / `read_file` → `Snapshot(document, history, schema_version)`; `dumps`/`save` take `history=`; history is structure-checked only
- [x] Query API: every `Queries` method (the contract calls it `area_properties`, not `mass_properties`)
- [x] CLI: `python -m caliper.engine replay [--history] | inspect | export` (same branch). `export` = validate, migrate, write latest schema without history; stderr notes a removed history
- [x] Property-based tests on geometry invariants (branch `stream/core/properties`, local). Alongside the per-feature ones, `tests/engine/test_invariants.py` runs random sketches of every kind (creates, dimensions, edits, moves, deletes) through: file + history round trip, replay of resolved commands, exact undo/redo, feature snap round trip, antisymmetric distances vs `check`, move keeps sizes/dimension values/areas. Each also passed at 1,000 examples
  - Found and fixed a bug: replaying resolved creates (which carry allocated ids) left `next_id` behind, so a file's history didn't rebuild it byte-for-byte. An explicit `e{n}` id now advances `next_id` past n
- [x] Bench cases as features land (branch `stream/core/bench-cases`): `dimension-bottom-edge`, `move-right-30`, `delete-circle-with-dimension`. All 5 cases pass
  - Contract gap 10: `Metric` can't check an absolute position (e.g. a feature's x), so "moved 30 mm right" is only caught by the snapshot comparison, not by `check`. No `area` case yet: the bench would need the occt extra in CI
