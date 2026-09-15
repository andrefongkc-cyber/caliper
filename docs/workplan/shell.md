Status: not started, next: Phase 0.5 milestone spike (after the Phase 0 check-in)

# Shell workplan — Stream B

Owns `caliper/app/`. Builds against `caliper.contracts` and the in-memory engine; never imports a kernel.

Markers: `[ ]` not started · `[~]` in progress · `[x]` done

## Phase 0.5 — Milestone spike (4-day timebox)

Built in `caliper/app/`, not a throwaway directory. Stream B inherits and rewrites it.

- [ ] Window with a QPainter canvas
- [ ] Rectangle tool → `CreateRectangle` through the bus
- [ ] Select → properties field → width edit command
- [ ] Save → quit → reopen → width still 120
- [ ] Record contract gaps found (feeds the freeze)
- [ ] Exit: milestone works end to end → freeze `commands.py` + `document.py` → split streams

## V1 — Shell

- [ ] Main window: Mac-first, dark, compact chrome, canvas-dominant
- [ ] Viewport: pan, zoom, grid, snapping, Retina-correct, smooth at 120 Hz
- [ ] Tool modes as a state machine: select, line, circle, rectangle, arc
- [ ] Selection, hover highlight, drag-to-move (one command on release), delete
- [ ] Properties panel for dimensions (coalesced edits = one undo step)
- [ ] Undo/redo wired to the command bus, with command display labels
- [ ] File menu: new, open, save, save as
- [ ] pytest-qt coverage for tool-mode transitions and input handling
