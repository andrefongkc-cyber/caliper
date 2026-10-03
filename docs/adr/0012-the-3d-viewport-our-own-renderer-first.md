# ADR 0012: The 3D view starts as our own renderer, with QPainter, behind a 2D/3D switch

- **Status:** Proposed (V2's F5, drafted by Andre's session on 2026-10-01). The Engine Plan
  asked Andre and Lucas to pick which option to try first, and they hadn't; this is the first
  try, chosen because it is the one that's easy to undo. Accepted, or changed, by Lucas (F7).
- **Date:** 2026-10-01
- **Code:**
  - `caliper/app/viewport/view3d.py` and `camera3d.py`;
  - the switch in `caliper/app/main_window.py`.
- **Related:**
  - ADR 0004 (PySide6);
  - ADR 0006 (no GPL);
  - ADR 0007 (GPL-only Qt modules stay out);
  - ADR 0013 (the solid and its mesh).

## Context

V2 makes parts, and the app has to show them, without taking anything away from the 2D
sketching that's in daily use. The Engine Plan left three options:

- **OCCT's own viewer** (LGPL);
- **VTK** (BSD);
- **our own renderer.**

Qt Quick 3D is ruled out: it's GPL-only (ADR 0007). Whichever is chosen must:

- draw a tessellated solid, with orbit, pan, and zoom;
- report its frame time;
- get the mesh through the contract (`Queries.mesh`, ADR 0013), never from a kernel. The app
  never imports one.
- run in CI's app job, which is offscreen on macOS with no OCCT and no GPU it can count on.

## Decision

**Our own renderer, with QPainter, no GPU, as the first try.**

- **The camera** (`camera3d.py`, plain maths, no Qt) is orthographic, as CAD views are, with
  Z up. It starts isometric, from the front right.
  - A left drag orbits about the target. A right or middle drag, or Shift with a left drag,
    pans, keeping what's under the pointer under it.
  - The wheel zooms about the pointer.
  - F fits the part, from any side.
- **Drawing** (`view3d.py`) uses the painter's algorithm.
  - **Faces:** the triangles facing the viewer, the furthest first. Each is lit flat by a light
    at the viewer, with a third of its colour as ambient.
  - **Edges:** drawn where faces meet at more than 25°, so a box shows its twelve edges, not
    its triangles.
  - **The triad:** a small X, Y, Z triad in the corner.
- **The mesh** comes from `session.queries.mesh(tolerance)`, with the tolerance a thousandth
  of the part's size, clamped to 0.01 to 1 mm. The view asks only when it's showing and the
  document has changed, and the engine keeps meshes by identity.
- **The view's messages:**
  - with no solid yet, it says how to make one;
  - with no kernel, what to install;
  - with a failing feature, it keeps the last good solid on screen, with the reason (core.md's
    item 5, on the screen's side).
- **The switch is a core mode, not a setting:**
  - **2D** sketches on the canvas, as before. **3D** shows the part's solid.
  - It's at the head of the toolbar, in the View menu, in the command palette, and on ⌘1 and
    ⌘2.
  - Both views read the same session. Switching changes which widget is shown (a
    `QStackedWidget`), never the document, the undo history, or the selection.
  - In 3D, the sketch-only tools (drawing, constraints, grid, snap) are disabled, not hidden.
  - A proposal from the agent switches back to 2D, where its card is.

  > **Changed by [ADR 0015](0015-sketching-in-3d-from-the-parts-planes.md)** (Proposed): the
  > tabs now hold two documents, the 3D part and a 2D sketch to test on. The 3D view shows the
  > part's planes and sketches too, and a sketch is edited in 3D, facing its plane. A
  > proposal is reviewed in the tab it was made in.

## Measurements

`bench/perf.py`, `v2/render-3d`, on this Mac (Apple silicon), offscreen, 1280 x 800, from
OCCT's meshes:

| Part | Triangles | Median frame | p95 frame |
|---|---|---|---|
| The milestone plate, 120 x 50 x 10 | 12 | 0.39 ms | 0.43 ms |
| 240 x 160 x 10 with 24 round holes | 2,604 | 19 ms | 34 ms |

Frame time grows with the triangles drawn: about 7 µs each. `tests/app/test_view3d.py` holds a
loose bound of 500 ms for a 1,000+ triangle part, so CI isn't flaky.

## Consequences

- **No new dependency.** QPainter is in `pyside6-essentials` (LGPL), and the licence check
  is unchanged.
- **It runs anywhere the app does,** offscreen tests included. The view is tested by its
  pixels and its camera, and the camera without Qt.
- **2D is untouched.** The canvas is the same widget, in the same place, and the default
  mode.
- **Limits:**
  - The painter's algorithm orders whole triangles. Faces that cross each other, or deep
    concave parts, can show the wrong one in front where a depth buffer wouldn't.
  - About 7 µs a triangle: a 10,000-triangle part is roughly 70 ms a frame.
    Performance V2.2 (Perf-8, 2026-10-02) halved it with the same pixels: about 3.4 µs a
    triangle, so 30 frames a second hold to about 9,000 triangles (`bench/perf.py`, `3d/frame`).
  - There's no picking or selection highlight in 3D yet.
- **When to move on.** When parts commonly pass about 10,000 triangles, or need exact hiding,
  move to `QOpenGLWidget` with a depth buffer. It is in `pyside6-essentials` and LGPL. It
  goes behind the same `View3D`: it reads the same mesh and camera, and nothing else changes.
  VTK or OCCT's viewer stay open if picking and many more triangles are needed at once.

## Alternatives considered

- **OCCT's viewer (`V3d`/`AIS`).** It's LGPL and already has picking. But it makes the app need
  the optional `occt` extra and embeds a native window, which is harder to test offscreen. It
  also brings a second scene model alongside the document.
- **VTK.** BSD and fast, but a large new dependency for one view. Its Qt widget has its own
  event loop and render window, so tests would need a display.
- **`QOpenGLWidget` now.** The likely next step. Its offscreen behaviour in CI is uncertain,
  and nothing measured yet needs a GPU.
- **Qt Quick 3D.** GPL-only (ADR 0007). Ruled out.
- **Replacing the 2D canvas with a 3D view of the sketch.** That would lose the 2D workflow in
  daily use. The switch keeps both.
