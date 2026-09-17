Status: measured the canvas in a real 120 Hz Retina window (pan, zoom and edits drop frames at 2,000 entities), next: fix pan/zoom first (reuse the layer while the view moves), then the per-change panel work

# Shell workplan — Stream B

Owns `caliper/app/`, `tests/app/`, and this file. Builds against `caliper.contracts` and the in-memory engine; never imports a kernel.

Markers: `[ ]` not started · `[~]` in progress · `[x]` done

## Plan (2026-09-15)

### What the engine offers today (main @ `7ac0543`)

| Works | Raises `NotImplementedError` |
|---|---|
| every create command, `ModifyEntity`, undo/redo + labels, `subscribe` | `MoveEntities`, `DeleteEntities`, `transaction`, `merge_key` |
| `queries.bounding_box`, `queries.entity_at_point` | `feature_point`, `measure_distance`, `entities_in_box`, `nearest_feature`, `dimension_value`, `area_properties`, `check` |
| `io.snapshot.save` / `load` (raises `LoadError`) | |

**Rule for gaps:** the shell calls the contract API as if it were complete. Every call that can hit an unbuilt engine piece goes through one seam, `caliper/app/engine_gaps.py`. The seam catches `NotImplementedError` and puts "X isn't in the engine yet" in the status bar instead of crashing. No geometry math is copied into the shell. When Stream A lands a piece, the feature lights up without any shell change. Tests fake only these missing queries, and only in `tests/app/`.

### Module layout (`caliper/app/`)

| Module | Responsibility |
|---|---|
| `__main__.py` | `python -m caliper.app [file]` |
| `session.py` | `DocumentSession` (QObject): owns the `Bus`, file path, and dirty state (saved snapshot vs `bus.document`). Holds selection and hover ids (UI state). Emits `document_changed`, `selection_changed`, `file_changed`. New and Open create a fresh `Bus`, because undo never spans files |
| `engine_gaps.py` | The single `NotImplementedError` seam described above |
| `viewport/transform.py` | `ViewTransform`: model (Y-up mm) ↔ widget (Y-down px), pan, zoom about a point, fit to a box. Plain floats, Qt-free, so it's unit-testable. **The only place Y is flipped** |
| `viewport/grid.py` | Adaptive 1-2-5 grid spacing for the current zoom, plus grid snap (a UI convenience, not geometry) |
| `viewport/canvas.py` | `Canvas` (QWidget + QPainter): paints grid, axes, entities, hover/selection highlight, and the tool preview. Converts mouse events to model points before handing them to the active tool. Handles pan (middle-drag, space-drag, two-finger scroll) and zoom (wheel with Ctrl or pinch, about the cursor). Retina: float coordinates + cosmetic pens, so Qt's device pixel ratio scaling keeps lines sharp |
| `viewport/painter.py` | Draws each entity kind from its input parameters (arc = `drawArc` from center/radius/angles). Not geometry logic: it paints stored inputs |
| `tools/base.py` | `Tool` base: `press/move/release/key/cancel`, `preview(painter)`, `hint`. Each tool is its own state machine with an explicit `Phase` enum |
| `tools/controller.py` | `ToolController`: the tool-mode state machine (select ↔ line/circle/rectangle/arc/dimension). Esc cancels the in-progress operation first, then returns to select |
| `tools/select.py` | Click → `entity_at_point`; Shift toggles; empty click clears; drag a selected entity → preview offset → one `MoveEntities` on release; Delete → `DeleteEntities` |
| `tools/shapes.py` (line, circle, rectangle, arc) | Drag or click-click to draw; one create command on completion; zero-size input sends nothing. Arc: center → start → end (3 clicks, CCW) |
| `tools/dimension.py` | Two `nearest_feature` picks → `CreateDistanceDimension`; a circle or arc pick → `CreateRadialDimension` |
| `properties.py` | Dock panel: fields for one selected entity. Commits on `editingFinished` (so one edit is one undo step, with no `merge_key` needed). A `Rejected` result highlights the field named by `Error.field` and shows the message |
| `main_window.py` | Menus (File: New/Open/Save/Save As; Edit: Undo X/Redo X/Delete; View: Zoom to Fit; Tools), compact toolbar, properties dock, status bar (tool hint, cursor x/y in mm, messages). Asks before discarding unsaved changes. `LoadError` gets a dialog that lists each `Error` |
| `theme.py` | Dark Fusion palette and compact metrics. No gradients, rounded cards, or decorative icons |

