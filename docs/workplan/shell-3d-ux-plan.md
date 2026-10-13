Status: APPROVED by Lucas 2026-10-13, all six decisions as recommended. Part 1 is being built on `shared/3d-ux-fixes`; progress is tracked in [shell.md](shell.md), not here. Audited 2026-10-12 on `shared/proposal-in-3d` @ `12d9bd1` (= `main` @ `d19ea2b` + PR #58's 13 commits).

# Shell plan: 3D UX fixes, CAD-grade navigation, and an agent auto mode

Stream B. Everything in Parts 1 to 3 stays inside `caliper/app/`, `tests/app/`, and `docs/workplan/shell.md`. Part 4 lists what needs Andre or a contract change; none of it is built by this plan.

Markers: `[ ]` not started · `[~]` in progress · `[x]` done. Sizes: S = under half a day, M = about a day, L = several days.

## 0. What "fetch the review changes" found

- **PR #58 is approved by Andre (2026-10-04) with no comments, inline or otherwise.** There are no review changes to implement.
- #57 merged, so #58 on GitHub is `CONFLICTING`: its remote branch still carries #57's 24 pre-rebase commits. **The local branch in this worktree is already rebased** onto `origin/main` (0 behind, 13 ahead). It needs a force-push, which agents can't do here:
  ```bash
  cd ~/Desktop/Active/caliper-ui
  git push --force-with-lease origin shared/proposal-in-3d
  ```
  then "Rebase and merge" on GitHub. A force-push can dismiss the approval if the repo has "dismiss stale reviews" on; if so Andre re-approves.
- Local `main` in `~/Desktop/Active/caliper` was 69 commits behind and is now fast-forwarded to `d19ea2b`. `coupler.caliper` and `gearv1.caliper` there are untouched (untracked).
- `shell.md`'s `Status:` line is stale (it still says "awaiting Andre" and "rebase once #57 merges"). Fixed in the first commit of the new work.
- #58's manual pass in the real window and Claude Desktop is still open on Lucas's side. Nothing below replaces it.

## 1. How this was audited

Read: `main_window.py`, `viewport/view3d.py`, `camera3d.py`, `scene3d.py`, `extrude.py`, `panels/features.py`, `agent/ui.py`, `agent/mcp_host.py`, `agent/proposal.py`, `shortcuts.py`, and the contract (`commands.py`, `document.py`, `queries.py`, `kernel.py`).

Then rendered the real `MainWindow` offscreen (1440×900, its own QSettings scope so saved folds weren't touched) on `bench/cases/extruded-plate-milestone/expected.caliper` in nine states: empty part, part loaded, extrude selected, sketch open in 3D, Extrude form, agent prompt, palette, folded, 2D tab. Several findings below came only from the pictures, not the code.

**The limit that shapes everything:** the contract has two part features (`Sketch`, `Extrude`) and the kernel has `extrude`, `union`, `cut`, `volume`, `mesh`. There is no fillet, chamfer, revolve, hole, shell, pattern, edge reference, feature rename, suppress, reorder, or rollback. Fusion/Onshape feature parity is an engine job, not a shell job. What the shell can do now is make navigation, selection feedback, and previews CAD-grade, because every later feature inherits them.

## 2. Part 1: fixes (found in the audit)

Ordered by how much they hurt. Evidence is `file:line` or the screenshot state.

- [ ] **F1. Trackpad navigation in 3D doesn't match the 2D canvas.** (M) `view3d.py:359-367` reads only `angleDelta().y()` and zooms. The canvas pans on two-finger scroll, zooms on pinch and ⌘+scroll (`canvas.py:746-790`). So on a trackpad, two-finger scroll means pan in a sketch and zoom in 3D, pinch does nothing in 3D, and there's no trackpad pan at all without a right-drag. Mac-first (ADR 0004). Fix: two-finger scroll pans, pinch and ⌘+scroll zoom about the pointer, Shift+two-finger orbits (Fusion's Mac map); a wheel mouse (no `pixelDelta`) still zooms. **Changes what two-finger scroll does in 3D today: decision 5.**
- [ ] **F2. After Finish, the 3D view is left staring flat at the sketch plane.** (S) `_close_sketch` copies the backdrop's camera into `view3d` (`main_window.py:1388-1391`), so Extrude opens on a plan view where depth can't be seen at all (screenshot: Extrude form). Fix: remember the camera from before the sketch opened and return to it on Finish and Cancel, animated (V2 below; a jump until then). The docstring states today's behaviour as intended and no test pins `view3d.camera` after Finish: **decision 3.**
- [ ] **F3. Extrude has no preview.** (M) The depth is typed blind and a refusal shows only after Return (`extrude.py:122-138`). Fix: run `CreateExtrude` on a scratch `Bus(document)` as the fields change (debounced), mesh the result, and draw it the way C-16 draws a proposed solid, in the preview colour not the agent's. Refusals (open profile, zero depth) show live under the fields. Known cost from C-16: about 40 to 54 ms once for 2,604 triangles, then cached by the engine.
- [ ] **F4. Selecting an extrude doesn't show it in 3D.** (S) `Scene.paint` takes `selected` but `_solid` never reads it (`scene3d.py:150-153`, `180-233`); only a picked face is tinted. Click "Extrude 1" in the Part panel and the solid doesn't change (screenshot: extrude selected). Fix: tint the selected extrude's faces through `queries.faces(id)` and the existing `face_triangles`.
- [ ] **F5. Nothing highlights under the pointer in 3D.** (M) No mouse tracking, no hover state (`view3d.py`, `scene3d.py`: no match for hover). Pre-highlight is how Fusion and Onshape tell you what a click will pick. Fix: hover a face, plane, or sketch, one pick per frame at most (a face pick is 5.4 ms benchmarked), drawn in `theme.HOVER`.
- [ ] **F6. The origin planes dominate the part.** (S) They scale with the part (`Scene.half`) and are filled, so on the milestone plate the Top plane covers a third of the view and cuts through the solid; on an empty part "Front"'s label is struck through by a plane edge (screenshots: empty, part). Fusion hides them by default; Onshape keeps them small. Fix: once the part has a feature, planes draw as outlines only and fade unless hovered (here or in the Part panel) or Sketch is waiting for a plane; View → Show Planes toggles. **Decision 4.**
- [ ] **F7. A sketch on its own solid is unreadable.** (S) The solid is drawn opaque behind the open sketch with the grid over it (screenshot: sketch open); the profile's lines have almost no contrast against the solid's fill. Fix: while a sketch is open, the solid draws at about a third opacity with its edges kept (Fusion's behaviour).
- [ ] **F8. Sketch tools fill the toolbar when nothing can be drawn.** (M) In the 3D tab with no sketch open, Line, Circle, Arc, Rectangle, Fillet, Dimension, Constrain, and Measure are all there, disabled, and the disabled ink is close to the enabled ink (screenshots: empty vs sketch open). Sketch and Extrude, the only two things you can do, are the only buttons without icons. Fix: the tray shows by context. Part: Sketch, Extrude, Measure, view tools. Sketching: the drawing tools, with Finish and Cancel. The tray already groups by category (`main_window.py:570-574`), so this is visibility per context, plus two icons.
- [ ] **F9. Two vocabularies for one feature.** (S) Properties shows the contract's raw values: "Plane xy", "Sketch e0", "Operation add", a "Reversed" checkbox. The Extrude form says "Top", "Sketch 1 (e0)", "Add to the solid", "Direction: Along the normal" (screenshots: extrude selected, Extrude form). Fix: one labeller (it exists: `titles`, `place_name` in `panels/features.py`) used by the form, Properties, and the tree.
- [ ] **F10. Double-clicking an extrude does nothing.** (S) `features.py:235-241` handles planes and sketches only. Fix: it opens the Extrude form filled in, editing: one `ModifyEntity`, with F3's preview.
- [ ] **F11. No context menus.** (M) Right-click in 3D is pan only; the Part panel has none. Fix: a right-click that doesn't move (the `DRAG_PX` rule already used for left) opens a menu for what's under it: Sketch Here, Edit Sketch, Extrude This Sketch, Edit Extrude, Look At, Delete, Zoom to Fit. Same menu from a Part panel row. Right-drag still pans.
- [ ] **F12. The status bar talks about the sketch when there isn't one.** (S) "3 degrees of freedom" shows in the 3D tab with no sketch open (screenshots: all part states). The 3D hint lists mouse gestures only, and Help → Keyboard Shortcuts lists canvas gestures only (`shortcuts.py:22-32`). Fix: degrees of freedom only while sketching; a 3D section in the sheet; hints that match F1.
- [ ] **F13. A proposed solid bigger than the view isn't brought into view.** (S) Recorded as "Not done" under C-16 in `shell.md`. Fix: fit to the union of the part and the proposal when the proposal's box leaves the view.
- [ ] **F14. A check's row cuts off the number that matters.** (S) "Volume of the part = 60…" then "70000" (screenshot: Checks). Fix: elide the label's middle, never the expected value.
- [ ] **F15. Carried from `shell.md:617`:** keyboard toggling of a browser group header. (S)

## 3. Part 2: what Fusion and Onshape have that the shell can build now

- [ ] **V1. View cube.** (L) Top right of the 3D view, in place of the 22 px unlabelled triad (`view3d.py:254-266`). Click a face, edge, or corner to snap there; drag it to orbit; a Home button. The proposal card moves under it (`_card_top` already works out where the card sits).
- [ ] **V2. Named views and animated camera moves.** (M) Front, Back, Left, Right, Top, Bottom, Isometric on Shift+1 to Shift+7 (Onshape's keys; none collide) and in View. Every camera jump (fit, face a sketch, F2's return, a named view) eases over about 180 ms; `Camera` is a frozen dataclass, so it's one interpolation. N in the 3D view looks straight at the picked face or plane, as it already does for a sketch. `PITCH_LIMIT = 89` (`camera3d.py:18`) needs a true top-down for Top and Bottom.
- [ ] **V3. Orbit about what you're looking at.** (S) Orbit turns about the camera's target, so a corner you zoomed into swings out of view. Fix: pivot on the point of the solid under the pointer at press (`Scene.solid_hit` exists), else the part's centre.
- [ ] **V4. Display modes.** (M) Shaded with edges (today), shaded, wireframe, and see-through. Remembered in QSettings. Perspective is out: the renderer is orthographic by design (ADR 0012).
- [ ] **V5. Selection readout.** (M) Bottom right, as Onshape does: one face picked says which and its plane; two parallel faces say the distance between them (both from `plane_frame`, no new query). Face area and edge length need a query: Part 4.
- [ ] **V6. Drag the extrude.** (M, after F3) An arrow on the profile along the normal: drag to set depth with the value following the pointer and snapping to whole mm; a click on the arrow's tail flips direction.
- [ ] **V7. Part panel as a feature tree.** (M) An icon per kind, a failing feature's reason on its row and not only in a tooltip, hovering a row highlights it in 3D and the other way round (with F5), Return edits, an eye per sketch to hide it in 3D (UI state, not in the file).
- [ ] **V8. The palette knows the context.** (S) ⌘K lists "2D Sketch" and "3D Part" first whatever you're doing, with disabled entries mixed in (screenshot: palette). Fix: what can run now first, the last few used above that, disabled entries below a rule.
- [ ] **V9. A next step after Finish.** (S) When a finished sketch has a closed profile, the status line offers "Extrude (⇧E)".

## 4. Part 3: the agent

All in `caliper/app/agent/` and the window, except where a line says Andre.

- [ ] **A1. Auto mode.** (L) A two-state switch on the prompt bar, in the Agent menu, and always visible in the status bar while on:
  - **Review** (today, and the default): every proposal waits for Accept.
  - **Auto**: a proposal is applied without a click when all of these hold, and otherwise stops on the card exactly as today:
    1. `proposal.acceptable` (no errors, every agent check passes);
    2. no `broken_checks` and no `removed_checks`;
    3. it wouldn't leave the part without its solid (`View3D._proposed` already works this out);
    4. at least one check was evaluated for it (**decision 1**: without this rule, "auto when checks pass" on a part with no checks is "auto, always").
  - Each auto-apply is still one undo step credited to the agent, in History, with a line in the status bar: "Applied X · ⌘Z to undo".
  - **Not remembered between launches,** and it turns off on New and Open. An auto mode that is still on next week, on another file, is a trap.
  - Turning it off offers "Undo everything applied in auto" (undo back to the history position where it was turned on; the same mechanism as Cancel Sketch's `_sketch_entry`).
  - **Claude Desktop builds a draft call by call and never says it's finished.** Auto applies the draft when `report_progress` says `calls_left == 0` or after 3 s with no call, whichever is first (**decision 2**). Applying after every call would make one undo step per call and end the draft each time.
  - **Needs Andre (small):** `Ended.ACCEPTED` in `caliper/ai/draft.py` tells Claude "The user accepted your pending changes". In auto mode that's false. A second reason, or a reworded one, on an `ai/` branch. Auto mode works without it but tells the model something untrue, so it shouldn't ship without it.
- [ ] **A2. A pause for Claude Desktop.** (S) The brake for A1. A Pause button refuses changing calls with a message that says the user paused Caliper (the same path as `BUSY`, `mcp_host.py:149-150`); reads still answer.
- [ ] **A3. What the proposal does to the part, in numbers.** (S) The card's summary adds the features gained and the volume before and after: "+ Sketch 2 on end of Extrude 1 · + Extrude 2 cuts 5 mm · 70,000 → 65,000 mm³". Both solids are already meshed for C-16.
- [ ] **A4. Hold to compare.** (S) While a proposal waits, holding a key shows the part as it is; releasing shows it as proposed. One flag in `View3D._proposed` and the canvas's preview.
- [ ] **A5. A run you can read back.** (M) In auto, the Assistant tab lists each applied step with its checks and "Undo to here".
- [ ] **A6. Step through a large proposal.** (L) ← and → on the card walk a proposal's commands, the view showing the part after step k. Later: it needs a replay per step, cached.
- [ ] **A7. Point and ask.** (M, part needs Andre) Chips on the prompt bar show what the request is about ("3 lines", "end of Extrude 1") with an ✕ each. The selection already reaches the assistant; the picked face doesn't (`session.picked_plane` isn't passed to `assistant.ask` or `draft.call`), and passing it is Andre's.

## 5. Part 4: what needs Andre or a contract change (flagged, not built)

In the order the shell would feel them:

1. **References to edges and faces of the solid in a sketch** (Onshape's Use, Fusion's Project). Without it a sketch on a face can't be tied to the face's outline, which is the first thing anyone tries.
2. **Fillet and chamfer on edges**, then **revolve** and a **hole** feature. Needs edge naming (ADR 0014 has the scheme, no code).
3. **Extrude extents:** symmetric, through all, up to a face; more than one profile per extrude.
4. **Feature rename, suppress, reorder, and a rollback marker.** The Part panel can't be a real feature tree without them.
5. **Queries:** a face's area, an edge's length, for V5. **Export:** STL from the mesh that already exists, STEP from OCCT.
6. **Offset and angled construction planes.**
7. **For the agent:** the picked face in the model's context (A7); `Ended.ACCEPTED`'s wording (A1).
8. **A decision due soon, not a feature:** the QPainter renderer holds 30 fps to about 9,000 triangles (Perf-8). Hover (F5) and animation (V2) draw more frames. The GPU threshold ADR 0012 recorded will be reached by real parts.

## 6. Order, branches, and what I'd cut

Three pull requests, each from `main` after the last merges. **Not stacked:** #48 to #51 and #58 each cost a re-land or a rebase because of stacking.

| PR | Branch | Contents | Size |
|---|---|---|---|
| A | `shared/3d-ux-fixes` | F1 to F15 | about 15 small commits |
| B | `shared/agent-auto-mode` | A1 to A5 (A1 waits on Andre's wording) | about 8 commits |
| C | `shared/3d-navigation` | V1 to V9 | about 12 commits |

A before B before C. The fixes first because F3 to F5 are what make 3D usable at all. The agent before the view cube because the agent checking its own work is what Caliper is for, and a view cube is what every CAD program has. If time is short, cut V1 (the view cube) and V4 (display modes): V2's named views give most of V1 for a fifth of the work.

A6, A7, and all of Part 4 are out of these three.

## 7. Do not touch

- `caliper/contracts/`, `caliper/engine/`, `caliper/ai/`, `bench/`, tests outside `tests/app/`, `CLAUDE.md`, `WORKPLAN.md`, `pyproject.toml`, ADRs.
- Invariants 2 and 5: every change is a `Command` on the bus; the agent gets no path the UI doesn't have. Auto mode calls the same `AgentController.accept`, nothing else.
- Previews, hover, the camera, display modes, hidden sketches, and the agent's mode are UI state: never commands, never in the file (ADR 0005).
- The C-16 and C-18 behaviour #58 adds, and its 22 mutation-checked tests.
- Qt modules outside `pyside6-essentials` (ADR 0007); no new dependency.
- The user's saved QSettings: tests and screenshots use their own scope.

## 8. Risks and unknowns

- **F1 and F2 change behaviour people may be used to** (two-finger scroll in 3D; where the camera is after Finish). Both are decisions below.
- **Frame cost.** F3, F5, and V2 all draw more often. Each lands with a `bench/perf_v22.py` number before and after; a regression past 16 ms a frame on the 2,604-triangle part stops that change.
- **Auto mode's worst case** is a wrong change applied with a weak check passing. The guard is rule 4 plus one-step undo plus "Undo everything applied in auto". It is not a guarantee, and the plan doesn't claim one.
- **Unverified:** nothing here was tried in the real window on the Retina display or with Claude Desktop; the screenshots are offscreen renders, which don't show trackpad feel, HiDPI, or animation. Whether a force-push dismisses Andre's approval depends on a repo setting I didn't check.
- **F4 with more than one extrude:** `queries.faces(id)` gives a feature's faces by name; a later cut can remove them (C-19's cousin). The tint then covers what's left, or nothing, and says so.

## 9. Verification

Per change: a test in `tests/app/` that fails when the change is broken on purpose (the project's rule; #58 did 22). Then, with real output and exit codes:

```bash
cd ~/Desktop/Active/caliper-ui
uv sync --extra app --extra occt --group app-test
QT_QPA_PLATFORM=offscreen uv run pytest tests/app -q; echo "exit=$?"
uv run pytest -q; echo "exit=$?"
uv run ruff check; echo "exit=$?"
uv run ruff format --check; echo "exit=$?"
uv run mypy; echo "exit=$?"
uv run python bench/perf_v22.py        # frame times before and after F3, F5, V2
```

Plus the nine offscreen screenshots again, before and after, for every visual change. What only Lucas can check, reported as `UNVERIFIED` until he does: trackpad gestures, animation feel, HiDPI, and auto mode against a live Claude Desktop run.

## 10. Decisions for Lucas

1. **Auto mode needs a check to act on.** Recommended: yes, a proposal with no check evaluated waits for review.
2. **When auto applies Claude Desktop's draft.** Recommended: at `calls_left == 0` or 3 s idle. Alternative: after every call (simpler, one undo step per call).
3. **Camera after Finish.** Recommended: back to where it was before the sketch opened.
4. **Origin planes.** Recommended: outlines that fade once the part has a feature, with a toggle.
5. **Trackpad in 3D.** Recommended: two-finger pans, pinch zooms, Shift+two-finger orbits. Today two-finger zooms.
6. **Scope.** Recommended: approve PR A now; B and C after A is in.
