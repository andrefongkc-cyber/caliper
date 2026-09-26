A "5 mm bearing" usually means a 5 mm bore. I'll draw a **625-size** deep-groove ball bearing in front view: **Ø5 bore, Ø16 outside**, with **7 individual Ø3 balls**. These are typical 625 proportions rather than one maker's datasheet.

First, checking that Caliper is open again after the force-quit, and what's in it:

I'll build the bearing fully constrained: four concentric circles for the rings (Ø5, Ø7.5, Ø13.5, Ø16), balls tangent to both rings so their size and pitch circle follow automatically, and equal-chord spacing for the 7 balls. Now computing the ball positions.

Creating the geometry: the origin point, the four ring circles, the dashed Ø10.5 pitch circle, and the seven balls.

All the elements are placed—origin, rings, pitch circle, and balls. Now I'll add seven dashed chord lines connecting adjacent ball centers and constrain them equal to space the balls evenly.

I'll set up the first batch of constraints: fixing the origin, making the rings and pitch circle concentric, driving the ring diameters, and placing the first ball's center on the pitch circle.

Next, I'll add the batch 2 constraints: each ball tangent to the inner and outer rings, plus leveling the first ball with the centre.

I've confirmed 34 degrees of freedom remain as planned (6 for ball angles, 28 for chords). Now I'll apply Batch 3: pinning each chord's ends to its two ball centers (14 constraints) and equalizing neighboring chords (6 constraints) to space the 7 balls evenly at 360°/7 apart.

The bearing is now fully constrained with zero degrees of freedom and no conflicts. I'll verify this final set of constraints matches the intended bearing design.

The bearing is drawn and fully constrained, and you've accepted all of it. All five checks pass. You accepted just before they ran, so they didn't reach the Checks panel; that's AI-1 in the known-issues list.

**What's in Caliper:** a 625-size deep-groove ball bearing in front view, with no shields, so the balls show.

| Part | Size | How it's held |
|---|---|---|
| Bore | Ø5 | driving dimension, concentric with the fixed origin |
| Inner ring (outside) | Ø7.5 | driving dimension, concentric |
| Outer ring (inside) | Ø13.5 | driving dimension, concentric |
| Outside diameter | Ø16 | driving dimension, concentric |
| 7 balls | Ø3 each | **tangent to both rings**, so their size and the Ø10.5 pitch circle follow from the rings |
| Ball spacing | 360°/7, 4.556 mm between centres | equal chords between neighbouring balls; the first ball is level with the centre |

**Checks:** Ø16 outside, Ø5 bore, a Ø3 ball, balls centred 5.25 mm from the centre, and ball-to-ball spacing 4.556 mm (about a 1.56 mm gap between balls). No conflicting or redundant constraints.

**Because it's parametric,** if you change the Ø13.5 or Ø7.5 ring dimensions, the balls resize and stay evenly spaced against both rings.

**What I assumed:**
- "5 mm" is the bore, and the size is 625 (5 × 16 × 5 mm).
- The ring thicknesses (1.25 mm) and the ball size are typical proportions I chose, not taken from a datasheet.

If you meant a 605 (5 × 14), a 695 (5 × 13), or a bearing with a 5 mm *outside* diameter, tell me and I'll redraw it.

`Status: 625 bearing drawn in Caliper and accepted (fully constrained, 5/5 checks); selection-hang fix and known issues in PR #39, next: review and merge #39`

- [ ] Review and merge #39 (the selection hang and the known-issues list)
- [ ] If you want the Ø5 bearing in another size (605, 695, or 5 mm outside), tell me which
- [ ] Optional: a side section view of the bearing (5 mm wide, with the ball seated in the rings)