### Build order (each a small commit)

1. `ViewTransform` + grid math + tests
2. Session + canvas + painter + main window shell (empty document renders, pan/zoom)
3. Tool controller + rectangle tool → **spike: draw**
4. Select tool + properties panel width edit → **spike: resize**
5. File menu save/open + dirty tracking → **spike: 120 survives reopen** (pytest-qt end-to-end test)
6. Line, circle, arc tools; undo/redo menu labels
7. Move/delete (through the gap seam), dimension tool + rendering (through the gap seam)
8. Theme polish, zoom-to-fit, status bar; screenshot review at 1x and 2x
9. Contract gaps list; update this file

### Out of scope (flagged, not built)

Numeric input while drawing, box selection (needs `entities_in_box`; gap seam only), constraints (V1.5), a settings/preferences layer, and a plugin/tool registry (the controller has a fixed V1 tool list).

## Phase 0.5 — Milestone spike (4-day timebox)

Built in `caliper/app/`, not a throwaway directory. Stream B inherits and rewrites it.

- [x] Window with a QPainter canvas (`python -m caliper.app [file]`)
- [x] Rectangle tool → `CreateRectangle` through the bus (drag or click-click; one command on completion)
- [x] Select → properties field → `ModifyEntity(width=120)` ("Undo Change Width")
- [x] Save → quit → reopen → width still 120. `tests/app/test_milestone.py` drives it with real input events, and checks the file bytes equal `snapshot.dumps`
- [x] Record contract gaps found (below)
- [ ] Exit: joint PR freezes `commands.py`, `document.py`, `queries.py`, `errors.py` → split streams

### Contract gaps (shell side, 2026-09-15) — for the freeze PR

Engine pieces the V1 shell calls but that raise `NotImplementedError` today, in the order the shell needs them. Each goes through `caliper/app/engine_gaps.py` and shows "X isn't in the engine yet":

1. `feature_point` + `nearest_feature`: feature snapping, the dimension tool, and drawing distance dimensions (the canvas shows "N dimensions not drawn")
2. `dimension_value`: dimension labels show "?" until then
3. `MoveEntities`, `DeleteEntities`: drag-to-move and Delete send the right command; the bus raises
4. `entities_in_box`: box selection
5. `merge_key`: not needed yet (the panel commits on Return/blur, so one edit is one undo step). Needed once value scrubbing exists

Contract wording to settle before freezing:

6. **`DistanceDimension.offset` sign is unspecified.** The shell uses positive = left of a→b. For HORIZONTAL/VERTICAL it's also unclear what the offset is measured from, so the dimension tool only creates ALIGNED for now
7. **Arc `start_angle` isn't normalized.** 0 and 360 are different stored inputs for the same arc, so two identical-looking documents compare unequal. The shell sends [0, 360). Decide: the engine normalizes, or the contract says callers must
8. **A no-op `execute` returns `Applied` but sends no `Change`.** The shell handles it; say so in the `CommandBus.execute` docstring
9. **Opening a file means a new `Bus`.** `CommandBus` has no load/replace, so the session swaps the bus and re-subscribes. That's probably right (undo never crosses files), but it should be stated
10. **No document revision.** The shell detects unsaved changes with `Document ==`, which is O(entities) per change. Fine for V1; a `revision` counter would be cheaper later. Non-blocking
11. **`LoadError` lives in `engine/io/canonical.py`,** not the contract, so every caller that opens files (shell, later the AI layer) imports an engine module to catch it
12. Agreeing with core gaps 2 and 5: `entity_at_point` is outline-only (clicking inside a rectangle is a miss, which is the SolidWorks convention and the shell relies on it), and `bounding_box` excludes annotations, so Zoom to Fit can clip dimension labels

### Built (2026-09-15)

