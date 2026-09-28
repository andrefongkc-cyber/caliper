# Known issues

What breaks in Caliper today, on `main` at `418c657` (2026-09-26). Each entry was checked
against the code or found in a test run. It's sorted by side:

- **AI side**: `caliper/ai`, the `caliper-mcp` server, the in-app assistant, and Claude Desktop
  setup.
- **Client side**: the Caliper app (`caliper/app`) and the engine under it.

On each side, problems that stop work come first, then slowness, then cosmetic issues. When
something is fixed, move it to [Recently fixed](#recently-fixed) with its PR.

| | Breaks work | Slow | Cosmetic |
|---|---|---|---|
| AI side | AI-2, AI-3 | AI-9, AI-10 | AI-4 |
| AI side, untested or limited | AI-5, AI-6, AI-7, AI-8 | | |
| Client side | C-1, C-2, C-3, C-4 | C-6 | C-8, C-9, C-11, C-12 |

---

## AI side

### AI-2. A check can't be taken back
- **What happens:** every check Claude runs stays on the proposal. A check with the wrong
  expected value shows as failing and can't be removed; `undo` only takes back changes.
- **Where:** `caliper/ai/tools.py`, `Workspace._run_check` and `_undo`.
- **Workaround:** reject the proposal and ask again.
- **Fix:** let `undo` also take back the last check, or add a `remove_check` tool.

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

### AI-4. The "what happened" note runs into the tool result
- **What happens:** when a result carries a note (e.g. "The user accepted your pending
  changes"), some clients show it glued to the JSON:
  `…part of the sketch.{"actual": 50.0, …`.
- **Where:** `caliper/ai/mcp_server.py`, `call_tool`. The note is its own text block, but
  Claude's app joins blocks with no separator.
- **Fix:** end the note with a blank line.

### AI-5. The tools don't say what each check metric means
- **What happens:** `run_check` lists metric names only. The contract says `distance_x` and
  `distance_y` are absolute distances, but the tool doesn't, so Claude has to guess. In the
  stress test it ordered points defensively.
- **Where:** `caliper/ai/tools.py`, the `run_check` spec. The meanings are in
  `caliper/contracts/queries.py`, `Metric`.
- **Fix:** put each metric's docstring into the tool description.

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

---

## Client side

### C-1. Your checks are lost on save and reopen
- **What happens:** checks live only in the open session. Save and reopen, or New or Open,
  and the Checks panel is empty.
- **Where:** `caliper/app/session.py` (`_checks`, cleared in `replace`).
- **Status:** needs an ADR, [#16](https://github.com/andrefongkc-cyber/caliper/issues/16).
  Option A (expectations in the document, changed by commands) is recommended there.

### C-2. Tangency through a point in between, or to a rectangle's side, is rejected
- **What happens:** "tangent" is rejected as redundant when the arc's end is joined to the
  line *through another point*, or when the straight side belongs to a rectangle. That's the
  same first-order blind spot #35 fixed for direct joins. Both cases were reproduced on
  `main`.
- **Where:** `caliper/engine/constraints/sketch.py`, `_joints`. It only recognises an arc
  end coincident directly with a `Line`, `Arc`, or `Circle`.
- **Workaround:** join the arc's end straight to the line, and use lines rather than a
  rectangle.
- **Fix:** follow chains of coincident points, and treat rectangle sides as lines.

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
  cluster) took 20–35 ms, the slowest calls of the run.
- **Where:** the solver's cost per cluster (`caliper/engine/constraints/sketch.py`), called
  from `caliper/app/agent/mcp_host.py`, `McpHost.handle`.
- **Fix, if it matters:** solve only the part of a cluster a command can move. Moving the
  draft off the UI thread would need care, because it has to stay in step with the document.

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

### C-11. App tests abort in a shell with no display
- **What happens:** `uv run pytest` aborts with a Qt fatal error in shells without a window
  server (SSH, sandboxes) unless `QT_QPA_PLATFORM=offscreen` is set. The test setup documents
  it but doesn't set it; CI does.
- **Where:** `tests/app/conftest.py`.
- **Fix:** set the default in `conftest.py`, or in the pytest config.

---

## Recently fixed

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
