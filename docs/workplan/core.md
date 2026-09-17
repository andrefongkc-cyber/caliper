Status: V1 on main; freeze PR #22 and the restored maintainer PRs #12-#14 open for Lucas, next: the six deferred contract decisions once the freeze lands

# Core workplan — Stream A

Owns `caliper/engine/`, `bench/`, `tests/` (except `tests/app/`), and this file. `caliper/contracts/` changes go through a joint PR once frozen (after Phase 0.5).

Markers: `[ ]` not started · `[~]` in progress · `[x]` done

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
- [~] Contract gaps the spike surfaces (engine side so far; Lucas adds the shell side). For the freeze PR:
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
  - [~] `feature_point` + `nearest_feature` (snapping, dimension tool, drawing distance dimensions). Branch `stream/core/feature-queries`, not pushed
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
  - [~] Zoom-to-fit must not clip dimension labels. Engine `bounding_box` stays geometry-only, because `check`/bench BBOX metrics use it and text size is a rendering detail; see the recommendation for the shell
- [x] `FilletCorner` (user request, 2026-09-15). Landed with the shell's Fillet tool in PR #15
  - Contract: `FilletCorner(a, b, radius, id=None)` in `commands.py`, so this is a joint change that needs Stream B's review; a `contracts/` branch is unrestricted by the boundaries check
  - V1 rounds a drawn corner: the two lines must already share an endpoint (exact match). Lines that would only meet if extended are rejected, per the user's decision
  - Math in `handlers.py`: tangent distance from dot/cross (exact on right angles), centre a radius inside the corner, both lines trimmed to the tangent points, arc added the short way round CCW. Undo is one automatic delta
  - Rejects: non-positive/non-finite radius, missing/malformed/duplicate/non-line ids, no shared endpoint, collinear lines, and a radius needing as much of a line as it has (a radius exactly equal to a line's length would leave a zero-length line)
  - Shell impact: Lucas's `test_palette.py::test_every_command_type_is_listed_or_deliberately_left_out` walks the `Command` union, so the palette must list or deliberately skip `fillet_corner`. That test fails on this branch until he does
  - Tests: exact right-angle values, all four shared-endpoint orders, undo/redo, resolved-command replay, a golden headless replay fixture, every error case, and a 1,000-example property test (tangency, sweep = 180° - corner angle, far ends kept). Verified the tests catch 5 deliberate breaks. Bench case `fillet-corner-10mm`
- [x] PR #15 (Lucas, 2026-09-17) landed the whole stack plus interior picking, FilletCorner, and the shell's V1 work in one joint PR, rebased to a linear 41 commits. On main with every extra installed: 679 passed, 1 failed (below); bench 6/6; the three replay fixtures are byte-identical
  - `tests/app/test_panels.py::test_area_isnt_offered_without_a_geometry_kernel` assumes no kernel is installed, so it fails wherever the occt extra is (CI's app job doesn't install it). Tested fix: make the test pin "no kernel" itself
  - PRs #12 (OCCT CI job), #13 (ADR 0007, pyproject correction, QML ban) and #14 (ADR 0003 Accepted) were closed by Lucas before #15 and are not in it; all three still apply cleanly
- [~] Exit: freeze `commands.py`, `document.py`, `queries.py`, `errors.py` → split streams
  - PR #22 drafts it as documentation only: both gap lists (core 1-10, shell 1-12) written into the four files, each marked frozen. No signatures or behaviour changed
  - Six gaps need a real decision and are deferred with a recommendation each: `Error` returns for the pickers, normalizing `Arc.start_angle`, moving `LoadError` into contracts, an author on `Change`, a position `Metric`, a document revision counter
  - Reopened and rebased #12 (OCCT CI job), #13 (ADR 0007 + pyproject), #14 (ADR 0003 Accepted); issue #21 asks Lucas why they were closed and carries the tested fix for the one test that fails outside CI

## V1 — Core

- [ ] Document model: entities, IDs, dirty tracking (no spatial index; revisit at >2,000 entities or hit-testing in a profile)
- [~] Commands: create/move/delete line, circle, rectangle, arc; modify dimension (done). "Compound" is transactions, below
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
  - **For maintainers:** no CI job installs the `occt` extra and runs tests, so OCCT conformance only runs locally. Proposal: add `--extra occt` to `core.yml` or a small `occt` job running `tests/engine/geometry`
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
