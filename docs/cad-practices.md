# CAD Practices for AI

Lessons from Caliper's manual AI/MCP test runs (`test-runs-andre/`) about how Claude builds
and constrains sketches: places where its approach was valid but more cluttered, fragile, or
harder to change than it needed to be. Consult it when planning a sketch's geometry and
constraints, before the first tool call. It builds on the tool instructions (`CONVENTIONS` in
`caliper/ai/tools.py`): lay out construction geometry first, then tie the part to it.

Each practice says how well it's supported. **Observed** means seen in a test run.
**Hypothesis** means it follows from what was seen but hasn't been tried in a run yet. The aim
is the simplest structure that holds the design intent: fewer dimensions, each relationship
stated once. None of this is a reason to add constraints for their own sake.

**What Caliper offers today**, so the practices stay within it:
- **Constraints:** coincident (also a point on a curve), concentric, horizontal and vertical (a
  line, or two points), parallel, perpendicular, tangent, equal (two lines, or two circles or
  arcs), midpoint, symmetric (two points about a line), fix, normal, and curvature.
- **Dimensions:** distance (aligned, horizontal, or vertical), radius or diameter, and angle. Each
  is driving, or a reference if it has no value.
- **Repeating:** `linear_pattern` (rows and grids of points, lines, circles, arcs, and
  rectangles), `circular_pattern` (points, lines, circles, and arcs round a centre), and
  `mirror_entities` (about a line), one call each, with the copies tied to the original.
- **Drawing:** `create_outline` (a traced outline of lines and arcs, joined, optionally closed
  and tangent at chosen joints) and `create_arc_through_points` (an arc from three points).
- **Rejections:** Caliper rejects a constraint that's already implied (`constraint.redundant`) or
  impossible (`constraint.conflict`).

**Practices by category**
- Construction geometry: repeated spacing; symmetry; an explicit layout, stated once.
- Dimensions: nearest datum; reference dimensions.
- Tangency and profiles: slots from lines and arcs.
- Constraint structure: each relationship once; degrees of freedom per feature.
- Geometry creation: let constraints place geometry.
- Verification: check while the proposal is pending.

---

## Practice: Construction lines for repeated spacing
*Construction geometry. Observed in 002; the preferred structure, with `linear_pattern`, ran in
003 (rerun 2).*

**Observation**
In 002 the 5 × 4 hole grid got 19 separate 30 mm spacing dimensions, 2 position dimensions
from the origin, and 19 horizontal or vertical alignments, each hole dimensioned from its
neighbour. It was correct and fully constrained, but 21 dimension labels crowd the grid, and
changing the spacing means editing 19 values. Andre's 002 notes: cluttered, and it could have
used construction lines and equal constraints instead.

**Preferred approach**
- In one call: `linear_pattern` on the dimensioned first feature. It joins each copy to the
  previous one with a construction line, all equal and parallel to the first, which carries
  one spacing dimension each way, and makes every copy equal to the original. In 003 the
  grid was 4 calls (the hole, its diameter, its height, the pattern) plus one to put the middle
  column on the centreline, fully constrained.
- By hand, the same intent: draw construction lines between neighbouring centres along one
  row and one column, with their ends coincident with the centres.
- Make the row lines horizontal and the column lines vertical.
- Make them all equal to one line, and dimension that one.
- Align every other hole to its neighbours with horizontal and vertical constraints, with no
  dimensions.
- Place the grid once, from its datum (see symmetry below).

In Caliper, `equal` works on lines and circles, not on the distance between two points, so a
construction line is how a repeated spacing is made equal.

**Why**
- Fewer dimensions: 4 instead of 22 for 002's grid (the diameter, one spacing, and two for
  position).
- Easier to modify: one edit changes every spacing.
- Clearer design intent: the grid reads as a grid.

**When to use**
Three or more features at the same spacing: rows, columns, and grids. For features on a circle,
see the layout practice below.

**When not to use**
- Two features, or spacings that differ; each of those is its own requirement.
- By hand when call count matters: each construction line costs a create and two coincidents,
  about 20 more calls than direct dimensions for 002's grid. `linear_pattern` is one call.

