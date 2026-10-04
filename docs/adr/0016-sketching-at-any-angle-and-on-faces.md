# ADR 0016: Sketching at an angle, and on the part's flat faces

- **Status:** Proposed (Andre, 2026-10-03, after Performance V2.2). Accepted, or changed, by
  Lucas with ADRs 0011 to 0015.
- **Date:** 2026-10-03
- **Changes:**
  - ADR 0011: "a sketch on a face waits for persistent naming (F4)";
  - ADR 0013: "a sketch never fails", and an extrude always going along the normal;
  - ADR 0014: the first reference to a face;
  - ADR 0015: an orbited view only looks.
  All four are Proposed; each points here.
- **Code:**
  - the contract: `caliper/contracts/` (`FaceRef`, `Extrude.reversed`, `Frame`, the new
    queries);
  - the engine: `caliper/engine/faces.py`;
  - the app: the canvas's view of a tilted plane, and picking a face in 3D.

## Context

Sketching in 3D faces one of the part's three planes (ADR 0015). Andre asked for what
Onshape does (2026-10-02):

- **Draw at an angle.** Drawing on a sketch's plane while the view is turned away from it,
  not only facing it.
- **Sketch on a face.** Start a sketch on a flat face of the solid, such as the top of a
  plate, to cut a pocket into it or build a boss on it.

Of the options put to him, he chose:

- a sketch on a face **follows the face**. It names the face by what made it, as ADR 0014
  names faces, so changing the extrude's depth moves the sketch with it;
- drawing works **up to a steep tilt**, about 70° from facing;
- **Claude gets the same**: a sketch on a plane or a face.

## Decision

**A sketch sits on a plane or on a face.**

- `Sketch.plane` is a `Plane` or a `FaceRef(feature, face)`. `feature` is an extrude before
  the sketch.
- `face` is a name from ADR 0014:
  - `start`: the cap on the extrude's sketch plane;
  - `end`: the cap at its depth;
  - `side e3`: the side swept from line `e3`;
  - `side e1.right`: one side of a rectangle (`bottom`, `right`, `top` or `left`).
- Only flat faces can be sketched on. The side of an arc or a circle is
  `face.not_planar`. A name that isn't one of the extrude's faces is `face.not_found`.
- The plane is a union rather than a second field, so a face sketch has no plane value that
  could disagree with its face. `ModifyEntity` moves a sketch between planes and faces as it
  moves one between planes.

**Where a face is: worked out from the extrude's inputs, never by the kernel.**

Take an extrude on a sketch whose plane has origin o, axes x and y, and normal n = x × y. It
sweeps depth h in direction d, which is n, or −n when `reversed`:

| Face | Plane | Points out of the solid |
|---|---|---|
| `start` | the sketch's plane | −d |
| `end` | moved h along d | +d |
| `side <line>` | through the line, along d | away from the profile's inside |

- A cut flips each of these: the solid's face points the other way to the tool's.
- **A sketch's normal is its face's outward normal**, so an extrude that adds builds
  outward, and one that cuts goes in once reversed.
- No kernel is needed, so a command still never needs one (ADR 0013). Picking a face and
  checking a name are the same arithmetic as the extrude's own profile check.
- A depth or line edit moves every sketch on that extrude's faces, and what's built on
  them.

**The axes of a sketch on a face: level, and up the face.**

For outward normal m:

- x is +X when m is vertical, and otherwise the level direction (−m.y, m.x, 0), normalised;
- y = m × x;
- the origin is the plane's point nearest the part's origin.

This rule gives exactly the three planes' own axes (ADR 0011's table), and a top face's axes
are the sketch's under it, so a hole drawn on top lines up with the plate's dimensions.
Because x is always level, the camera that faces a sketch needs no roll.

**An extrude can go the other way.**

- `Extrude.reversed` (default false) sweeps against the normal.
- `CreateExtrude.reversed` left as None is resolved by the engine: true for a cut from a
  face, which then goes into the part, as in Onshape, and false otherwise. The resolved
  command records it.
- The kernel is unchanged. A reversed extrude is the same profile on a plane moved back by
  its depth.

**A sketch can fail.**

- If an edit takes away its face, the sketch fails with the reason (`feature_error`). The
  examples are a profile that's no longer closed and a side line deleted; a deleted extrude
  takes its sketches with it.
- Only the extrudes that read a failed sketch fail (`feature.failed`).
- A file still opens.
- Creating a sketch on a face, or moving one onto a face, is refused unless the face is
  there now.
- A sketch on a face of a later feature is `dependency.cycle`.

**Drawing at an angle.**

- Facing the plane, the canvas works exactly as it does now.
- Turned away by up to 70°, the canvas draws the sketch through the camera, and every tool
  works. Seen at an angle, a plane maps to the screen by an affine map, and the canvas's
  drawing, picking, grid and box selection use it.
- Past 70°, a click on the screen covers too much of the plane one way to pick reliably, so
  drawing waits, as it does now, until the view is turned back or N faces the plane.
- Picking at an angle reaches as far on the plane as 6 pixels on screen reach in the
  foreshortened direction. That is never short, and at most 2.9 times as far across the
  tilt at 70°.

**New queries.** All of them are analytic, so Claude's tools and a script use them without a
kernel:

- `plane_frame(plane)`: where a plane or face is;
- `faces(extrude)`: the names of an extrude's flat faces;
- `face_at(point, normal, tolerance)`: which named face a point of the solid is on, for
  picking. The latest feature wins, since it made the surface there;
- `entities_in_polygon(corners, crossing)`: a box selection seen at an angle.

`Frame`, which the kernel takes, moves to the queries, where `plane_frame` returns it. The
kernel contract still exports it.

**File schema 5.** The migration adds `"reversed": false` to every extrude. Scripts keep
schema 2: the new command fields are optional.

## Consequences

- A part is built the way it is in Onshape: plate, sketch on its top, pocket into it, a boss
  on a side.
- Sketches on faces follow edits to depth and to lines, with no persistent-naming machinery
  in either kernel.
- **Limit:** a face that a later cut removes entirely isn't noticed. A sketch on it stays
  where the face was. Finding that out needs ADR 0014's kernel naming, which this doesn't
  build.
- **Limit:** a face split by a later cut still gives one plane, which is all a sketch needs.
- **Limit:** the analytic kernel cuts and joins exactly only on parallel planes, such as a
  pocket from a top or bottom face. A cut from a side face needs OCCT, as non-parallel cuts
  always have.
- Drawing seen from behind the plane is allowed within the 70°; the sketch then reads
  mirrored.
- Every file is rewritten once, at schema 5.

## Alternatives considered

- **`Sketch.on` beside `plane`.** A face sketch would carry a plane that means nothing.
  Rejected.
- **The face's plane copied into the sketch.** Simplest, but it doesn't follow the face.
  Andre chose following.
- **Faces named by the kernel through every boolean (ADR 0014 in full).** It catches a
  removed or split face, but a command would need a kernel, and it is several times the
  work. Deferred until a feature needs edges, such as fillets.
- **A face sketch's axes taken from the extrude's sketch.** A side face's axes would then be
  tilted, and the camera would need roll. Rejected for the level rule.
- **A signed depth instead of `reversed`.** Rejected: depth stays a positive length, as
  every dimension is.
- **Drawing at any angle, up to edge-on.** Picking falls apart near edge-on. Andre chose
  70°.
- **Picks ranked by distance on screen.** Every picker would need a metric. Rejected: the
  queries stay the only hit-testers, and the 70° limit bounds the difference.