| Area | Where | Notes |
|---|---|---|
| Transform + grid | `viewport/transform.py`, `viewport/grid.py` | Only place Y flips. Zoom about cursor, clamped; fit; adaptive 1-2-5 grid; grid snap rounds to the spacing's decimals |
| Canvas | `viewport/canvas.py`, `painter.py`, `annotations.py` | Cosmetic pens (sharp at 2x, verified on Cocoa at DPR 2.0). Trackpad pans, pinch or Cmd/wheel zooms, middle or Space-drag pans, Option suspends snapping. Grid lines batched per paint |
| Tools | `tools/` | Controller: `activate` cancels the operation first; Esc cancels the operation, then leaves the tool; right-click cancels. Each tool has an explicit `Phase` enum. A fast second click (Qt DblClick) counts as a press |
| Session | `session.py` | Owns bus, path, dirty state, selection, hover. Prunes deleted ids from the selection on every change |
| Properties | `properties.py` | Fields generated from the entity dataclass; `Rejected` highlights `Error.field`; enum fields are combo boxes; fixed width so selecting never shifts the canvas |
| Window | `main_window.py`, `theme.py` | File (New/Open/Save/Save As, unsaved-changes prompt, `LoadError` dialog listing each error), Edit (Undo/Redo with labels, Delete, Select All), View (Zoom to Fit F, Grid G, Snap), Sketch tools. Dark Fusion palette, text-only tool bar grouped by category |
| Tests | `tests/app/` (81) | pytest-qt offscreen. Mutation-checked: breaking the properties commit, rectangle corner normalization, or the Y flip fails 5 / 1 / 21 tests. Qt tests are skipped on the Linux core job (verified: 221 passed there with no Qt) |

### Debt to remove

- `caliper/app/engine_gaps.py` and the `CompletedQueries` test stand-ins in `tests/app/conftest.py` (they duplicate engine geometry) once Stream A lands the queries
- The drawing-in-progress overlay text can overlap geometry near the bottom-left corner
- No numeric input while drawing, no line chaining, no scrubbing fields (flagged, not V1-blocking)

## V1 — Shell

- [x] Main window: Mac-first, dark, compact chrome, canvas-dominant
- [~] Viewport: pan, zoom, grid, grid snapping, Retina-correct done. Feature snapping waits on `nearest_feature`. 120 Hz not profiled yet
- [x] Tool modes as a state machine: select, line, circle, rectangle, arc (+ dimension)
- [~] Selection, hover highlight done; drag-to-move and delete send the right commands but wait on the engine
- [x] Properties panel for dimensions (one edit = one undo step, no merge_key needed yet)
- [x] Undo/redo wired to the command bus, with command display labels
- [x] File menu: new, open, save, save as
- [x] pytest-qt coverage for tool-mode transitions and input handling
- [x] Profile paint time with ~2,000 entities at 120 Hz (2026-09-17, real window; see "Real-window performance" below)

## Next: an interface that shows the verification loop (plan, 2026-09-15)

Proposed, awaiting the user's approval. Nothing below is built.

### What the research says

| Tool | What to take | What to avoid |
|---|---|---|
| SolveSpace | Double-click a dimension on the canvas to type a new value; a "type the value right after placing a dimension" option. Constraints drawn on the geometry, not in a list | A text-window property browser that feels like a terminal |
| Dune 3D | Non-modal sketching (no separate "sketch mode" to enter and leave); one interactive tool system for every operation | Reusing EDA chrome that isn't tuned for mechanical work |
| FreeCAD | Its own community names the pain: steep learning curve, inconsistent workflows across workbenches, a task panel that changes shape per tool | Modal workbenches; long constraint lists as the main way to understand a sketch |
| Plasticity (commercial) | Keyboard-first: a command palette on one key that finds every tool and setting by name | Hotkey-only discoverability |
| Zoo Design Studio (open source) | Every click edits the same underlying model a program (their KCL code) or the AI can edit; the AI result stays editable parametric geometry, not a frozen mesh | Making code the thing users must read to understand the model |
| Onshape AI Advisor, Fusion Assistant | Proof incumbents are adding assistants: Onshape's is a help chatbot; Fusion's runs simple commands from text and "gets confused by anything ambiguous" | A chat box bolted beside the model with no way to check what the AI did |

