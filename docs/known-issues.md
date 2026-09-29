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
| AI side | AI-2, AI-3, AI-11 | AI-9, AI-10 | |
| AI side, untested or limited | AI-6, AI-7, AI-8 | | |
| Client side | C-1, C-3, C-4 | C-6 | C-8, C-9, C-12 |

---

## AI side

### AI-2. A check that shouldn't be there can't be taken back
- **What happens:** every check Claude runs stays on the proposal, and `undo` only takes back
  changes. A check of the right measurement with the wrong value can now be put right by
  running it again (see Recently fixed); a check of something that shouldn't be checked at
  all still stays.
- **Where:** `caliper/ai/tools.py`, `Workspace._run_check` and `_undo`.
- **Workaround:** reject the proposal and ask again.
- **Fix:** a `remove_check` tool, or `undo` taking back the last check too.

### AI-3. Claude Desktop loses Caliper when the checkout or `.venv` changes
- **What happens:** Claude Desktop runs
  `uv run --directory <checkout> --extra mcp caliper-mcp`. If the checkout is on a branch
  without the MCP code, or the shared `.venv` is re-synced without it, `caliper-mcp`
  disappears and Claude Desktop shows no Caliper tools. Both happened on 2026-09-26. The same
  shared `.venv` also lost `pytest-qt` once, so every app test failed with
  `fixture 'window' not found`.
- **Where:** the Claude Desktop config in [docs/mcp.md](mcp.md), and one `.venv` shared by
  development and Claude Desktop.
- **Workaround:** keep the checkout on `main`, and after switching branches run
  `uv sync --extra app --extra mcp --group app-test`.
- **Fix:** give Claude Desktop its own install of `caliper-mcp` (e.g. `uv tool install`),
  separate from the development `.venv`.

### AI-6. The in-app assistant has never talked to the real Claude API
- **What happens:** unknown. The Claude adapter is tested only against a fake SDK client.
  The request shape, including the `server-side-fallback-2026-07-01` beta, hasn't been
  confirmed against a live API.
- **Where:** `caliper/ai/claude.py`.
- **Next:** one live run with `CALIPER_ASSISTANT=claude` and a key in your shell.

### AI-7. The in-app assistant's conversation keeps growing
- **What happens:** each turn is capped (context size, 16 replies), but the conversation
  only resets when another document opens. A long session sends more history, and costs
  more, on every request.
- **Where:** `caliper/ai/agent.py`, `Assistant.conversation`.
- **Fix:** trim or summarise old turns.

### AI-8. MCP needs macOS or Linux
- **What happens:** the bridge uses a Unix socket, so on Windows the window can't serve
  Claude Desktop (it says so in the status bar).
- **Where:** `caliper/ai/bridge.py` and `caliper/app/agent/mcp_host.py`.

### AI-9. There's no way to draw an arc through three points
- **What happens:** `create_arc` takes a centre, radius, start angle, and sweep, so tracing a
  drawing means working out every arc's centre and angles outside Caliper. Drawing the bear
  (21 arcs) needed a separate script to fit arcs through points on the outline.
- **Where:** `caliper/ai/tools.py`. The tools mirror `CreateArc` in the contract.
- **Fix:** a tool that takes three points (or two ends and a point on the arc) and turns them
  into an ordinary `CreateArc` in the tool layer, so no contract change is needed.

### AI-10. Joining a traced outline takes one call per joint
- **What happens:** every corner of a profile needs its own `create_constraint` call. The
  bear's closed outline took 33, on top of 33 calls to draw it.
- **Where:** `caliper/ai/tools.py` (one tool per command, no batching).
- **Fix:** a profile tool that draws connected segments and joins their ends, made of the
  same `CreateLine`, `CreateArc`, and coincident `CreateConstraint` commands.

### AI-11. Arcs don't pattern, and rectangles don't turn
- **What happens:** `linear_pattern` and `circular_pattern` refuse arcs: no constraint ties a
  copy's angles to its original's (mirror has a way round it: a construction point at the
  centre). `circular_pattern` refuses rectangles, which are always axis-aligned. A slot or a
  gear tooth with arc ends can be mirrored but not patterned.
- **Where:** `caliper/ai/patterns.py`.
- **Workaround:** mirror the arcs, or draw them to the patterned points; draw a turned
  rectangle as four lines.
- **Fix:** tie an arc's copy through its ends and a construction point, as mirror does.

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

### C-6. A change to one large, tightly joined shape takes tens of milliseconds
- **What happens:** MCP calls run on the UI thread, one at a time. Most take a millisecond or
  two at any proposal size (Performance V2), but a command still solves the whole cluster it
  touches: in the recorded plate, each constraint on the 12-point star (24 lines, one
  cluster) took 20–35 ms, the slowest calls of the run. A repeat is many commands in one call,
  so it adds up: a 12-point star from one `circular_pattern` call is 162 commands in about
  2.3 s (`bench/perf.py`, `repeat/*`), while a 5 by 4 grid of holes is 133 in 0.22 s.
- **Where:** the solver's cost per cluster (`caliper/engine/constraints/sketch.py`): each
  command checks its new relations for redundancy against the whole cluster's rows, and the
  cluster grows with every copy. Called from `caliper/app/agent/mcp_host.py`,
  `McpHost.handle`.
- **Fix, if it matters:** solve only the part of a cluster a command can move, or keep the
  redundancy check's factorization from one command to the next (deferred in
  docs/workplan/core.md, Solver V2.1). Moving the draft off the UI thread would need care,
  because it has to stay in step with the document.

### C-8. The empty-sketch hint draws over a first proposal
- **What happens:** the "empty sketch" hint shows whenever the document is empty, including
  under a proposal's ghost geometry.
- **Where:** `caliper/app/viewport/canvas.py` (`empty_hint.setVisible`).
- **Fix:** hide it while a proposal is shown.

### C-9. The proposal's change list shows raw command text
- **What happens:** expanded, the list reads like
  `Ref(entity='e1', feature=<Feature.END: 'end'>)`.
- **Where:** `caliper/app/agent/ui.py`, `_command_text` and `_value`.
- **Fix:** format refs as `e1.end`, or reuse the History panel's labels.

### C-12. A constraint that holds only by squeezing geometry to almost nothing is accepted
- **What happens:** a vertical constraint between a 69 × 1 rectangle's bottom-left corner and
  its centre can only hold with no width at all. The solver squeezes the rectangle to 1e-9 mm
  wide and accepts it, with the constraint off by 5e-10 mm. The old solver did the same.
- **Where:** `caliper/engine/constraints/sketch.py`. Tolerances are relative to the sketch's
  size: Newton measures convergence at the size the solve started from (69 mm here), and the
  collapse check at the size it ended at (1 mm).
- **Fix needs a decision:** measure both at one size (the larger), which rejects this as a
  collapse. That changes which commands are accepted, in cases like this one only.
- **Pinned** as it is by `tests/engine/constraints/test_editing.py`, so a change to it is
  noticed.

---

## Recently fixed

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
