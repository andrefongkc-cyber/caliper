Status: waiting on Lucas's review of PR #1 (Phase 0), next: merge, then the Phase 0.5 spike

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
- [~] Step 8: Check in with the user, then open PR `phase-0/foundation` → `main`
  - [x] CODEOWNERS: Stream A @andrefongkc-cyber, Stream B @lucassnam
  - [x] `app (macOS)` runs on every PR and is a required check (public repo: macOS runners are free)
  - [x] Rewrote all 31 commits to the GitHub no-reply address (trees and dates unchanged); repo-local `user.email` set
  - [x] Installed `gh` 2.100.0 (checksum verified); user signed in via device flow (scopes repo, workflow)
  - [x] Created empty public repo https://github.com/andrefongkc-cyber/caliper; `origin` set; invited @lucassnam (write)
  - [x] Pushed `main` and `phase-0/foundation`
  - [x] Branch protection on `main`: 1 approval + code owners, stale approvals dismissed, required checks `core (Linux)` / `app (macOS)` / `boundaries` (strict), linear history, admins included
  - [x] Opened https://github.com/andrefongkc-cyber/caliper/pull/1; fixed CI setup (pinned setup-uv to a commit, disabled pytest-qt in the licenses job). All four checks green
  - [ ] Lucas (collaborator, invite accepted) reviews and approves → merge

## Phase 0.5 — Milestone spike (4-day timebox; shell side in shell.md)

- [ ] planegcs build test on Apple Silicon (Xcode + eigen + boost). Result reshapes ADR 0003
- [ ] Verify `pyside6-essentials` contains no GPL-only modules (ADR 0006)
- [ ] Exit: milestone works end to end → freeze `commands.py` + `document.py` → split streams

## V1 — Core

- [ ] Document model: entities, IDs, dirty tracking (no spatial index; revisit at >2,000 entities or hit-testing in a profile)
- [ ] Commands: create/move/delete line, circle, rectangle, arc; modify dimension; compound
- [ ] Command bus: validation, execution, automatic deltas, batch transactions, unrecorded mode
- [ ] Undo/redo: bounded stack (count + bytes), display labels
- [ ] `OCCTKernel` behind the `occt` extra; passes the shared conformance suite
- [ ] Serialization: snapshot save/load, schema version, migration framework, history section off by default + stripped on export
- [ ] Query API: `measure_distance`, `bounding_box`, `entity_at_point`, `mass_properties`
- [ ] CLI: `python -m caliper.engine replay | inspect | export`
- [ ] Property-based tests on geometry invariants
- [ ] Bench cases as features land
