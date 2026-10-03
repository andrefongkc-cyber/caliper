# ADR 0013: Solids, extrude as the first feature, and recomputing only what changed

- **Status:** Proposed (V2's F2 and F3, Andre, 2026-10-01; Accepted with ADR 0011 once Lucas
  reviews V2's contract from the app's side, F7)
- **Date:** 2026-10-01
- **Contract:**
  - `caliper/contracts/kernel.py` (Provisional);
  - `document.py`: `Extrude`, `ExtrudeOperation`, `Metric.VOLUME`;
  - `commands.py`: `CreateExtrude`;
  - `queries.py`: `Point3`, `BoundingBox3`, `Mesh`, `SolidProperties`, `solid_properties`,
    `mesh`, `feature_error`;
  - `errors.py`: `kernel.unsupported`, `dependency.cycle`, `feature.failed`.
- **Related:**
  - ADR 0001 (OCCT behind a Kernel protocol);
  - ADR 0002 (automatic deltas);
  - ADR 0005 (inputs only);
  - ADR 0011 (the part);
  - the recomputation graph in `docs/workplan/core.md`.
- **Number:** 0012 is kept for the 3D viewport, as the Engine Plan numbered it.

## Context

V2's first milestone runs end to end: a 120 x 50 sketch on XY, extruded 10 mm, is
60,000 mm³; widened to 140 it's 70,000; undone, 60,000; saved, reopened, and replayed to the
same bytes. ADR 0011 made the document a part with features in order. This ADR adds what the
first solid needs.

1. **A kernel that builds solids** (F2). The Kernel protocol was Provisional for exactly this.
2. **An extrude as a feature** (F3), with the 2D rules' guarantees: it is a command, undone by a
   delta, replayed exactly, and never stores what it computes.