**The opening:** incumbent AI in CAD is a chat panel next to the model. Nobody shows the *verification loop*: what the agent changed, what it measured, whether requirements pass. That's Caliper's wedge, and it's a UI problem as much as an engine one.

### Design principles for the shell

1. **Requirements are visible.** A Checks panel lists expectations ("width = 120 ± 0.001") with live pass/fail and the measured value, re-evaluated on every change. Measuring something should be one click away from turning it into a check.
2. **Every change has an author.** The history shows each command with who made it (you, a script, the agent), because commands are the only door.
3. **The agent proposes; the user disposes.** Agent work arrives as a reviewable proposal: ghost geometry (added, changed, removed), the commands it will run, and the checks before and after. Accept applies it; reject leaves no trace. No chat bubbles: a prompt bar and a proposal card.
4. **The palette is the tool schema.** Cmd+K searches the same command set the AI uses, with typed parameter entry. Humans and the agent learn one vocabulary.
5. **Precision without dialogs.** Type dimensions while drawing (Tab between fields), double-click any dimension on the canvas to edit it, and show errors next to the geometry with their error code, never as a modal.
6. **Non-modal and calm.** No sketch mode to enter. One accent colour for selection and one for agent proposals; state is shown by shape and badge, not only colour.

### Phases (Stream B)

| Phase | Goal | Depends on Stream A / contract |
|---|---|---|
| **P1: Land and unblock** | PR #3 merged; freeze-PR gaps settled; engine pieces wired as they land; `engine_gaps.py` and the test stand-ins deleted | Point queries, move/delete, `dimension_value` |
| **P2: Design system** | Tokens module (colour, type, spacing, motion), monoline SVG icon set via QtSvg (functional, not decorative), SF Pro/system type scale, screenshot baselines for regressions | None |
| **P3: Precision input** | Heads-up numeric entry, on-canvas dimension editing, inference guides (horizontal/vertical alignment to nearby points), measure tool, Cmd+K palette, full keyboard map | `nearest_feature`, `feature_point`, `measure_distance` |
| **P4: Structure panels** | Model browser (entity list synced with selection), History timeline with author, Checks panel | `check`; **contract decisions:** where expectations are stored, and an author/source on `Change` |
| **P5: Performance** | Paint time measured at 2,000 and 10,000 entities; move the canvas onto QOpenGLWidget or cached layers only if the numbers say so; 120 Hz on ProMotion | Spatial index if hit-testing shows up in the profile |
| **P6: AI-native surfaces** | Prompt bar, proposal review (ghost diff, accept/reject), the agent's attempts shown against checks ("✗ 100.0 → ✓ 120.0"), all driven first by a scripted stand-in agent so the UI doesn't wait for V3 | Transactions (one undo step per accepted proposal); an owner for `caliper/ai/` |
| **P7: Constraints (V1.5)** | Constraint glyphs on geometry, degrees-of-freedom colouring (under/fully/over-constrained), conflicts highlighted where they occur | planegcs (ADR 0003) and the constraint contract |

### The next goal, step by step: P2 + P3, "CAD-grade and keyboard-fast"

Progress (2026-09-15): steps 1-9 done; step 10 (review pass) in progress. Step 1 found `ink_dim` below 4.5:1 contrast (4.26) and raised it to 5.14. Step 5 edits rectangle width/height and circle/arc radius; editing dimension labels waits on `dimension_value` and `feature_point`.

Deviations from the plan, and why:
- Step 6 guides use points the pointer has *hovered* (acquired, up to 4), because queries can't list every feature on screen. This matches Fusion/SketchUp and needs no contract change.
- Step 7 lists every command whose fields are numbers, points, or the selection. `CreateDistanceDimension`, `CreateRadialDimension` (need feature references) and `ModifyEntity` are left out on purpose; a test pins that list.
- Step 8 has no "Keep as check" button yet: `check` isn't in the engine and where expectations live is undecided (P4).
- Steps 6 and 8 only work in the real app once `nearest_feature`, `feature_point`, and `measure_distance` land; they're tested against the stand-ins in `tests/app/conftest.py`.

Chosen because it needs almost nothing new from Stream A and every later phase builds on it.

