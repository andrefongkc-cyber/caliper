Status: waiting on Andre's review of PR #3 (milestone + V1 shell, CI green; merge with Rebase and merge), next: Stream A lands point queries + move/delete, then the contract-freeze PR

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
- [ ] Profile paint time with ~2,000 entities at 120 Hz
