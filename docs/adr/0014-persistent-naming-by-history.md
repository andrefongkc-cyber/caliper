# ADR 0014: Persistent names for faces and edges come from history, not position

- **Status:** Proposed (V2's F4 spike, Andre, 2026-10-01. It records what was found; the code
  waits for the first feature that refers to a face or edge.)
- **Date:** 2026-10-01
- **Evidence:** `tests/engine/geometry/test_naming_spike.py`, run against OCCT (cadquery-ocp
  8.0.1) on macOS, and by CI's `occt (Linux)` job, which runs `tests/engine/geometry`
- **Related:**
  - ADR 0001 (OCCT behind the Kernel protocol);
  - ADR 0011 (the part and its stable ids);
  - ADR 0013 (solids, recomputed, never stored);
  - core.md's recomputation graph, item 4.

## Context

The next features after the extrude refer to topology another feature generated: a sketch
placed on a face, a fillet on an edge, a hole from a face. The part is rebuilt from its inputs
whenever anything changes (ADR 0013). A reference saved as "this face" must find the same face
after a rebuild with other numbers, or say it can't. It must never quietly find a different
one. This is the persistent-naming problem, and the Engine Plan made it a spike with an ADR
before anything relies on it.

The spike named the faces and edges of an extruded plate (120 x 50 x 10, with and without a
round hole) in two ways. It rebuilt the plate after each change V2 makes or will soon make,
and compared what each name pointed at, by centre and area, with formulas.

## What was found

| Rebuilt after | By position (OCCT's face order) | By history (what made the face) |
|---|---|---|
| Width or height changed | Kept | Kept |
| Depth changed | Kept | Kept |
| The same feature rebuilt | Kept | Kept, faces and edges |
| A hole added to the profile | **Moved**: "face 4" was the start cap, now the hole's wall | Kept; the hole's wall gets a new name, nothing else moves |
| That hole removed | Moved back | Its name is gone, so a reference to it is lost, not re-pointed |
| The outline drawn from another corner | **Moved**: "face 0" was the bottom side, now the top | Kept |
| A boss joined, apart from the plate | — | Every name on its face |
| A hole cut through the whole depth | — | Every name carried one to one: the caps come back holed; the hole's walls are the tool's own sides |
| A boss joined along a side | — | The shared side is deleted (now inside); the rest carried one to one |
| A slot cut right across the top | — | **Split**: "end" names two faces, either side of the slot |

Two details mattered:

- **OCCT copies edges** when it joins them into a wire. History must be asked about the face's
  own wire edges (`BRepTools_WireExplorer`), in the order each wire runs, not the edges first
  made. Asked about those, `BRepPrimAPI_MakePrism.Generated` gives nothing back for most.
- **Edges named by the faces they join** follow the faces: "end|side e1.right" is the top of
  the right side at width 120 and still at 140. A cylinder's seam is the one edge a face meets
  itself along ("side e9|side e9").

## Decision

**Name by history, never by position.**

- **A face's name** is its feature, then what made it:
  - `start` or `end` for an extrude's caps, from `MakePrism`'s `FirstShape` and `LastShape`;
  - `side <entity>` for the face swept by a sketch entity's edge (`Generated`). A
    rectangle's sides are named `side e1.bottom`, `.right`, `.top`, and `.left`, the
    rectangle's own curve features.
- **The engine makes the names from inputs** it already has: feature ids and sketch entity
  ids, stable for an entity's life and the same on every replay (ADR 0011). The kernel maps
  them to its faces from its own history. OCCT has that history; the analytic kernel's
  prisms know the profile edge behind each side.
- **An edge's name** is the names of the two faces it joins, sorted. A seam is a face with
  itself.
- **Through booleans**, names follow OCCT's own history (`BRepAlgoAPI` `Modified` and
  `IsDeleted`): kept, carried to the face that replaced it, or deleted.
- **A reference saved in the file is a name**, which is an input (ADR 0005). Resolving it
  happens at recompute.
  - **Found once:** fine.
  - **Found nowhere** (its geometry was removed, its face deleted): the feature that refers to
    it fails, with the reason.
  - **Found more than once** (a split): it fails too, as ambiguous. It is never resolved by a
    guess.
- **Nothing is built yet.** No feature refers to a face or an edge, so no `FaceRef` is in the
  contract and no naming code is in `caliper/`. The first feature that needs one adds it:
  - a `Kernel` method that returns a solid's faces and edges by name;
  - the reference type in the contract;
  - the two failures above;
  - tests on both kernels, starting from this spike's cases.

## What V2 can and cannot safely refer to

**Can**, once the first referring feature lands:

- **On an extrude:** its caps and sides, and the edges between them, by these names.
- **Through these changes:**
  - its sketch's dimensions, or its depth;
  - other geometry added to or removed from the profile;
  - the order the outline was drawn in;
  - any rebuild.
- **Through these booleans:** joins of solids apart, holes cut through, and bosses that share
  a side (the shared side's name is gone, correctly).

**Cannot**, which is refused or waits for more work:

- **A face a later cut splits in two.** One name, two faces. Telling them apart needs more
  than history, such as the cutting tool's names or the side of it each piece is on. Until
  then, a reference to it fails as ambiguous.
- **Faces of features that don't exist yet** (fillets, chamfers, shells, patterns of
  features), each of which brings its own history to name from.
- **Faces merged by unifying coplanar faces** (`ShapeUpgrade_UnifySameDomain`). It isn't used.
  Using it later must carry names through its history, or references break.
- **Geometry redrawn as a new entity.** A line deleted and drawn again is a new id, so faces
  named by the old one are lost, and the reference says so. That's on purpose: a name never
  moves to something else.
- **Imported shapes**, which have no history.
- **Anything named by position.** The spike shows it moving under ordinary edits.

## Consequences

- **The milestone needs no names**, and nothing in it changes.
- **The first face reference** costs a kernel method, a contract type, two failure codes, and
  tests, all scoped here. It costs no new mechanism: the names come from ids the document
  already has, and the history from OCCT.
- **Split faces** are the open problem, and the first place a user could meet it is a pocket
  cut across a face that a later sketch sits on.

## Alternatives considered

- **Names by position** (face index). Moves under ordinary edits, as the spike shows. Rejected.
- **Names by geometry** (the face whose centre or normal matches). Breaks exactly when a
  dimension changes, which is when a reference matters, and two faces can match. Rejected,
  though geometry can help pick between the pieces of a split.
- **OCAF's naming (`TNaming`).** OCCT's own framework for this. It brings OCAF's document
  model alongside ours, and the same history is reachable directly, as the spike shows.
  Revisit if splits and fillets make the direct route grow too far.
- **Building the naming system now.** No feature refers to a face yet. Building it ahead of
  one is speculative generality (CLAUDE.md), and the spike's tests stand ready for whoever
  does.
