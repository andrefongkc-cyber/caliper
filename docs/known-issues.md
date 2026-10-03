# Known issues

What breaks in Caliper today, on `main` at `b598af0` (2026-09-30, the N phase merged in #54),
updated for V2 (F1 to F8) on `shared/v2-milestone`. Each entry was checked against the code or
found in a test run. It's sorted by side:

- **AI side**: `caliper/ai`, the `caliper-mcp` server, the in-app assistant, and Claude Desktop
  setup.
- **Client side**: the Caliper app (`caliper/app`) and the engine under it.

On each side, problems that stop work come first, then slowness, then cosmetic issues. When
something is fixed, move it to [Recently fixed](#recently-fixed) with its PR.

| | Breaks work | Slow | Cosmetic |
|---|---|---|---|
| AI side, untested or limited | AI-6, AI-8 | | |
| Client side | C-13 (limited), C-15 (needs OCCT) | C-6 | C-16, C-17, C-18 |

---

## AI side

### AI-6. The in-app assistant has never talked to the real Claude API
- **What happens:** unknown. The Claude adapter is tested only against a fake SDK client.
  The request shape, including the `server-side-fallback-2026-07-01` beta, hasn't been
  confirmed against a live API.
- **Where:** `caliper/ai/claude.py`.
- **Next:** one live run with `CALIPER_ASSISTANT=claude` and a key in your shell. Deferred on
  2026-09-30 (N11), by Andre's decision: no API key will be provided, so nothing was sent, no
  SDK was installed, and the adapter wasn't changed. It stays open until someone runs it with
  their own key.

### AI-8. MCP needs macOS or Linux
- **What happens:** the bridge uses a Unix socket, so on Windows the window can't serve
  Claude Desktop (it says so in the status bar).
- **Where:** `caliper/ai/bridge.py` and `caliper/app/agent/mcp_host.py`.

---

## Client side

### C-13. A slot drawn as a rectangle and two end arcs has no area
- **What happens:** an area check, or the Checks panel's area, refuses a slot built the way
  the stress-plate prompt asks for it, a rectangle with semicircle ends: "the profile isn't
  closed". The arcs end at the rectangle's corners, which aren't edge ends, so the slot isn't
  one loop; its outline would need the rectangle's short sides left out, which is a union.
- **Where:** `caliper/engine/profiles.py`, `find`.
- **Workaround:** draw the slot as one outline of two lines and two arcs (`create_outline`, or
  lines and arcs joined end to end): it's a loop, and has an area.

### C-15. Without the `occt` extra, the app has no solids or volumes
- **What happens:** a part's solid is worked out by a geometry kernel (ADR 0013). The app uses
  OCCT, so without the `occt` extra, `solid_properties`, `mesh`, and volume checks answer
  `kernel.unavailable`. Volume checks are still stored, and measured wherever a kernel is,
  as area checks are. Engine tests and the bench use the analytic kernel instead, which builds
  extrusions exactly but can't combine solids that overlap.
- **Where:** `caliper/engine/geometry/__init__.py` (`default_kernel`).
- **Fix, if it matters:** `uv sync --extra occt`. Or fall back to the analytic kernel in the
  app for the parts it can build, a decision for ADR 0001's successor rather than a quiet
  default.

### C-6. An edit that moves a large, tightly joined shape factorizes again what it moved
- **What happens:** MCP calls run on the UI thread, one at a time. A command solves only the
  clusters it touches, and a redundancy check carries on from whichever of the last few
  checks' factorizations starts with the most of the same rows, up to the first row that
  changed: across geometry newly joining the cluster, wherever its columns fall, and across a
  check of another cluster in between (see Recently fixed). But an edit that moves geometry
  changes the rows of everything it moved, and those are factorized again.
- **Where:** `caliper/engine/constraints/sketch.py`, `_redundancy`, `_extended`, and
  `_truncated`; called from `caliper/app/agent/mcp_host.py`, `McpHost.handle`.
- **Fix, if it matters:** update the factorization in place for rows that changed (a rank
  update), or check only the part of a cluster a command can move. Neither gives the bits a
  fresh factorization gives, so either would change which near-threshold relations are
  accepted; Performance V2.2 keeps the solver bit-identical (Andre, 2026-10-01), so it's a
  decision of its own.
- **Measured 2026-10-02 (Performance V2.2, Perf-5):** the earlier diagnosis was wrong. The
  12-point star's checks didn't start again because rows moved (they hadn't), but because
  each copy's columns were inserted in the middle of the order, and a 150-constraint chain's
  because each new line's own cluster was checked between two checks of the chain. Carrying
  on across both: the star's circular pattern 333 → 188 ms, the chain's 299 calls 2.6 → 1.0 s
  (median 1.6 → 0.5 ms, p95 35 → 12 ms).

### C-16. A proposed extrude can't be seen before it's accepted
- **What happens:** a proposal is drawn as dashed geometry on the sketch it changes. An
  extrude changes no geometry, so there is nothing to draw: the card, over the 3D view, lists
  it, and its volume check is measured on the proposed solid, but the 3D view shows the solid
  as it is until Accept.
- **Where:** `caliper/app/viewport/view3d.py`, which meshes `session.document`.
- **Fix, if it matters:** mesh `proposal.result` in the 3D view while a proposal is shown,
  in the agent colour. The engine's `mesh` query already works on any document.

### C-17. Drawing in 3D needs the view to face the sketch
- **What happens:** a sketch is edited in 3D facing its plane (ADR 0015). Orbited away, the
  view only looks: clicks turn it, and drawing waits until N faces the sketch again. Onshape
  lets you draw on a plane seen at an angle.
- **Where:** `caliper/app/viewport/backdrop.py`, and the canvas's view, a scale and an offset.
- **Fix, if it matters:** give the canvas an affine view, which a slanted plane needs, through
  its picking, snapping, and dimension labels.

### C-18. Switching tabs drops a pending proposal
- **What happens:** each tab is its own document, and Claude works on the one shown. Switching
  tabs while Claude's proposal waits drops it, as opening a file does, and Claude's next call
  is told the document changed.
- **Where:** `caliper/app/agent/ui.py` and `mcp_host.py`, on `document_replaced`.
- **Fix, if it matters:** keep a proposal with its tab, and show it again on the way back.

---

## Recently fixed

In [#54](https://github.com/andrefongkc-cyber/caliper/pull/54) (the N phase):

- Area checks of lines and arcs: an area was one rectangle or circle, so an area check of a
  traced outline, such as the stress plate's, was refused whatever it made. A closed profile is
  now lines and arcs joined end to end, with holes; what isn't one is refused with the reason
  (N4). The slot built from a rectangle and arcs still isn't one (C-13, above).
- A check was refused when the machine had no geometry kernel: without the `occt` extra every
  area check was "install the occt extra", even of a valid profile. It's stored, and measured
  wherever a kernel is.
- Changes from Claude Desktop were held back while the in-app assistant worked only for command
  tools; checks, undo, and the repeat and drawing tools went through. Every change waits now.
- The proposal card named every check a proposal removed, one red line each: clearing the
  stress plate pushed Accept off the card. More than two are counted in one line.
- History called an edit to a check "Change Expected"; it's "Edit Check", and checks can be
  edited from the Checks panel by keyboard (N7).

On `shared/performance-v2.2` (Performance V2.2, stacked on the 3D sketching, not pushed):

- C-6, mostly: a redundancy check carries on across columns inserted anywhere (geometry
  joining the cluster) and from any of the last four checks (another cluster's in between),
  still to the bit: the star's pattern 333 → 188 ms, the chain 2.6 → 1.0 s. Edits that move
  a whole cluster still factorize it again (above).

On `shared/v2-3d-sketching` (ADR 0015):

- A part started in the 2D tab and was only seen in 3D, and a sketch was edited by jumping to
  2D, with no way to say it was done. The 3D tab is now the part, from its Top, Front, and
  Right planes; a sketch is edited in 3D facing its plane, and closed with Finish or Cancel.
  The 2D tab is a sketch to test on, with its own file.

On `shared/v2-milestone` (V2's F6 and F7):

- C-14: a part with more than one sketch couldn't be worked on in the app: every sketch was
  drawn on one canvas, and drawing was refused with `sketch.required`. Sketch mode edits one
  sketch at a time. The canvas, the tools, the browser, and Select All see only the active
  sketch, and drawing goes into it. A sketch on XZ or YZ is drawn in its own 2D coordinates.
  The 3D view shows the solid, but not the sketches on their planes yet.
- With two sketches, the in-app assistant's and Claude Desktop's drawing calls were refused
  with `sketch.required` unless the model named a sketch. They now draw in the sketch the user
  is editing, as the window's tools do, and the document summary says which one that is (F7).
- With two sketches, the Checks panel's "Sketch width" and "Sketch height" measured every
  sketch's geometry at once and were refused. They measure the sketch being edited (F7).
- A proposal's dashed preview drew another sketch's changes on the edited sketch's canvas, in
  the wrong place. It shows only the edited sketch's (F7).
- An extrude proposed with its volume check was called "Assistant Changes" on the card and in
  the undo menu, because a change to no entity looked like one more check. It's "Extrude" (F7).

On `shared/n-phase-final` (closing out the N phase):

- `bench/perf.py` crashed since #51, on `Draft.checks` and an old `prepare` signature, both
  gone now that checks are commands in a proposal. It runs again, counting the same checks.

On `contracts/checks-authors-labels` (stacked on the known-issue fixes, not pushed):

- C-1: checks were lost on save and reopen, New, or Open. They're stored in the document now
  ([ADR 0010](adr/0010-checks-in-the-document.md), Option A of
  [#16](https://github.com/andrefongkc-cyber/caliper/issues/16)): made by `CreateCheck`,
  edited and deleted by the usual commands, undone like any change, and saved with the part
  (file schema 3). A check whose geometry is deleted stays, failing, until it's put right.
- AI-1, changed: a check Claude runs with nothing pending is a change now, so it's proposed
  for you to accept rather than put straight into the Checks panel.
- Found on the way: a mirror, pattern, or outline as Claude's first change after an Accept
  was reported as applied and then dropped. It starts a proposal now.
- C-3: horizontal and vertical dimensions from scripts and the AI placed their labels by a
  different rule than the app. There's one rule now, the one the canvas draws by: the offset
  runs from the midpoint, square to what's measured, so a positive one puts a horizontal
  dimension above its points and a vertical one to their left, and the tool descriptions say so
  ([#28](https://github.com/andrefongkc-cyber/caliper/issues/28) item 1).
- C-4: changes a script made on the bus showed no author in History. Every `Change` says who
  asked for it (`Change.source`), and History shows it
  ([#17](https://github.com/andrefongkc-cyber/caliper/issues/17) item 1).
- C-6, further: a redundancy check carries on from the last factorization up to the first
  changed row, not only when every row is the same (the plate session: 0.54 s to 0.50 s).
  Edits that move a whole cluster are still slow (above).

On `shared/known-issue-fixes` (stacked on the audit, not pushed):

- C-12: a solve that squeezed geometry to nothing to make a constraint hold was accepted.
  Collapse is now judged at the larger of the solve's start and end scales, so it's refused.
- C-6, for runs of commands: each redundancy check factorized its whole cluster afresh, so a
  pattern's commands cost more each time (a 12-point star from one circular pattern: 2.3 s).
  A check now carries on from the last one's factorization when it starts with the same rows,
  to the bit: the star takes 0.34 s. An edit that moves geometry still starts again (above).
- AI-9: `create_arc_through_points` works out an arc from three points.
- AI-10: `create_outline` draws a chain of lines and arcs and joins it, optionally closed and
  tangent at chosen joints, in one call.
- AI-11: linear and circular patterns take arcs (held by their ends and a construction point
  at the centre, as mirror holds them). Rectangles still can't be turned: they're always
  axis-aligned.
- AI-2: `remove_check` takes a check off the proposal; running one again corrects its value.
- AI-7: the in-app assistant sends only its last four turns.
- AI-3: the recommended Claude Desktop config gives the server its own environment,
  `.venv-mcp`, from the same checkout ([mcp.md](mcp.md#set-up)). Update your config to it.
- C-8: the empty-sketch hint hides under a first proposal.
- C-9: the proposal's list of changes names references as `e1.end`.

On `shared/2d-v1-audit` (the 2D V1 audit, stacked on #48, not pushed):

- C-2: tangency to a rectangle's side at its corner, or through points joined between the arc
  and the line, was refused as redundant. Joints now follow chains of coincident points,
  take a line's or arc's midpoint as on it, and know a corner is on its two sides. The slot
  workaround (the arc's centre vertical with the corner) still works; one tangency now does
  the same, and a second is refused as really implied.
- AI-4: the note before a result now ends with a blank line, so clients that join text blocks
  don't glue it to the JSON.
- AI-5: `run_check` says what each metric measures and reads.
- AI-2, partly: running a check of the same measurement again replaces the earlier one.
- C-11: app tests default to `QT_QPA_PLATFORM=offscreen`.
- A constraint that repeats part of what others say (a fix on a line already horizontal) was
  refused as "already implied"; it now says it's partly implied and what to do. A dimension
  value no geometry can meet no longer names itself as the conflict.

On `shared/performance-v2` (Performance V2 and the numerical pass after it, not merged yet):

- C-7, mostly: solver round-off was saved in files (a 0 stored as 8.6e-78 after a resize).
  A value a solve didn't need to change now keeps its stored value, so a 0 stays 0 and a
  typed 1.5 stays 1.5; round-off can still appear in a value a solve really moved.

- AI-1: checks Claude ran with no change pending (after an Accept, or when it only looked)
  never reached the Checks panel. Now they go straight there, from Claude Desktop and from
  the in-app assistant.
- C-5: accepting a large proposal froze the window (25 s for the 255-change stress plate).
  Accept now commits what the draft already validated and solved: 0.02 s.
- C-6, mostly: each MCP call slowed as the proposal grew (1.1 s at worst in the stress
  plate, 0.46 s at the 95th percentile). Now 1.2 ms typically and 36 ms at worst; what's
  left is above.
- C-10: the Assistant log laid out every line again on each call. It's plain text now and
  shows the last 5,000 lines, keeping every one for `lines()`.

Earlier:

- Box-selecting many curves (e.g. a whole traced outline) froze the window for good. The
  Constrain menu asked which constraints apply, and `relations.match` tried all n! orders of
  the references before finding that no rule takes more than three:
  [#39](https://github.com/andrefongkc-cyber/caliper/pull/39), with a regression test.
- A fillet's tangent constraint was rejected as redundant: [#35](https://github.com/andrefongkc-cyber/caliper/pull/35).
- Large proposals replayed every command on every change, so checks timed out: [#35](https://github.com/andrefongkc-cyber/caliper/pull/35).
- The proposal card grew past the window: [#35](https://github.com/andrefongkc-cyber/caliper/pull/35).
- A second Caliper window took the MCP socket from the first: [#34](https://github.com/andrefongkc-cyber/caliper/pull/34).
- `solve_status` redid the whole document, and suggestions took 3.4 s ([#28](https://github.com/andrefongkc-cyber/caliper/issues/28) items 2 and 3): [#31](https://github.com/andrefongkc-cyber/caliper/pull/31).
- Committing a transaction sent no `Change`, and there were no position checks ([#17](https://github.com/andrefongkc-cyber/caliper/issues/17) items 2 and 5): [#29](https://github.com/andrefongkc-cyber/caliper/pull/29).
- A spatial index for picking ([#17](https://github.com/andrefongkc-cyber/caliper/issues/17) item 4): [#31](https://github.com/andrefongkc-cyber/caliper/pull/31).
- An app test failed when OCCT was installed ([#21](https://github.com/andrefongkc-cyber/caliper/issues/21) item 1): the test now pins "no kernel".