3. **Recomputing only what a change reaches** (core.md's graph, items 1 to 3), as the 2D
   solver already does for clusters and checks.

## Decision

**The kernel builds solids.**

- **The protocol:**
  - `extrude(face, frame, depth)` places a face drawn in 2D on a `Frame`, a sketch plane's
    origin and axes from `part.frame(plane)`, and sweeps it along the normal.
  - Then `union`, `cut`, `volume`, `bounding_box_3d`, and `mesh(solid, tolerance)`, whose
    triangles face out.
- **Both kernels** pass one conformance suite, which checks them against formulas, never
  against each other. It includes property tests that volume equals area times depth, with
  arcs and holes.
- **The analytic kernel is exact or says it can't.** A solid there is prisms whose insides
  don't overlap. Union and cut are exact where no 3D boolean is needed: solids apart; one
  inside another on the same frame; a cut through the whole depth; a cut that leaves
  nothing. Anything else is `kernel.unsupported`, never a guess, and OCCT does it.
- **Its meshes** cut caps into strips at each corner's height. Ear clipping was tried first,
  and a property test found it stalling.

**An extrude is a feature.**

- **The feature:** `Extrude(id, sketch, depth, operation, ids)`, with depth more than 0, along
  the sketch plane's normal.
  > **Changed by [ADR 0016](0016-sketching-at-any-angle-and-on-faces.md)** (Proposed): or
  > against it, with `reversed`; a sketch can be on a face, and fails when the face is gone.
- **What it sweeps:** the profile is the geometry in `ids`, or with none given, the sketch's
  geometry that isn't construction geometry.
- **One solid per part:** `ADD` joins the extrude on, and the first extrude must add.
  `REMOVE` cuts it away.
- **Made or edited only from one closed profile** (`profile.not_closed` otherwise, with the
  reason, as N4's areas). A later edit to the sketch that breaks the profile isn't refused:
  the extrude *fails*, says why (`feature_error`), and every feature after it is
  `feature.failed`, not recomputed. That is how a check whose geometry is deleted works (C-1).
- **Deleting a sketch deletes the extrudes that read it.**
- **It reads only what comes before it.** An edit that would make it read a later feature,
  or itself, is `dependency.cycle`, and so is a file that does (`graph.order`, and the
  loader). The feature graph can't have a cycle.

**Solids are recomputed, never stored, and only where something changed.**

- **Never stored.** `caliper/engine/features.py` works the part's solid out from its features
  in order, when a query asks. Kernel output differs across platforms in the last bits, so
  storing it would break byte-identical files (ADR 0005). A command never needs a kernel; a
  query does.
- **Cached by identity, not by version numbers:**
  - an extrude's prism, by the kernel, the extrude, its sketch, and the very geometry objects
    in its profile;
  - the solid after it, by the solid before and that prism;
  - each document's results, and each solid's meshes.

  Documents share every entity a change left alone, and undo puts the old objects back. So:

  | Change | What is rebuilt |
  |---|---|
  | The width | One prism |
  | A dimension's label moving | Nothing |
  | Undo or redo | Nothing |
  | One sketch of two | Its prism and the join after it |

  A kernel that counts its own work proves each of these in `tests/engine/test_recompute.py`.
- **The graph** (`caliper/engine/graph.py`) names what depends on what: `inputs`,
  `dependents`, `affected`, and `order`.
  - Its 2D layer is the solver's `references` and `referrers`, under the graph's names
    rather than moved: moving them would change every solver caller and no behaviour. A
    property test over random sessions holds them equal.
  - A sketch is one node, whose inputs are the geometry drawn in it.
  - An extrude reads its sketch and builds on the solid of the extrude before it.

**Queries.**

- **The solid:** `solid_properties(ids)` gives volume and 3D bounds, and `mesh(ids,
  tolerance)` gives triangles to draw. Empty `ids` is the whole part; one feature's id is the
  part as it stood after that feature.
- **Failures:** `feature_error(id)` says why a feature fails.
- **Checks:** `Metric.VOLUME` checks the same thing. A volume check stays out of the check
  cache, because it reads features, not only the entities it names.
- **Without a kernel:** `kernel.unavailable`. "No solid yet" needs no kernel to say, and says
  so.
- **The bench** measures with OCCT when it's installed, and otherwise with the analytic
  kernel. Its last line says which.

**Files.** Schema 4 carries `extrude` features too. F1 and F3 reach `main` together, and
schema 4 hasn't been released, so there's no second bump. Older files have no extrudes, and
their migration is unchanged.

**The AI tools** get `create_extrude` and `run_check`'s `volume`, since the app gets an Extrude
tool (F6) and the model has what the UI has.

## Consequences

- **The milestone runs headless on both kernels**
  (`tests/engine/test_milestone.py`, and the bench case `extruded-plate-milestone`):
  - 60,000, then 70,000, then 60,000 after undo;
  - the same after save and reopen;
  - and byte-identical replays.
- **A change costs what it reaches.** The milestone's width change takes about 0.5 ms with
  one prism rebuilt, and moving a label or undoing rebuilds nothing (`bench/perf.py`,
  `v2/milestone`).
- **Kernels agree within tolerance, not to the bit.** OCCT's volumes and bounds are accurate
  to its geometric tolerance, so volume checks need a tolerance, as area checks do.
- **Limits:**
  - The analytic kernel can't combine overlapping solids. A part like that needs the `occt`
    extra, and the app without it can't show solids (known issue C-15).
  - One solid per part.
  - A failing feature stops everything after it. Keeping the last good solid on screen is
    the viewport's job (F5 and F6).
- **References to the solid's own faces and edges** (a sketch on a face, a fillet on an edge)
  wait for persistent naming (F4, its own ADR).

## Alternatives considered

- **Storing solids or meshes in the file.** That breaks byte-identical files and stores what
  can be computed (ADR 0005). Rejected.
- **Recomputing every feature on every query.** Simple, but a part grows features, and the 2D
  solver already shows identity caching works. Version counters or dirty flags would
  duplicate what object identity gives for free.
- **A general boolean in the analytic kernel**, clipping polygons with arcs in 3D. Large and
  easy to get subtly wrong, when OCCT exists and the conformance suite keeps the two honest
  where they overlap.
- **Several solids (bodies) per part, and naming the solid an extrude builds on.** More
  contract for nothing the milestone needs. The implicit chain (each extrude builds on the
  last) is enough for one solid, and adding bodies later is additive.
- **Moving `references` and `referrers` into the graph module.** That changes every caller in
  the solver for no change in behaviour. The graph names them instead, and a test holds them
  equal.