**Example**
002: 20 holes, Ø6 at 30 mm.
- Along the bottom row: 4 horizontal construction lines.
- Up the left column: 3 vertical construction lines.
- All 7 lines are equal, with one 30 mm dimension.
- The other 12 holes are aligned to their neighbours: 12 vertical and 12 horizontal
  constraints, no dimensions.

## Practice: Use the design's symmetry
*Construction geometry. Observed in 002; the preferred structure ran in 003 (rerun 2).*

**Observation**
002's features were laid out symmetrically about the plate's vertical centre, x = 120: the slots
at 60 and 180, the holes from 60 to 180, and the star at 120. Yet each feature was placed with
two dimensions from the origin point: 14 dimensions from the origin in all.

**Preferred approach**
- Draw a construction centreline. For 002 that's from the midpoint of the bottom edge to the
  midpoint of the top edge.
- Put centred features on it: the star's centre coincident with the line.
- Mirror paired features about the line: `mirror_entities` copies a finished feature (a
  slot's rectangle and arcs) and ties the copy with symmetric constraints; `symmetric` pairs
  two points you drew yourself.
- Dimension the pair once: the distance between the slots, or one slot from the line.

**Why**
- Fewer dimensions: the slots need 2 instead of 4, and the star 1 instead of 2.
- Easier to modify: widening the plate keeps everything centred.
- Clearer design intent: the pair can't drift apart unevenly.

**When to use**
When the design is symmetric: centred features, and features mirrored in pairs.

**When not to use**
- Features that are deliberately off-centre, such as 002's D-cutout and Ø0.2 hole. Dimension
  those from the edge or feature they relate to.
- Features that are only symmetric by coincidence and should move independently.

**Example**
002's slots: symmetric about the centreline, with 120 mm between their centres, and one height.

## Practice: Make the controlling layout explicit, and state it once
*Construction geometry. Observed in 001 (Andre's notes); the preferred approach is a
hypothesis.*

**Observation**
In 001 the seven balls were spaced by a construction polygon: seven equal chords between
neighbouring ball centres. They were held on the pitch circle only indirectly: ball 1 by a
coincident, the rest by being tangent to both rings. Andre's 001 notes: the construction
heptagon "isn't constrained to the center"; what controls the layout isn't visible in the
sketch.

**Preferred approach**
Decide which geometry drives the pattern, and constrain through it:
- **Either** every ball centre on a construction pitch circle concentric with the bearing,
  spaced by the equal chords;
- **or** the balls held by tangency to the rings, with the pitch circle kept only as a
  reference, with nothing constrained to it.

Not all of them: if the pitch radius, the ball radius, and both ring diameters are all driven,
tangency to the rings adds nothing, and Caliper rejects it as redundant. Pick which of these the
design drives.

**Why**
- Clearer design intent: the sketch shows what controls the arrangement.
- Less redundant constraint structure: each relationship is stated once.
- Easier to modify: changing the controlling dimension moves the pattern predictably.

**When to use**
Circular arrangements: balls, bolt holes, and teeth.

**When not to use**
When the arrangement is a consequence of other geometry you mean to drive. Balls sized to fit
between given rings, for example: tangency is then the intent.

**Example**
001: a 625 bearing, 7 balls, Ø10.5 pitch circle.

## Practice: Dimension from the nearest meaningful datum, with the label beside the feature
*Dimensions. Observed in 002; the preferred placement ran in 003 (rerun 2), from the plate's
edges and centreline.*

**Observation**
002's positions were dimensioned from the origin point, with label offsets chosen per call of
up to 260 mm from what they measure. On screen the labels overlapped in the middle of the plate,
and long leaders crossed the part. Andre: "everything is cluttered".

**Preferred approach**
- Measure a feature from what it relates to: a centreline, an edge, or its neighbour.
- Keep each label just outside the feature. In 002 that's a small offset, about 8–15 mm.
- The bigger win is needing fewer dimensions in the first place (the two practices above).

**Why**
- Clearer design intent: which edge each feature is measured from is obvious.
- Readable labels.
- Easier to modify: moving one feature doesn't drag long leaders across others.

**When to use**
Every placement dimension.

**When not to use**
When the origin, or the corner at the origin, really is the design's datum.

**Example**
002's D-cutout was dimensioned from the origin with 168 mm and 175 mm label offsets. Dimensions
from the plate's left and top edges would sit right beside it.

## Practice: Report already-determined values with reference dimensions
*Dimensions. Observed in 002; it worked.*

**Observation**
002's hole-to-slot dimension measured a distance that other constraints already fixed. A
driving 120 would have been rejected as redundant.

**Preferred approach**
Leave the value empty to make a reference (driven) dimension. It shows the measurement and adds
no constraint.

**Why**
- The value is shown without a redundant constraint.
- It follows the geometry when the geometry changes.

**When to use**
Requested dimensions on geometry that's already fully constrained.

**When not to use**
When the value is a requirement that should drive the geometry. Then remove whatever else fixes
it, rather than doubling up.

**Example**
002: from the bottom-left hole to the right slot's centre, reading 120 (reference).

## Practice: Build slots from lines and arcs, not a rectangle
*Tangency and profiles. Observed in 002 and 003; the workaround worked, and the problem behind
it (C-2) is fixed.*

**Observation**
In 002 each slot was a rectangle plus two semicircle arcs, because the prompt asked for that.
- Caliper rejected tangency from the arcs to the rectangle's long sides as redundant ("already
  implied by e45 (coincident)", known issue C-2).
- The rectangle's short sides stayed inside each slot as extra lines.

**Preferred approach**
Draw a slot as two lines and two arcs:
- Make the ends coincident, and each arc tangent to the lines it meets. Tangency at a direct
  arc–line joint is accepted since #35.
- Make the two arcs equal.
- Dimension the centre-to-centre length and the width.

If a rectangle is required, keep the arc ends on its corners, and make each arc tangent to
one long side: with its ends on the corners, that one tangency makes it a semicircle tangent
to both, and a second is refused as implied. Tangency to a rectangle's side at its corner is
accepted since the C-2 fix (2026-09-29); before it, the workaround was the arc's centre
vertical with its corner (horizontal, for a vertical slot), which still works.

**Why**
- A clean closed profile with no interior edges.
- Tangency Caliper accepts.
- Clearer design intent: the slot's length and width are its two dimensions.

**When to use**
Slots and other stadium shapes.

**When not to use**
Shapes whose ends aren't semicircles. A rectangle with rounded corners is `fillet_corner` on
four lines.

**Example**
002's slots: 40 mm centre to centre, 10 mm wide.

## Practice: Constrain each relationship once
*Constraint structure. Observed in 002; that the doubled version would be rejected is a
hypothesis.*

**Observation**
For 002's star, putting all 24 points on their circles and also mirroring them about the
centreline would state half of the mirror equations twice. The build instead:
- put only one half's points on the circles;
- mirrored the rest with 11 symmetric constraints;
- made 12 edges equal.

The result was 0 degrees of freedom, with no rejections.

**Preferred approach**
Give each piece of intent one constraint. Before adding one, ask whether it already follows
from others, for example symmetry plus a circle, equal edges plus a closed outline, or tangency
plus a pitch radius.

**Why**
- Less redundant constraint structure: no rejections mid-build.
- Easier to read, and easier to change without conflicts.

**When to use**
Always.

**When not to use**
Never add a truly redundant constraint. Some rejections are false, though (C-2): see the slot
practice.

**Example**
002's star: one half pinned to the circles, the other half mirrored. With `mirror_entities`
the other half is one call: 62 calls for the whole star, layout included, in a workspace
trial, against over 100 in the 002 run.

## Practice: Plan the degrees of freedom per feature, and confirm them with solve_status
*Constraint structure. Observed in 001 and 002; it worked.*

**Observation**
In 001 and 002 each feature was built with a planned degree-of-freedom (DOF) count and checked
with `solve_status`:
- 002's slots went from 12 to 8 to 0 DOF, as planned.
- The star went from 33 to 11 to 0 DOF.
- 001 showed 34 DOF left before its spacing constraints, as planned.

A wrong count would have shown a mistake where it was made, not at the end.

**Preferred approach**
- Count a feature's DOF first: a point 2, a line 4, a circle 3, an arc 5, a rectangle 4.
- Subtract what each constraint removes.
- Check `solve_status` after each group, and stop to look if the count differs.

**Why**
An under- or over-constrained feature is caught while it's small, not among 250 entities.

**When to use**
Any sketch with more than a few features.

**When not to use**
Tiny sketches, where one check at the end is enough.

**Example**
002: `solve_status` after the slots, the grid, the D-cutout, and each stage of the star.

## Practice: Let constraints place the geometry
*Geometry creation. Observed in 001, 002, and the gear run; the preferred approach ran in 003
(rerun 2).*

**Observation**
Claude computed exact coordinates before drawing:
- 001's ball positions came from a shell script.
- 002's 24 star points were worked out by hand to 6 decimals.
- The 16-tooth gear had every coordinate computed, and no construction geometry.

The constraints then only confirmed what the arithmetic had already placed.

**Preferred approach**
- Create geometry roughly where it goes: close enough that the solver picks the right solution
  (the right order, side, and orientation).
- Let the layout's dimensions and constraints put it exactly.
- Compute by hand only what Caliper can't express, such as a three-point arc's centre (AI-9).

**Why**
- Less arithmetic to get wrong.
- Clearer design intent: the intent lives in the constraints, not in coordinates.
- Faster to build.

**When to use**
Geometry that the layout fully determines.

**When not to use**
When a rough start could solve to the wrong configuration, such as a star's points crossing or
an arc flipping. Give a close start there. In 003 the star's half-points, drawn to 2 decimals,
solved into place; how much rougher a start the solver tolerates is untested.

**Example**
002's star: its points were fully determined by the circles, the mirror, and the equal edges,
so an approximate start should have been enough.

## Practice: Check while the proposal is pending
*Verification. Observed in 001, 002, and the 2026-09-25 stress test.*

**Observation**
- 001's five checks ran after the user had accepted, so none reached the Checks panel (known
  issue AI-1).
- The 2026-09-25 stress test lost 30 of 36 passing checks that way.
- In 002 every check ran before Accept, and all 10 reached the panel.

**Preferred approach**
Run a check for each size the request states as part of the proposal, before telling the user
to review and accept it.

**Why**
The checks travel with the proposal, stay in the Checks panel, and are re-measured after every
later change.

**When to use**
Every stated size, position, or distance.

**When not to use**
Quick looks where the value isn't a requirement. Use `measure_distance` for those.

**Example**
002: 10 checks run while the proposal was pending, all passing and all kept.

---

## Observed From Test Runs

| Run | Date | Contributed |
|---|---|---|
| `test-runs-andre/001-ball-bearing` | 2026-09-26 | Explicit layout; DOF per feature; constraints place geometry; check while pending |
| `test-runs-andre/002-stress-plate-build` | 2026-09-27 | Repeated spacing; symmetry; datum and labels; reference dimensions; slots; each relationship once; DOF per feature; constraints place geometry; check while pending |
| `test-runs-andre/003-stress-plate-rerun2` | 2026-09-28 | Repeated spacing and symmetry with `linear_pattern` and `mirror_entities`; datum dimensions; constraints place geometry (the star from a rough start); slots (C-2 again); reference dimensions |
| `test-runs-andre/004-stress-plate-main` | 2026-09-30 | Datum dimensions to a construction frame (no extension past a fillet); `circular_pattern` for the star; slots with no workaround (C-2 fixed); checks stored with the proposal; reference dimensions |
| 16-tooth gear (`docs/workplan/ai.md`, Layout first) | 2026-09-26 | The layout-first rule now in `CONVENTIONS`; constraints place geometry |
| MCP stress tests (`docs/known-issues.md`, AI-1) | 2026-09-25 | Check while pending |

When a new run confirms, refutes, or adds a practice, update it here, and move it from
Hypothesis to Observed only when a run has actually shown it.
