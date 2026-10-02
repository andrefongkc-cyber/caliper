# ADR 0015: The part is sketched in 3D, from its planes, and the 2D tab is a document of its own

- **Status:** Proposed (drafted by Andre's session on 2026-10-01, after V2's F8, at Andre's
  direction). Accepted, or changed, by Lucas with ADRs 0011 to 0014.
- **Date:** 2026-10-01
- **Code:**
  - the two documents: `caliper/app/session.py` (`Space`, `use`);
  - the 3D view: `caliper/app/viewport/scene3d.py`, `view3d.py`;
  - sketching in 3D: `caliper/app/viewport/backdrop.py`, the canvas, and the window's Sketch,
    Finish, and Cancel;
  - the engine: `part.no_sketch`, `part.first_sketch`, and script schema 2;
  - the AI: a first sketch on Top in a part with none.
- **Changes:** ADR 0011's as-built answers 2 and 3 (File → New, what the canvas draws), and
  ADR 0012's "one document behind both views". Both are Proposed, and each points here.

## Context

F5 to F8 built V2 as one document behind two views: a sketch was drawn in the 2D tab, and
the 3D tab showed the part's solid. Trying it, Andre found that confusing:

- A part should start the way it does in Onshape: from its origin and its Top, Front, and
  Right planes, with nothing else.
- A sketch should be made and edited in 3D, on its plane, not by jumping to the 2D tab.
- The 2D tab is for testing: a sketch to try things on, apart from the part.
- Saving should go with the tab: the part to one file, the test sketch to another.
- There was no way to say a sketch is done.

Andre chose, of the options put to him (2026-10-01):

- a new part has **just the planes**, no sketch;
- a sketch is edited **facing its plane**, with N to face it again after orbiting, rather
  than drawing at any angle (which is closer to Onshape but a much larger change);
- the tabs hold **two separate documents**, each with its own file, undo history, and
  unsaved-changes state;
- Claude, in Claude Desktop or the prompt bar, works on **the tab the user is in**.

## Decision

**Two documents, one per tab.**

- The session holds both: the 3D tab's part and the 2D tab's sketch. One is shown at a time.
- `DocumentSession.use(space)` switches, as opening a file does, and the other keeps its bus,
  file, history, selection, and sketch until it's shown again.
- Everything that reads the session reads the one shown: the panels, the agent, and Claude
  Desktop. A proposal pending when the tab changes is withdrawn, as it is when a file opens.
- New, Open, and Save act on the tab shown. Closing the window asks about each tab's unsaved
  changes in turn.
- The app starts in 3D.

**A part starts with its planes.**

- In the 3D tab, a new part is `part.no_sketch()`: no features, nothing drawn.
- The 3D view always shows the origin and the planes, named as Onshape names them: Top (XY),
  Front (XZ), Right (YZ). It shows each sketch's curves on its plane, then the solid.
- The Part panel lists "Default geometry" (Origin, Top, Front, Right) above the features.
- A click in the 3D view picks a plane or a sketch. A double-click on a plane starts a sketch
  there; on a sketch, it edits it.
- `Document.empty()`, one sketch on XY, is still the new part everywhere else: the 2D tab,
  headless use, the bench, and every V1 file. Nothing in `caliper/contracts/` changed.

**A sketch is edited in 3D, facing its plane, by the same canvas.**

- An orthographic camera looking straight at a plane maps it to the screen with a scale and
  an offset, exactly as the 2D canvas maps a sketch. So the canvas edits the sketch, every
  tool as it is, over the part drawn from the camera that faces the plane at the canvas's
  own scale and centre (`Backdrop`). Panning and zooming move both together.
- **Sketch** (Shift+S, or the toolbar) starts a sketch on the picked plane, or edits the
  picked sketch. With neither, it shows the planes to choose from.
- **A right drag orbits away.** The canvas then draws only the scene, and drawing waits.
  **N** faces the sketch again. A right click without a drag still cancels the tool.
- **Finish** (✓) closes the sketch: its changes stay, each its own undo step.
- **Cancel** (✗) closes it and undoes everything since it opened, including making it. Redo
  brings it all back.
- Extrude finishes an open sketch first, then sweeps the picked sketch or the last one edited.

**Claude works on the tab shown.**

- In the part, drawing that names no sketch goes into the one open, as the window's tools do.
- In a part with no sketch, the AI's drawing tools first make one on XY, the Top plane
  (`part.first_sketch`), in the same call and the same undo step.
- A proposal in 3D is reviewed facing the sketch it draws in: the open one, another of the
  part's, or the one it's making. Accepted, that sketch is open.

**Replay.** What the part's tab records starts from a part with no sketch, so scripts gain
schema 2 and `"part": "empty"`. Schema 1 scripts read as before, and still start from one
sketch on XY. Byte-identical replay (invariant 4) holds for both tabs.

## Consequences

- The 2D tab is untouched: every V1 test, pixel baseline, and Claude Desktop 2D session runs
  as before, on its own document.
- The part is made the way CAD users expect: plane, sketch, finish, extrude.
- The sketcher isn't duplicated. The 3D tab edits with the same canvas, tools, snapping,
  dimensions, and constraints.
- Drawing needs the view to face the sketch: an orbited view only looks. Drawing at any
  angle would need the canvas to map a slanted plane (an affine view in place of a scale
  and an offset), in its picking, snapping, and labels.
- Switching tabs while Claude has a proposal withdraws it, and Claude is told the document
  changed.
- The planes grow with the part, to about its size, so they don't swamp a small one.
- Drawing the planes and sketches costs the 3D view about half a millisecond a frame, on the
  plate (0.41 → 0.82 ms) and on a plate with 24 holes (19.4 → 19.8 ms), against F8's saved
  run (bench `v2/render-3d`, 2026-10-02).

## Alternatives considered

- **One part, saved two ways** (Save in 2D writes the edited sketch as its own file). Andre
  chose separate documents: the 2D tab is for testing, and its work shouldn't land in the
  part.
- **A new part with Sketch 1 on Top ready**, as V1's documents have. Andre chose planes only.
  The 2D tab keeps the ready sketch.
- **Drawing on a plane at any angle**, as Onshape allows. Deferred: facing the plane reuses
  the proven sketcher unchanged, and can grow into this.
- **A second canvas for the 3D tab.** Rejected: two tool state machines, two sets of caches,
  and the window's tools would have to know which canvas they act on.