1. [x] **Tokens and theme module.** Replace colour literals in `theme.py` with named tokens (surface, ink, accent, agent, pass, fail); a type scale using the system font; spacing steps. *Done when* no widget or painter uses a hex literal (grep-enforced in a test).
2. [x] **Icon set.** About 20 monoline SVG icons at 16/20 px for tools and panels, loaded through QtSvg and tinted from tokens; the tool bar shows icon plus label, collapsing to icon-only when narrow. *Done when* icons are sharp at 1x and 2x (screenshot check on Cocoa).
3. [x] **Screenshot baselines.** A pytest-qt harness renders the window in fixed states (empty, drawing, selection, error) and compares against stored images with a tolerance. *Done when* a deliberate 1 px layout shift fails the test.
4. [x] **Heads-up numeric entry.** While drawing, typing a number opens small fields next to the cursor (Rectangle: width, Tab, height; Circle: radius; Line: length, Tab, angle). Enter commits one command. *Done when* R → 120 Tab 50 Enter creates exactly one `CreateRectangle` of 120 × 50 at the clicked corner.
5. [x] **On-canvas dimension editing.** Double-click a dimension label or a rectangle edge to edit its value in place; Enter sends `ModifyEntity`; `Rejected` shows the message under the field. *Done when* double-click → 120 → Enter matches today's properties-panel path, including the undo label.
6. [x] **Inference guides.** Dashed horizontal/vertical guides when the pointer lines up with a nearby feature point, snapping on that axis. *Done when* the pointer snaps to x of a corner within tolerance and the guide is drawn (needs `feature_point`; built against the stand-ins until it lands).
7. [x] **Command palette.** Cmd+K lists every tool and action by name with its shortcut; typing filters; commands with parameters open typed fields generated from the command dataclass (the same fields an AI tool call would fill). *Done when* Cmd+K → "rect" → 120, 50 → Enter creates the rectangle, and the palette's list is derived from the `Command` union, not hand-maintained.
8. [x] **Measure tool.** Click two points to see distance, dx, and dy in a small overlay; a "Keep as check" button is visible but disabled until P4. *Done when* measuring two corners shows 120.000 (needs `measure_distance`).
9. [x] **Keyboard map and discoverability.** Every action has a shortcut shown in menus, tooltips, and the palette; a Help → Keyboard Shortcuts sheet is generated from the actions. *Done when* a test asserts no two actions share a shortcut.
10. [x] **Review pass.** Real-window screenshots at 1x and 2x, both themes if a light theme is in scope; update this workplan; open the PR with a summary.

### Contract and repo items to raise (not Stream B's to change)

- **Author on changes:** `Change`/`Applied` carry no source (user, script, agent). Needed for the History panel and proposal review.
- **Where expectations live:** in the document (travels with the part, like drawing requirements) or in a sidecar. It's an ADR-sized decision.
- **Previewing without committing:** the shell can preview a proposal on a scratch `Bus(document)` and replay it on the real bus to accept, using only public API. Verified 2026-09-15: replaying the commands *as submitted* gives a byte-identical document, but replaying the *resolved* commands (with ids filled in) does not (see the next item). Accept only if the real document still equals the one the preview started from; otherwise re-run the proposal. Transactions would make an accepted proposal one undo step.
- **Engine finding for Stream A: replaying resolved commands desyncs `next_id`.** `commands.py` says the resolved command in `Applied` "is what replay and the transcript record". But a create command with an explicit id doesn't advance `Document.next_id`: a live session saves `next_id: 2` after one rectangle, while replaying its resolved command saves `next_id: 1`. The files differ, so a recorded transcript doesn't replay byte-identically. Allocation skips taken ids, so nothing collides. Repro: `Bus().execute(r.command)` where `r` is the `Applied` from a fresh bus, then compare `document.next_id`. Either explicit `e<n>` ids should bump `next_id`, or transcripts should record the submitted command.
- **Who owns `caliper/ai/`:** neither stream does today, but P6 needs an agent to drive.
- **Branch naming:** git can't create `stream/shell/<topic>` while a `stream/shell` branch exists, so the documented topic-branch convention doesn't work as written. Proposal: topic branches as `stream/shell-<topic>`, with the boundaries script updated on a `shared/` branch.

