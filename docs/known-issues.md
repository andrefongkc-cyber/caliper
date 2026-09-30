# Known issues

What breaks in Caliper today, on `main` at `418c657` (2026-09-26), updated for the branches in
review (2026-09-29). Each entry was checked
against the code or found in a test run. It's sorted by side:

- **AI side**: `caliper/ai`, the `caliper-mcp` server, the in-app assistant, and Claude Desktop
  setup.
- **Client side**: the Caliper app (`caliper/app`) and the engine under it.

On each side, problems that stop work come first, then slowness, then cosmetic issues. When
something is fixed, move it to [Recently fixed](#recently-fixed) with its PR.

| | Breaks work | Slow | Cosmetic |
|---|---|---|---|
| AI side, untested or limited | AI-6, AI-8 | | |
| Client side | C-1, C-3, C-4 | C-6 | |

---

## AI side

### AI-6. The in-app assistant has never talked to the real Claude API
- **What happens:** unknown. The Claude adapter is tested only against a fake SDK client.
  The request shape, including the `server-side-fallback-2026-07-01` beta, hasn't been
  confirmed against a live API.
- **Where:** `caliper/ai/claude.py`.
- **Next:** one live run with `CALIPER_ASSISTANT=claude` and a key in your shell.

### AI-8. MCP needs macOS or Linux
- **What happens:** the bridge uses a Unix socket, so on Windows the window can't serve
  Claude Desktop (it says so in the status bar).
- **Where:** `caliper/ai/bridge.py` and `caliper/app/agent/mcp_host.py`.

---

## Client side

### C-1. Your checks are lost on save and reopen
- **What happens:** checks live only in the open session. Save and reopen, or New or Open,
  and the Checks panel is empty.
- **Where:** `caliper/app/session.py` (`_checks`, cleared in `replace`).
- **Status:** needs an ADR, [#16](https://github.com/andrefongkc-cyber/caliper/issues/16).
  Option A (expectations in the document, changed by commands) is recommended there.

### C-3. H/V dimensions from scripts and AI place labels by a different rule than the app
- **What happens:** for a horizontal or vertical dimension between diagonal points, the
  engine measures the label offset perpendicular to a→b, while the app uses the axis normal.
  The app works around it, but scripts and the AI tools get the engine's rule, so labels can
  land somewhere unexpected.
- **Where:** `caliper/engine/constraints/dimensions.py`, `_offset`, bridged in
  `caliper/app/dimension_layout.py`, `engine_placement`.
- **Status:** a joint `contracts/` change,
  [#28](https://github.com/andrefongkc-cyber/caliper/issues/28) item 1.

### C-4. Changes from scripts show no author
- **What happens:** `Change` doesn't say who made a change. The app stamps "You" or "Agent"
  itself, but a script calling the bus directly shows up unattributed in History.
- **Where:** `caliper/contracts/commands.py`, `Change`.
- **Status:** [#17](https://github.com/andrefongkc-cyber/caliper/issues/17) item 1.

### C-6. An edit that moves a large, tightly joined shape takes tens of milliseconds
- **What happens:** MCP calls run on the UI thread, one at a time. A command solves only the
  clusters it touches, and a run of commands that adds to one cluster without moving it (a
  pattern, a mirror) reuses the last check's factorization (see Recently fixed). But an edit
  that moves the cluster's geometry, however slightly, changes its rows, so the check starts
  again: in the recorded plate, each constraint on the 12-point star (24 lines, one cluster)
  took 20–35 ms.
- **Where:** `caliper/engine/constraints/sketch.py`, `_redundancy` and `_extended`; called from
  `caliper/app/agent/mcp_host.py`, `McpHost.handle`.
- **Fix, if it matters:** update the factorization for rows that changed instead of starting
  again, or solve only the part of a cluster a command can move.

---

## Recently fixed

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
