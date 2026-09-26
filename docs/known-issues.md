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
| AI side | AI-1, AI-2, AI-3 | AI-9, AI-10 | AI-4 |
| AI side, untested or limited | AI-5, AI-6, AI-7, AI-8 | | |
| Client side | C-1, C-2, C-3, C-4 | C-5, C-6 | C-7, C-8, C-9, C-10, C-11 |

---

## AI side

### AI-1. Checks run with no pending proposal are dropped
- **What happens:** Claude runs `run_check` and gets a pass or fail, but the check is kept
  only if a proposal is pending. If you've just accepted, or Claude only looks, the check
  never reaches the Checks panel. In the second stress test (a 28-entity part), 30 of 36
  passing checks were lost this way.
- **Where:** `caliper/ai/draft.py`, `Draft.call`. With no draft open, the call runs on a
  throwaway `Workspace`.
- **Workaround:** have Claude check before you accept, while its changes are still pending.
- **Fix needs a decision:** either record a check-only call as a small proposal, or add it
  to the Checks panel directly (which is also blocked by C-1).

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

### C-5. Accepting a very large proposal freezes the window
- **What happens:** Accept runs every command through the solver once, on the UI thread.
  That's about 0.7 s at 128 changes and 9.7 s at 254.
- **Where:** `AgentController.accept` in `caliper/app/agent/ui.py`, and the solver's cost per
  command.
- **Status:** Performance V2 (engine), paused.

### C-6. Each AI change briefly blocks the window
- **What happens:** MCP calls run on the UI thread, one at a time. That's about 25–35 ms
  each around 130 pending changes, and 0.2 s at 250. A slow solve blocks the window for as
  long as it takes.
- **Where:** `caliper/app/agent/mcp_host.py`, `McpHost.handle`.
- **Fix:** mostly the solver (Performance V2). Moving the draft off the UI thread would
  need care, because it has to stay in step with the document.

### C-7. Solver round-off is saved in files
- **What happens:** after a solve, coordinates that should be exactly 0 can be stored as
  e.g. `8.6e-78`. That was seen on construction lines after a resize. It's harmless but
  untidy, and it makes files noisier to diff.
- **Where:** the engine solver writing values (`caliper/engine/constraints/sketch.py`,
  `_written`).
- **Fix:** snap values within a tiny tolerance of a round number, deterministically.

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

### C-10. The Assistant log grows without limit
- **What happens:** every request and tool call is another line, and there's no clearing or
  collapsing.
- **Where:** `caliper/app/panels/assistant.py`.

### C-11. App tests abort in a shell with no display
- **What happens:** `uv run pytest` aborts with a Qt fatal error in shells without a window
  server (SSH, sandboxes) unless `QT_QPA_PLATFORM=offscreen` is set. The test setup documents
  it but doesn't set it; CI does.
- **Where:** `tests/app/conftest.py`.
- **Fix:** set the default in `conftest.py`, or in the pytest config.

---

## Recently fixed

- Box-selecting many curves (e.g. a whole traced outline) froze the window for good. The
  Constrain menu asked which constraints apply, and `relations.match` tried all n! orders of
  the references before finding that no rule takes more than three: fixed on
  `shared/fix-constrain-menu-hang`, with a regression test.
- A fillet's tangent constraint was rejected as redundant: [#35](https://github.com/andrefongkc-cyber/caliper/pull/35).
- Large proposals replayed every command on every change, so checks timed out: [#35](https://github.com/andrefongkc-cyber/caliper/pull/35).
- The proposal card grew past the window: [#35](https://github.com/andrefongkc-cyber/caliper/pull/35).
- A second Caliper window took the MCP socket from the first: [#34](https://github.com/andrefongkc-cyber/caliper/pull/34).
- `solve_status` redid the whole document, and suggestions took 3.4 s ([#28](https://github.com/andrefongkc-cyber/caliper/issues/28) items 2 and 3): [#31](https://github.com/andrefongkc-cyber/caliper/pull/31).
- Committing a transaction sent no `Change`, and there were no position checks ([#17](https://github.com/andrefongkc-cyber/caliper/issues/17) items 2 and 5): [#29](https://github.com/andrefongkc-cyber/caliper/pull/29).
- A spatial index for picking ([#17](https://github.com/andrefongkc-cyber/caliper/issues/17) item 4): [#31](https://github.com/andrefongkc-cyber/caliper/pull/31).
- An app test failed when OCCT was installed ([#21](https://github.com/andrefongkc-cyber/caliper/issues/21) item 1): the test now pins "no kernel".
