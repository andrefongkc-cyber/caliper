Checking what changed in the two tool files before drawing.

Empty sketch. Starting with the plate outline.

Joining the fillets to the sides: ends coincident, then tangent.

All 8 fillet tangencies were accepted. Now the sides' directions, the corner on the origin, and the plate's sizes.

The plate is fully constrained. Next is the vertical centreline: construction geometry from the middle of the bottom edge to the middle of the top edge, which the symmetric features will hang on.

The centreline is held by the plate's two edge midpoints. Now the left slot: a rectangle and two end arcs, with the arc ends on the rectangle's corners.

As expected, Caliper rejected tangency to the rectangle's side as redundant. That's known issue C-2, still open. I'll use the workaround from the practices file: each arc's centre vertical with its corner, which makes the arc tangent.

1 degree of freedom left, as planned: the slot's position across the plate. Now mirroring the slot about the centreline.

The mirror worked in one call. The right slot is tied to the left one about the centreline. Now one dimension for the pair: 120 mm between the slot centres.

Both slots are fully constrained, centred at (60, 120) and (180, 120). Now the holes: one dimensioned hole, then a single linear-pattern call for the 5 × 4 grid.

All 20 holes came from one pattern call. The spacing dimensions are e78 (across) and e106 (up). Last, I'll centre the grid on the plate by putting the middle column on the centreline.

The grid is fully constrained and centred on the plate. Now the D cutout. There's still no three-point-arc tool (known issue AI-9), so I worked out the circle through the three points myself: centre (35, 133.75), radius 16.25. I'm dimensioning it from the plate's left and top edges, its nearest datums.

The D passes exactly through the three points and is fully constrained. Now the star's layout: a centre point at the middle of the centreline (the plate's centre), then R25 and R12 construction circles.

Now I'll draw the star's left half: 12 lines from the top point round to the bottom point. I'm placing them only roughly, to 2 decimals, and letting the constraints put them exactly.

Joining the 11 corners of the half.

Now the ends go on the centreline, and each corner goes on its circle: tips on R25, inner corners on R12.

Now equal edges, to space the corners evenly.

The star's half is fully constrained, and the constraints have put every corner exactly in place from the rough start. Now mirroring it to close the outline.

The star is one closed outline of 24 lines, fully constrained. The right half is 12 mirrored lines that meet the left half at the top and bottom points. Now the tiny hole, dimensioned from the right and top edges, then item 7's dimension.

Everything is at 0 degrees of freedom. The hole-to-slot dimension is a reference: both ends are already fixed, so it shows the measurement, 120, without adding a constraint. Now the checks.

All 12 checks pass. A final status check, then I'll tell Caliper I'm done so the timer stops.

The plate is drawn and fully constrained: 0 degrees of freedom, with nothing conflicting or redundant. All 12 checks pass, and it's one proposal for you to review and accept in Caliper. It took about 162 calls, against 281 last time, and I called `report_progress` at the start and at the end, so the timer should have stopped by itself.

**What Caliper rejected (1 change):**
- **Tangency between the left slot's left end arc (e43) and the top side of its rectangle:** rejected as "already implied by e45 (coincident)".
- This is known issue C-2 and it's still open. I tried it once to confirm; the constraint is wrongly flagged as redundant.
- **What I did instead:** I made each arc's centre vertical with its rectangle corner (e49, e50). The arc then meets the long side at a right angle, which is exact tangency. The right slot is a mirror, so it inherits the same fix.

Nothing else was rejected.

**How it was built, following the practices file this time:**
- **Plate:** four lines with R12 fillets, the corner held on the fixed origin. It has one R12 dimension and 240 × 160 dimensions measured from the origin. All 8 fillet tangencies were accepted.
- **Centreline:** a construction line from the middle of the bottom edge to the middle of the top edge. The slots, grid and star hang on it.
- **Slots:** I built the left slot, then mirrored it in one call. The pair has one 120 mm dimension between centres and sits 40 mm below the top edge. That gives centres at (60, 120) and (180, 120).
- **Holes:**
  - One hole with the single Ø6 dimension, 25 mm above the bottom edge.
  - One pattern call made the 5 × 4 grid. One 30 mm dimension each way sets the spacing.
  - The middle column sits on the centreline, so the grid stays centred if the plate changes.
- **D cutout:**
  - Caliper still can't draw an arc through three points (known issue AI-9), so I worked out the circle myself: centre (35, 133.75), R16.25.
  - It's dimensioned from the left and top edges.
  - The arc passes through all three points to within 0.00000002 mm.
- **Star:**
  - Its centre is the middle of the centreline, which is the plate's centre. It has R25 and R12 construction circles.
  - I drew the left half roughly and the constraints placed it exactly: corners on the circles, equal edges, ends on the centreline.
  - One mirror call made the other half, closing a single outline of 24 lines.
- **Tiny hole:** Ø0.2, 10 mm from the right edge and 10 mm from the top edge.
- **Hole-to-slot dimension:** added as a reference dimension, because both ends are already fixed. It reads 120.

**Checks (all pass):**

| Check | Result |
|---|---|
| Plate width / height | 240 / 160 |
| Slot centre-to-centre, both slots | 40 |
| Slot overall length, both slots | 50 |
| Slot width, both slots | 10 |
| Hole spacing, across / up | 30 / 30 |
| Star outer radius, on a mirrored tip | 25 |
| Tiny hole diameter | 0.2 |

**Layout overlaps, drawn as the prompt specified:**
- The top-row holes at (60, 115) and (180, 115) sit on the slots' bottom edges.
- The holes at (120, 85) and (120, 55) overlap the star.

**Saving:** test 002's folder already has a `003-timing.md`, and Caliper never overwrites one. Save this drawing into a new test folder instead, for example `004-stress-plate-build-rerun`, so this run's timing is written.

This drew the same plate as before in a little over half the calls. The second slot, 19 of the holes and half the star were each made in one step. The drawing now carries the design intent, so editing one dimension moves everything that depends on it. The one rejection is the known tangency issue, and the usual workaround handled it. What's left is for you to review and accept it, and to see whether the time-left counter and auto-stop behaved on screen.

Status: mirror and linear pattern tools, and the Timing panel's time left and auto-stop, on `shared/mirror-pattern-time-left` (stacked on `shared/performance-v2`, local, not pushed), next: a live rerun of test 002 on it from Claude Desktop, then Andre's review

- [ ] Accept the proposal. Note the Accept time, whether the time-left counter showed, and whether the time stopped when I reported done
- [ ] Save into a new test folder, e.g. `004-stress-plate-build-rerun`, and fill in its `005-my-results.md`
- [ ] Run the same prompt from Claude Desktop, to see whether it picks up the new tools and practices by itself