## Phases P1–P7 on the complete engine (2026-09-15)

Built on a local integration branch, `integ/shell-on-engine` = `stream/shell` + `origin/stream/core/properties` (the top of Stream A's stack). Shell commits touch only `caliper/app/`, `tests/app/`, and this file, so they rebase onto `main` once the engine merges. Full suite on that branch: 602 passed, 15 skipped.

- [x] **Issue #7** (on `stream/shell`, PR #5): tests that pinned engine gaps now simulate them with `@pytest.mark.bus(missing=...)`. Verified: 533 pass on the engine stack.
- [x] **P1 Land and unblock:** `engine_gaps.py` and every test stand-in deleted; move, delete, box select, point snapping, measure, and dimension values run on the real engine. Horizontal and vertical dimensions render with the one-CCW-rule convention (`caliper/app/dimension_layout.py`), and the Dimension tool picks orientation from placement.
- [x] **P4 Structure panels:** Sketch browser (grouped, natural id order, selection sync, double-click frames), History (author chips, undone entries dimmed, click to go back), Checks (live pass/fail via `queries.check`, add from the selection or the last Measure, Delete removes). Area is offered only when the engine can evaluate it (it needs a kernel the shell may not load).
- [x] **P5 Performance** (offscreen, median): the canvas caches grid, geometry, and dimensions in a pixmap; hover, selection, and previews paint on top. The browser updates from each `Change` instead of rebuilding.

  | Entities | Repaint | Pointer move | One edit |
  |---|---|---|---|
  | 2,000 | 9.4 → 0.2 ms | 3.8 ms | 43 → 34 ms |
  | 10,000 | 42 → 0.2 ms | 19 ms | 154 → 75 ms |

  Pointer move at 10,000 is the engine's linear `nearest_feature` + `entity_at_point` (profiled: 0.53 s of 20 moves inside `engine/queries.py`). A spatial index is Stream A's call.
- [x] **P6 AI-native surfaces:** prompt bar (⌘L), proposal card (plan, commands, checks before and after, broken user checks), ghost geometry, Accept (⌘Return) as one transaction credited to Agent, Reject (Esc). Proposals run on a scratch `Bus(document)` and replay the submitted commands; accepting a stale proposal is refused. Driven by `caliper/app/agent/scripted.py`, a labelled stand-in that understands the phrasings in `EXAMPLES`; the review flow is what a V3 model would use.
- [~] **P7 Constraints:** blocked on the contract. The shell's needs are proposed in #18 (2026-09-17); planegcs acceptance is on `shared/adr-0003-accepted`.

### Gaps found (for Stream A / the contract)

1. **`Change` has no author.** The shell stamps You/Agent itself in `DocumentSession`; a script calling the bus directly isn't attributed.
2. **A committed transaction sends no notification with its label.** Views only see each inner command; the shell refreshes undo labels on its own history signal.
3. **Where checks live** is still undecided; they're session state, cleared by New/Open.
4. **Spatial index** for hit-testing and snapping past about 5,000 entities (numbers above).
5. **Dimension offset rule** is implemented in the shell as proposed on PR #5 and not yet in the contract docstring.

### Found and fixed along the way

- `ChecksPanel.metric` shadowed `QWidget.metric()`, crashing any render of the panel.
- After a transaction the Edit menu kept the previous undo label.
- The app-test fixture re-applied the stylesheet per test (61 s → 11 s suite).

## Issue #9 (interior picking, drag-select, Zoom to Fit labels), 2026-09-15

Done on `integ/shell-on-engine` with `origin/stream/core/interior-picking` merged in; 614 pass.

- [x] **Two selection tests updated** for interior picking, plus a third of mine (`test_box_select_right_to_left_also_selects_what_it_touches`) whose drag started inside the circle and now moved it. Workplan item 12 above is superseded: clicking inside a closed shape selects it.
- [x] **Drag-select decision: keep the Fusion/SolidWorks behaviour** (a drag starting inside a closed shape moves it) **and add ⌘-drag to force a box**, since Shift (add), Option (suspend snapping), and Space (pan) are taken. The Select hint and the shortcut sheet say so.
- [x] **`editable_field` takes a tolerance**, so double-clicking the middle of a rectangle no longer opens Width or Height by accident; it must be within the pick radius of an edge (or, for a circle or arc, of the rim).
- [x] **Zoom to Fit pads for dimension labels** (`Canvas._with_label_extents` + `annotations.label_anchors`): fit the geometry, measure each visible label at that scale, then fit once more. `bounding_box` stays geometry-only, as Stream A decided.

## Issue #11 · FilletCorner in the app (2026-09-15)

Built on local branch `integ/fillet` = `origin/contracts/fillet-corner` + my 9 shell commits + `origin/stream/core/interior-picking`. 659 pass.

**Palette decision (his question 2):** `FilletCorner` is listed, and takes its two lines from the selection, like Move and Delete. `command_schema` now understands single `EntityId` arguments: a command whose fields are entity ids fills them from the selection in id order, and `CommandSpec.wants` says how many must be selected. The palette refuses with "Fillet Corner needs exactly 2 selected, not 1" rather than opening a form that can't work.

**Fillet tool** (`caliper/app/tools/fillet.py`, key **O**): click one line, click the other, type a radius, Return. The preview is not shell geometry: the tool runs `FilletCorner` on a scratch `Bus(document)` and draws what the engine returns, so the rounded corner and both trimmed lines are exactly what accepting will produce. A radius that doesn't fit shows the engine's own message ("the radius must stay under 80.0") and sends nothing. Picked lines are drawn in the selection colour, the result in the preview colour.

Items 1 and 3 of #11 were already done earlier today (interior picking test updates + ⌘-drag, and Zoom to Fit label padding); see the section above.

## The joint PR (2026-09-15)

`contracts/v1-complete` = Stream A's 10 stacked engine branches + `contracts/fillet-corner` + `stream/core/interior-picking` + Stream B's V1 shell work + the contract docstrings interior picking needed. 664 pass, 16 skipped; ruff, format and mypy clean; boundaries allows it as a `contracts/` branch.

It is one PR because three of the pieces cannot land separately:

1. **Interior picking** changes `entity_at_point`, which flips shell tests either way round: whoever merges first turns the other red (Andre's analysis in issue #11).
2. **`FilletCorner`** adds a `Command`, so the palette test that walks the `Command` union fails until the shell lists it.
3. The rest of Stream B's work uses move, delete, transactions, the full query API and `check`, none of which are on `main`.

Shell work added on top of the engine in this PR:

- **Fillet tool** (key **O**) with a preview computed by the engine on a scratch bus, and `FilletCorner` in the palette taking its two lines from the selection.
- **Optional file history:** File → Include History in Saved Files writes the resolved commands that built the drawing (off by default, per ADR 0005); opening such a file says how many steps it carries. Undone steps are not written.
- Everything recorded in the sections above: browser, history, checks, the agent review flow, performance work, interior picking updates with ⌘-drag, and Zoom to Fit label padding.

Not included, deliberately: `shared/adr-0003-accepted`, `shared/occt-ci`, and `shared/qt-gpl-bundling`, which are Stream A's maintainer changes and independent of this.

## After the merge (2026-09-17)

- [x] **#15 merged** as `19757b9` with Rebase and merge: 41 linear commits, tree identical to the approved head. GitHub refuses to rebase a branch holding merge commits, so the branch was flattened first (one `core.md` status conflict, resolved as the original merge had) and CI re-ran green. On `main`: 664 passed, 16 skipped (OCCT extra not installed); ruff, format, mypy clean. The app tests need `QT_QPA_PLATFORM=offscreen` locally, as CI sets.
- [x] Issue #11 closed. The branches #15 subsumed are deleted.
- [x] **Contract gaps filed.** #16 covers where checks live: an ADR, recommending expectations in the document, changed by commands. #17 groups the rest: author on `Change`, no notice when a transaction commits, the dimension offset rule, the spatial index, and absolute-position metrics. The "Gaps found" list above now lives in those issues.
- [~] **P7 contract proposal (#18):**
  - Constraints as one entity dataclass per kind, with no placement field.
  - `value: float | None` makes a dimension driving.
  - A `solve_status` query.
  - Conflicts come back as `Rejected` with `constraint.conflict` and a new `Error.ids`.
  - A `DragFeature` command.
  - Solved positions stored in the file.
  - Also flags that `vision.md` puts driving dimensions in V2 while ADR 0003 and the `DistanceDimension` docstring say V1.5.
- [ ] Next, once #18 settles decisions 1, 4, and 5: build the constraint glyph layer, the DoF colouring tokens, and the constraint palette entries against stand-ins.
- [ ] Next, once #16 is decided: the Checks panel reads from the document, and adding or removing a check goes through the bus.

## Real-window performance (2026-09-17)

The P5 numbers were measured offscreen at device pixel ratio 1, and "repaint 42 → 0.2 ms" was the *cached* repaint only. New `tests/app/bench_canvas.py` (not collected by pytest) opens the main window and times what a user actually causes. Machine: M2 Pro MacBook Pro, Liquid Retina XDR, 120 Hz, ratio 2, window 1440x900. Sketch: a grid of rectangles, circles, lines, and arcs with 1 in 20 a horizontal dimension. Medians of 40 runs. Times are CPU time to handle the event and paint into Qt's backing store, not display latency.

| Frame (budget 8.33 ms at 120 Hz) | 2,000 offscreen | 2,000 real | 10,000 offscreen | 10,000 real |
|---|---|---|---|---|
| Cached repaint | 0.34 | 3.36 | 0.38 | 4.79 |
| Pointer move (handler + repaint) | 5.27 | 6.93 | 24.75 | 26.11 |
| Pan frame | 14.90 | **25.55** | 69.90 | **80.37** |
| Zoom frame | 15.06 | **22.33** | 69.65 | **77.74** |
| One edit (command + repaint) | 41.06 | **51.73** | 112.14 | **122.75** |

Reproduce: `uv run python tests/app/bench_canvas.py`, and again with `QT_QPA_PLATFORM=offscreen`.

**What costs what at 2,000, real window** (checked by hiding panels and removing dimensions, not only by profiling):

| Setup | Edit command | Edit repaint | Pan frame |
|---|---|---|---|
| Panels shown, with dimensions | 10.72 | 37.73 | 23.57 |
| Panels hidden, with dimensions | 4.20 | 17.39 | 17.94 |
| Panels shown, no dimensions | 9.46 | 31.35 | 17.56 |
| Panels hidden, no dimensions | 3.23 | 15.83 | 16.27 |

- **Pan and zoom rebuild the whole static layer every frame.** `_static_layer` keys on scale and origin, so the cache never survives a view change. Redrawing about 1,900 shapes into a ratio-2 pixmap is about 16 ms: twice the budget, with no panels and no dimensions.
- **The side panels cost about 6 ms of every edit's command and about 20 ms of its repaint.** cProfile points at `SketchBrowser._apply` → `_fill` (about 100 rows and about 200 `setText` calls per edit) plus panel painting flushed in the same window sync. In the real app, `update()` coalescing may hide part of the repaint share; `bench_canvas.py` calls `repaint()` to time synchronously.
- **Dimensions are cheap:** 100 of them add about 1.6 ms. cProfile claimed 11 ms; its per-call overhead inflates call-dense Python.
- **Pointer moves** fit at 2,000 (6.9 ms) and miss at 10,000 (26 ms). That's the engine's linear hit-testing, the same both ways, so the spatial index (#17) only matters near 10,000.
- **Cached repaint** (blitting the ratio-2 layer) takes 3.4 to 4.8 ms: about half a 120 Hz frame before anything else is drawn.

Fix order (Stream B, not started):
- [ ] **Pan and zoom:** keep the last layer while the view moves; translate it for pan and scale it for zoom, then rebuild once the view settles. Target: a pan frame near the cached-repaint cost.
- [ ] **Panels:** update only the rows a `Change` touches, not every dimension row; measure again to see how much of the repaint share goes with it.
- [ ] **Layer rebuild:** batch the draw calls (`drawLines` and `drawRects` arrays, or one path per pen) instead of one Python→Qt call per entity. Measure with `bench_canvas.py`, panels hidden.
