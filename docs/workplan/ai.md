Status: the N phase's AI side on `shared/n-phase` (local, not pushed): test 004 recorded over MCP from Claude Code (N9, in part), the live in-app assistant run blocked with no API key here (N11, AI-6 unverified); next: a Claude Desktop run on `main` and one live API request when a key is set

# AI workplan

The assistant: a model that understands a request and does it through Caliper's own commands and queries. Two ways in share one set of tools: **MCP (primary)**, where Claude Desktop is the model and calls Caliper's tools through `caliper-mcp`, and the **direct Anthropic API (secondary)**, the in-app assistant in the prompt bar. `caliper/ai/` is headless and imports `contracts` and `engine` only; the shell hosts it. Owner: Andre (Lucas reviews, as for every area). AI-only work goes on `ai/<topic>` branches, which may touch `caliper/ai/`, `tests/ai/`, this file, and `docs/adr/`; anything touching the shell or shared files goes on `stream/shell` or `shared/`.

Markers: `[ ]` not started · `[~]` in progress · `[x]` done

**Picking this up in a fresh session.** Everything is on `main`: the assistant (#32), MCP (#34), and the stress-test fixes (#35); 1138 passed, 16 skipped. Offline: `uv run pytest tests/ai tests/app/test_mcp.py tests/app/test_assistant.py`. With Claude Desktop: [docs/mcp.md](../mcp.md). Direct API: `uv sync --extra ai`, `ANTHROPIC_API_KEY` in your shell, `CALIPER_ASSISTANT=claude uv run python -m caliper.app`.

## The N phase, AI side (branch `shared/n-phase`, 2026-09-30)

- [~] **N9, the stress plate live on `main`**: run from Claude Code over MCP, not Claude Desktop, and recorded as [test 004](../../test-runs-andre/004-stress-plate-main/) (prompt, output, results). 0 DOF, nothing rejected, 12 of 12 checks, about 101 calls (one mirror, one linear and one circular pattern), `report_progress` at both ends (98, then 0). Its checks came back with ids, so they were stored with the proposal, and after Accept the sketch held them (12 checks among 402 entities). Not covered, so not claimed: Claude Desktop's own behaviour (does it call `report_progress` and the repeat tools unprompted?), the time left as shown on screen, and Save and reopen in the window (automated instead: N10). The folder lacks `003-timing.md`, the drawing, and `005-my-results.md`, which Caliper and Andre write. The MCP connection in that session listed the older tool set (no `create_outline`, `create_arc_through_points`, or `remove_check`), so it was started from older code than the app's
- [ ] **N11, one live request through the Claude adapter**: blocked. No `ANTHROPIC_API_KEY` in this environment (checked without printing it), no `.env`, and the `ai` extra isn't installed; nothing was installed or faked. AI-6 stays unverified
- [x] Changes wait while the in-app assistant works: `caliper.ai.tools.CHANGES` names every tool that can change the sketch (commands, repeats, drawing tools, `run_check`, `remove_check`, `undo`); the MCP host refuses those while the assistant is busy, where it refused only command tools (N6)
- [x] `run_check`'s `area` metric says what it reads: one closed profile, its outline (a circle, a rectangle, or lines and arcs joined end to end, in any order) and any holes inside it (N4)

## Checks are changes (branch `contracts/checks-authors-labels`, 2026-09-29; local)

C-1 put checks in the document ([ADR 0010](../adr/0010-checks-in-the-document.md)), so the AI side uses the same commands the Checks panel does (invariant 5):

- [x] `run_check` measures a check, then stores it with `CreateCheck`, or corrects the sketch's check of the same measurement (the model's, the user's, or one accepted earlier) with `ModifyEntity`; the result names the check's id. A check that can't be measured is reported and not stored. `remove_check` deletes the sketch's check of a measurement with `DeleteEntities`. Both are changes: undo takes them back, and over MCP they are no longer read-only
- [x] No `create_check` tool (`COMMAND_TOOLS`): one way to check. The MCP server still offers 28 tools
- [x] **AI-1, changed**: a check with nothing else pending starts a proposal of its own for the user to accept, instead of going into the Checks panel unreviewed. `Answer.checked`, `Draft.checks`, `Workspace.checks`, and `Turn.checks` are gone: checks are among the commands
- [x] Proposals are named by what else they do: a rectangle and its check is "Create Rectangle" (`Workspace.label`)
- [x] **Fixed on the way**: a mirror, pattern, or outline as the first change of a draft was run, reported as applied, and dropped; only a command tool's name used to start a draft
- [x] Tests: `tests/ai/test_tools.py` (stored, corrected, a user's check corrected, removed, undone), `test_draft.py` (a check or a pattern first starts a draft), `test_mcp_server.py`, and the app's MCP and assistant tests

## The AI side of the 2D V1 audit (branch `shared/2d-v1-audit`, 2026-09-29; local)

Part of the audit in [core.md](core.md#2d-v1-audit-and-hardening-branch-shared2d-v1-audit-2026-09-29-local-not-pushed). Only changes with evidence behind them from the test runs:

- [x] **AI-2, in part**: a check of the same measurement (metric, refs, ids) run again replaces the earlier one, and the result says what it replaced; a check that shouldn't be there at all still can't be removed
- [x] **AI-5**: `run_check`'s description says what each metric reads and measures (`tools.METRICS`); a test fails if the contract gains a metric without one
- [x] **AI-4**: the note before a result ends with a blank line
- [x] Rejections a model reads are truer (engine): "partly implied" where a constraint repeats only part of the others, and no conflict that names itself
- [x] Not changed, and why: the create tools' one-line descriptions (no run showed a problem); a profile tool for outlines (AI-10) and arcs in patterns (AI-11) are new features, left for after the audit

## Circular pattern, and a better time left (branch `shared/mirror-pattern-time-left`, 2026-09-28)

Andre, after test 003 (the stress plate on mirror and linear pattern: 4m 40s and 162 calls, against 9m 17s and 281): make a circular pattern tool; the Timing line should show only the time and the time left (the calls are in the fields); and can the time left be more accurate.

- [x] **`circular_pattern`** (`caliper/ai/patterns.py`): points, lines, and circles round a centre point, over a full circle or part of one, clockwise for a negative angle. Each orbit (a point and its copies) sits on a construction circle through the original, joined by equal construction chords: round a full circle the count sets the spacing, and a partial pattern has one angle dimension between two radii. A copy's point that lands where another already is gets a coincident constraint instead, so a star's outline closes and each orbit is placed once; points that almost meet are reported in a note. Refused: arcs (no constraint ties their angles), rectangles (always axis-aligned), originals that overlap their own copies, copies 180° or more apart in a partial pattern
- [x] Trials: a 6-hole bolt circle in 0.03 s; the 002 star as one constrained point (two edges, inner ends exactly 15° either side) and one call, 2.4 s, 0 DOF, one closed outline of 24 lines, about 14 calls instead of 62 with mirror
- [x] **The Timing line**: `▸ 1m 12s · ~3m 40s left`, just the time when the run stops; no call count
- [x] **Time left, priced by kind** (`caliper/app/agent/estimate.py`): each call is measured from the end of the call before it (Claude's time, the MCP round trip, redrawing, the command and solve), by kind: repeats (mirror and patterns), checks, and the rest. Each kind still to go is priced at that kind's cost in this run, blended with priors from test 003 (1.7 s, 0.8 s, 5 s) while few are measured. `report_progress` takes an optional split into repeats and checks; without it the calls are priced at the run's average. Time Claude has spent since the last call counts toward the next one only
- [x] **Smoothed**: it counts down each second; every 15 s, and when a repeat finishes, it moves halfway to the new figure; a new plan, or a figure more than half out, replaces it at once
- [x] **Honest about what it knows**: until 10 calls are measured it says `about 4 min left` (to the minute); `no estimate` without a plan; `finishing…` when the plan's calls are done; the final time alone when the run stops, as soon as Claude reports 0
- [x] Tests: `tests/app/test_estimate.py` (kinds, priors, blending, the figure, the countdown, refresh, due, stale and new plans, never below zero), `tests/app/test_timing.py` (what the run feeds it: kinds, the report call not counted, measured from the call before), `tests/app/test_mcp.py` (the line from rough to measured to stopped, every wording, bad splits refused), `tests/ai/test_patterns.py` (bolt circles full, partial, clockwise, and half a turn; the star closes and follows its layout; near misses; spokes share the centre; refusals). 1374 passed, 16 skipped
- [ ] Live run from Claude Desktop: does it call `report_progress` with a split, and how close is the estimate?
- [ ] Priors from more runs: the three figures come from one Claude Code run. Keeping the last run's figures (in memory, or in settings) would make the first estimate better; not done, since one run's figures aren't yet evidence
- [ ] Circular pattern of arcs (gear teeth): an arc's copy needs its angles tied, the same problem mirror solved with a construction point

## Mirror, linear pattern, and time left (branch `shared/mirror-pattern-time-left`, 2026-09-28)

Andre, after the live rerun of test 002: Claude could mirror more instead of drawing each piece, a linear pattern would help, the Timing panel should show an estimate at the start and count down without costing Claude effort, and the time doesn't stop by itself. He also couldn't find docs/cad-practices.md: it reached `main` (#46) after `shared/performance-v2` branched, so that branch was rebased onto `origin/main` (local, not pushed), and this one is stacked on it.

- [x] **`mirror_entities`** (`caliper/ai/patterns.py`): points, lines, circles, arcs, and rectangles about a line, one call. Each copy is tied to its original by symmetric constraints: a line's ends, a circle whole, a rectangle's opposite corners (horizontal or vertical axis only), an arc's ends start to end plus a construction point at its mirrored centre, held level with the copy's centre across the chord's bisector. Symmetric arcs would state the radius twice and be rejected as redundant, and a level-with constraint alone is singular for a semicircle. Geometry on the axis is skipped
- [x] **`linear_pattern`**: points, lines, circles, and rectangles in a row or grid, one call. Each copy is joined to the previous by a construction line equal and parallel to the first, which gets one driving spacing dimension (and horizontal or vertical when the direction is); each copy equals the original (radius, length and direction, sides). Arcs are refused: nothing ties a copy's angles. At most 100 copies a call (it runs on the UI thread)
- [x] Both are ordinary commands on the workspace's bus: all kept or all undone if any is rejected, one step for the model's `undo` (`Workspace._calls`), replayable byte for byte. No contract change, as AI-9 and AI-10 proposed for their tools
- [x] **`CONVENTIONS`** point to them, and carry the core of docs/cad-practices.md, which Claude Desktop can't read: dimension from the nearest datum with the label beside the feature, each relationship once, reference dimensions for fixed sizes, and DOF per feature. Unverified until a live run
- [x] **Time left** (`report_progress`, MCP only, `caliper/ai/mcp_server.py`): Claude says how many calls it plans, once; Caliper converts that to time at the run's pace (2 s a call until 10 calls) and counts down on the Timing line (`▸ 1m 12s · ~3m 40s left · 35 calls`). Not recorded. `report_progress` with 0 is "done": the time stops at once
- [x] **Auto-stop:** without that, the time stops after 60 s with no call (`QUIET`), or 3 minutes while Claude is short of its estimate
- [x] Trials in a workspace: the 002 hole grid is 5 calls (0.22 s for the pattern), the slots' mirror 1 call (fully constrained), the whole star 62 calls, layout included, from a rough start (the mirror in 0.19 s), all at 0 DOF; 281 calls for the plate before
- [x] Tests: `tests/ai/test_patterns.py` (the slot, the half star, every kind about slanted lines, a D-shaped arc, skips and refusals, the grid, respacing and resizing from one dimension, one undo step, replay, all or nothing), `tests/app/test_timing.py` (estimate, pace, countdown, done, quiet stops), `tests/app/test_mcp.py` (the panel's line, done before Accept, bad estimates, a pattern as one proposal and one undo step), `tests/ai/test_mcp_server.py`. 1327 passed, 16 skipped
- [ ] Live rerun of test 002 from Claude Desktop on this branch: does Claude call `report_progress`, use the tools, and follow the practices?
- [ ] Circular pattern: the same approach (copies of a seed, tied by equal constraints and construction geometry about a centre) would make the star about 15 calls and the ball bearing's balls one. It needs no contract change after all

## Performance V2, the AI side (branch `shared/performance-v2`, 2026-09-27)

Part of the whole-app pass in [core.md](core.md#performance-v2-branch-sharedperformance-v2-2026-09-24-to-2026-09-27), which has the numbers. For the AI layer:

- [x] **Accept doesn't solve again.** `Workspace.executed` (and `Draft.executed`, `Turn.executed`, `Plan.executed`) is the record of what ran: the base, each step's `Applied`, and the result. Accepting commits it through `handlers.already`: 25 s → 0.02 s for the 255-change plate, still one undo step
- [x] **AI-1 fixed.** A check run with nothing pending (`Answer.checked`, or a turn with only checks) goes to the user's Checks panel instead of being lost
- [x] **Smaller results.** A change's result no longer echoes the command, and shows a solve's modified entities in full for the first 8, naming the rest in `also_modified`: the plate's results went from 138 to 92.5 KiB. [docs/mcp.md](../mcp.md#what-a-changes-result-tells-claude) says what a result holds
- [x] **Stop.** `Assistant.ask(stop=...)` ends a turn before its next model request and forgets it (`STOPPED`); the prompt bar shows Stop while the assistant works
- [x] Tests: `tests/ai/test_tools.py` (the record commits after an undo), `tests/ai/test_agent.py` (a stopped turn), `tests/app/test_mcp.py`, `tests/app/test_assistant.py`, `tests/app/test_performance.py`
- [ ] A live timed rerun of test 002 from Claude Desktop on this branch, for the end-to-end number

## Timing panel, live time, and the timing file (branch `shared/timing-dock`, 2026-09-27)

Andre, after test 002: the Timing section was hard to find in the Assistant tab, Total run should keep going while Claude works, 003-timing.md shouldn't need copying by hand, and the constraint badges crowd a big sketch. Stacked on `shared/open-files` (#43). He chose: the file is written on save, and the time stops on Accept or Reject.

- [x] **Its own panel, above Properties** (`caliper/app/panels/timing.py`, `TimingPanel`, dock `timing-dock`); the Assistant tab is just the log again. Still one line until opened, and hidden unless Claude Desktop can connect
- [x] **Live time:** `Timing.elapsed` while a run is live, shown every second (`▸ 4m 07s · 57 calls · running`). It stops on Accept or Reject (or a dropped proposal), Start run, another document, or 3 minutes with no call; a call after an accept part-way starts it again. Total run then settles on its recorded value (start to Claude's last call), which is also what's copied and written
- [x] **003-timing.md on save:** saving a drawing into `test-runs-*/NNN-name/` writes that folder's 003-timing.md from the latest run (`timing.timing_file`), never over an existing one; the status bar says so
- [x] **Constraint badges:** View → Show Constraints (which already hid the badges and kept the dimensions) now has ⇧C and a toolbar button, with its own icon
- [x] Tests: live time, stopping, restarting, idle, Start run and another document (`test_timing.py`); which drawings get a timing file; the panel ticking and settling on Accept; the file written on save, not overwritten, not written elsewhere or with no run (`test_mcp.py`); badges hidden while a dimension stays clickable (`test_glyphs.py`)

## MCP timing and test-run records (branch `shared/mcp-timing`, 2026-09-26)

User request (2026-09-26): time every Claude Desktop task automatically, shown compactly in the Assistant tab with fixed fields and names, and a permanent folder convention for recording AI/MCP test runs. Stacked on `shared/fix-constrain-menu-hang` (#39) so the checkout keeps that fix; no change to CAD, proposal, or MCP behaviour, and no contract change.

**The design question, and the answer:** Caliper sees a task only through its MCP calls, not the moment the prompt is sent or Claude's closing message. Andre chose automatic runs plus an optional Start run: a run starts at Claude's first call (or on New/Open, or after 3 minutes with no calls), and pressing Start run (⌘⇧R) as the prompt goes out makes First response measurable. Anything not measured shows N/A.

- [x] `caliper/app/agent/timing.py`: `RunTimer` (Qt-free, injected clock): runs, each call from arrival to answer, proposal-building spans, accepts; `markdown()` is the test folder's `003-timing.md`, exactly
- [x] `McpHost.handle` times every call and emits `timed`; `AgentController.applied` now carries the Accept's seconds; `start_run()`
- [x] The Assistant tab: a one-line Timing section above the log (hidden unless Claude Desktop can connect), expandable to the six fields and the date, with Start run and Copy for 003-timing.md; Agent → Start Timing Run (⌘⇧R)
- [x] Tests: `tests/app/test_timing.py` (the arithmetic on a fake clock: calls, longest, total, first response, proposal creation, accept, no proposal, rejected, several proposals, new runs by Start run, idle, and another document, the formats) and nine window tests in `tests/app/test_mcp.py` (real calls over the socket with a faked clock, accept and reject, stale accept, New, the shortcut, the collapsed section, the copy)
- [x] Docs: [docs/mcp.md, Timing](../mcp.md#timing) (the fields and definitions, for every future test) and [test-runs-andre/README.md](../../test-runs-andre/README.md) (the folder convention: five numbered files per test, `001-prompt.md` to `005-my-results.md`, plus `test-name.caliper`, and no README in a test folder); `001-ball-bearing` brought into it
- [ ] A live timed run from Claude Desktop (needs Caliper restarted on this branch)

**For Lucas:** `caliper/app/agent/mcp_host.py` (`handle` split into timing and `_respond`), `caliper/app/agent/ui.py` (`applied` has a second argument), `caliper/app/panels/assistant.py` (`TimingSection`), `caliper/app/main_window.py` (the Assistant tab is now a container: `assistant_panel`), `caliper/app/theme.py`.

## Layout first (branch `ai/construction-first`, 2026-09-26)

A 16-tooth gear drawn over MCP (`gearv1.caliper`, not in git) came back as 65 arcs, 35 lines, and 261 constraints with **no construction geometry**: tip and root arcs at r 27 and 20.25, but no pitch, root, or tip circle and no centre lines. The model worked every coordinate out itself and chained the outline edge to edge. Cause: nothing told it the layout exists. `CONVENTIONS` covered units only, and every create tool showed `construction` as a bare boolean under the description "Caliper's CreateCircle command."

- [x] `CONVENTIONS` (both prompts): build the layout first as construction geometry driven by dimensions, then draw the outline on it with constraints, and finish and check one feature before repeating it
- [x] Every create tool's `construction` field says what it is for (`_FIELD_DESCRIPTIONS` in `caliper/ai/tools.py`; the contract dataclasses are untouched)
- [x] Tests in `tests/ai/test_tools.py`; removing the field description fails them. 1140 passed, 16 skipped
- [ ] Live rerun in Claude Desktop: the same gear request should start with construction circles. Unverified until then: a prompt change is only proven by the model's behaviour
- [ ] Open question for Andre: a circular pattern command. Without one, "repeat from the layout" still means placing each tooth by hand. It would be a contracts change (joint review). 2026-09-28: `linear_pattern` showed a tool made of existing commands needs none (see Mirror, linear pattern, and time left)

## MCP stress-test fixes (branch `shared/mcp-stress-fixes`, 2026-09-25)

The first large MCP session (a 187-entity, fully constrained plate with 57 checks) exposed three problems. Stacked on `shared/ai-mcp-server`; no contract change.

- [x] **Tangency at a fillet joint was rejected as redundant** (engine; see core.md). Written at the joint instead, so it's accepted and counted; the alignment workaround is no longer needed
- [x] **Large proposals were slow.** Measured, not assumed: every call that changed the draft showed it again, and `prepare()` replayed every pending command through a fresh bus, 1 + 2 + ... + n commands in all. At 224 changes one replay took 5.2 s and the session 277 s; the draft's own calls took 5.2 s in total, and checks 0.1 ms: checks timed out only because each new one triggered a replay. `prepare(..., result=)` now takes the workspace's document (byte-identical to the replay), for MCP drafts and the in-app assistant's turns. 128 changes: session 20.3 s to 0.69 s, slowest call 0.70 s to 0.03 s (`tests/ai/bench_mcp_session.py`). Accept still runs every command through the session's bus in one transaction (about 9.7 s at 254 changes: the solver, Performance V2 territory)
- [x] **The proposal card grew without bound.** More than 6 changes: a summary line and a Show/Hide toggle; shown, the list scrolls within 180 px. Small proposals are unchanged; errors, broken checks, and check rows stay visible
- [x] Tests: engine regression tests above; `tests/app/test_mcp.py` (a proposal from a workspace equals the replayed one; a long MCP session replays nothing and accepts as one step, undo and redo; small and large cards, bounded, expandable, failing checks shown, accept from collapsed). Deliberate breaks of each fix fail their tests
- [x] End to end with processes (new app offscreen, `uv run caliper-mcp`, SDK client): tangency at both fillet joints accepted (DOF 7 to 5, nothing redundant), 125 more changes in 1.2 s with the slowest call 35 ms, a new check 7 ms, radius -5 rejected

**For Lucas:** `caliper/app/agent/ui.py` (`ProposalCard` summary, toggle, and bounded list; `propose(..., result=)`), `caliper/app/agent/proposal.py` (`prepare(..., result=)`), `caliper/app/agent/mcp_host.py`, and the engine change in `caliper/engine/constraints/`.

## MCP: Claude Desktop as the primary way in (branch `shared/ai-mcp-server`, 2026-09-25)

User request (2026-09-25): make MCP the primary way Claude works in Caliper, keep the direct Anthropic API path as the secondary one, reuse the existing tools rather than duplicating them, and keep proposals, validation, undo, replay, and credentials as they are.

**The design question, and the answer:** Claude Desktop starts an MCP server as its own process; the document lives in the Caliper window. A server with its own document would drift from the window, so `caliper-mcp` holds no document: it forwards each call over a user-only Unix socket to the window, where it runs like the in-app assistant's calls. MCP has no "turn" that ends, so the window keeps one draft that grows with each change and is shown as a proposal after each; the user still accepts or rejects it, and Claude can't. The draft ends on accept, reject, a user edit, or opening another document, and Claude's next call begins with a note saying which. No contract change.

**Built:**
- [x] `caliper/ai/draft.py`: `Draft`: the calls of an outside client on one `Workspace`; looking opens no draft; ends with a reason (`Ended`) reported once on the next call; a change made underneath drops it; undoing all of it leaves nothing pending
- [x] `caliper/ai/bridge.py`: the wire between `caliper-mcp` and the window: one JSON line each way over `~/.caliper/mcp.sock` (`CALIPER_MCP_SOCKET`), in a 0700 directory; the path comes from HOME because Claude Desktop passes no TMPDIR; malformed or oversized messages refused
- [x] `caliper/ai/mcp_server.py`: `caliper-mcp` (a `[project.scripts]` entry), on the MCP SDK's low-level server (v2.2, MIT, the `mcp` extra and the dev group). It lists `TOOLS` unchanged (read-only hints on the queries), sends instructions sharing `CONVENTIONS` with the in-app prompt, refuses unknown tool names itself, and puts any draft note first in the result. The SDK is imported only when the server runs; no API key read or needed
- [x] `caliper/ai/tools.py`: `CONVENTIONS` shared by both prompts (the in-app system prompt is byte-identical), and turn-neutral wording for `inspect_document` and `undo`
- [x] Shell: `app/agent/mcp_host.py` (`McpHost`) listens with `QLocalServer` (QtNetwork, in pyside6-essentials, LGPL) and answers on the UI thread, one call at a time. Refuses changes while the in-app assistant is busy. A second window doesn't take the socket from the first (checked before listening: Qt renames its socket into place); a crashed window's leftover is replaced. `MainWindow.serve_mcp(path)`, called by `python -m caliper.app` unless `CALIPER_MCP=off`; tests and plain `MainWindow()` open no socket. `AgentController` gains `propose()` (the assistant path uses it too) and an `applied` signal. The Assistant tab logs each MCP call with the client's name
- [x] Tests: `tests/ai/test_draft.py`, `test_bridge.py`, `test_mcp_server.py` (the SDK's own client, in-process and launching `caliper-mcp` as a process with Claude Desktop's environment, against a Qt-free Caliper stand-in over a real socket), `tests/app/test_mcp.py` (the real window: proposal, accept, one undo step, redo, replay, reject, stale, another document, busy, private socket, one owner, garbled input, SDK client end to end), and `test_architecture.py` (importing `caliper.ai` loads neither SDK). Five deliberate breaks each failed a test
- [x] End to end with processes: `python -m caliper.app` (offscreen) and `uv run --extra mcp caliper-mcp` driven by the SDK's stdio client: 21 tools, read, create, check, and a rejected command; 1.2 ms per call over the bridge, so no engine benchmark was needed

**Not done here, deliberately:**
- No run with Claude Desktop itself: it would mean editing Claude Desktop's own config on this machine, and the Caliper window can't be shown from the agent's shell. `docs/mcp.md` has the setup and a 13-step manual test
- No MCP resources or prompts, only tools; no redo tool (the user redoes in Caliper); no headless (window-less) MCP mode
- Windows: the bridge needs Unix sockets (macOS first, ADR 0004)
- If the app is killed its socket file stays until the next start replaces it

## Foundation (branch `shared/ai-interface-foundation`, 2026-09-24)

User request (2026-09-24): the foundation for a generative assistant that interfaces with Caliper through well-defined interfaces, model-agnostic, with a thin UI and a real end-to-end demo; no engine optimisation, no contract change, no custom model.

**What the existing architecture already gave, and was reused rather than rebuilt:**
- An operation request is a `Command`; its JSON form is the one command scripts and replay use (`engine/io/codec.py`), and the bus validates it and rejects bad input with structured `Error`s.
- The document is inspected through `Queries`; what changed comes back as `Applied.delta`; `check` verifies intent (now with position metrics); transactions and undo are the bus's.
- The shell's P6 review flow (`caliper/app/agent/`): a `Plan` prepared on a scratch copy, a proposal card with each check before and after, ghost geometry, and Accept as one undo step credited to the agent. Its docstring already said a real model should plug in here.

**Built:**
- [x] `caliper/ai/model.py`: the provider-neutral `Model` protocol and messages (user turns, replies with tool calls, tool results). A reply keeps the provider's native content to send back unchanged
- [x] `caliper/ai/tools.py`: one tool per `Command` kind, generated from the contract (14 today), plus `inspect_document`, `inspect_entities`, `measure_distance`, `run_check`, `solve_status`, `applicable_constraints`, and `undo`. Calls run in a `Workspace`: a scratch bus on a copy of the document. Its `commands` are the resolved commands, replayable on the original to the same bytes. No code execution, shell, or file tools
- [x] `caliper/ai/context.py`: a bounded, deterministic summary: counts, bounds, solve status, selection, up to `limit` entities (selection and focus first, then nearby geometry, then the rest in reading order), and what changed since the model's last turn
- [x] `caliper/ai/agent.py`: `Assistant.ask`: request plus context, model, tool calls, results back, until the model answers; a turn ends with its steps, reply, commands, checks, and any error. Stops after 16 replies. The conversation carries over between turns and resets when another document opens. `from_environment()`: on only with `CALIPER_ASSISTANT=claude`
- [x] `caliper/ai/claude.py`: Claude through the official `anthropic` SDK, loaded lazily. `claude-opus-5` by default (`CALIPER_AI_MODEL` overrides), adaptive thinking, server-side refusal fallbacks (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`); replies go back exactly as they came; every tool result of a reply goes back in one message. Credentials are the SDK's own (`ANTHROPIC_API_KEY` or `ant auth login`); Caliper never reads a key
- [x] Shell: `AgentController` sends requests to the assistant when one is configured, on a worker thread, and turns its commands into the existing `Plan` and proposal card; without one, the scripted stand-in answers exactly as before. New Assistant tab in the browser (`panels/assistant.py`): each request, each tool step, the reply, errors. The prompt bar names the model and waits while it works. An edit made meanwhile makes the proposal stale, as before
- [x] Fixed on the way: the Checks panel and proposal card crashed on a position check (`panels/checks.py` `describe()` had no case for `POSITION_X`/`POSITION_Y`, flagged in PR #29); the assistant made it reachable
- [x] Tests: `tests/ai/` (tools, context, loop, Claude adapter against a fake SDK client), `tests/app/test_assistant.py` (the real window with a scripted model: ask, transcript, proposal, accept, undo and redo, follow-ups, errors, busy, stale, new document), and architecture tests (`caliper/ai` imports no app, Qt, or OS modules; importing it loads no UI and no model SDK). Seven deliberate breaks each failed a test
- [x] Manual end-to-end in the real window (offscreen) with a scripted model: "Make a rectangle 100 by 50 and put it 20mm to the right of the origin" → proposal with two passing checks, document untouched → Accept → e1 at (20, 0), 100 × 50, credited to Agent → Undo removes it

**Not done here, deliberately:**
- No live model run: the `anthropic` SDK isn't installed and no credentials are set on this machine, so the Claude adapter is tested only against a fake client. The request shape follows the Claude API reference, including `fallbacks`; confirm it against the installed SDK version on the first live run
- Mid-turn transactions for the model: the whole turn becomes one transaction when accepted, so the model doesn't open its own
- Streaming replies to the UI, cost/token display, cancelling a running turn

## Decisions (made in the foundation PR, for Lucas to confirm)

- [x] **Owner and branch rule:** `caliper/ai/`, `tests/ai/`, `docs/workplan/ai.md` are Andre's (CODEOWNERS, CLAUDE.md, CONTRIBUTING.md). `check_boundaries.py` has an AI area for `ai/<topic>` branches, and `tests/ai/` is no longer inside Stream A's `tests/`
- [x] **Dependency:** `anthropic` is the optional `ai` extra (MIT) in `pyproject.toml` and `uv.lock`; `caliper/ai` is in mypy's `files`
- [x] **docs/architecture.md:** the shell hosts the assistant and may import `caliper/ai`
- [x] **WORKPLAN.md** links this file
- [x] **.env.example:** `CALIPER_ASSISTANT`, `CALIPER_AI_MODEL`, and `ANTHROPIC_API_KEY` as placeholders; the key is read by the SDK only
- [x] **CLAUDE.md and WORKPLAN.md** follow the ownership rule, the `ai` extra, strict mypy on `caliper/ai`, and the branch rename (stacked PR on #32)

## For Lucas (MCP branch)

- Shell: `app/agent/mcp_host.py` (new), `AgentController.propose()` and `applied` in `app/agent/ui.py` (the assistant path now calls `propose()`; behaviour unchanged), `MainWindow.serve_mcp()` and closing the socket on close, `AssistantLog.remote_step`, and `app/__main__.py` serving MCP by default (`CALIPER_MCP=off` turns it off)
- Architecture: the app process listens on a local socket by default. It's user-only and can only create proposals, never apply them; decide whether it should be opt-in instead
- Dependencies: `mcp>=2.2` (MIT) as the `mcp` extra and in the dev group (so the core CI job runs its tests without a workflow change); it brings pydantic, starlette, uvicorn, httpx2, cryptography and others, all passing `test_licenses.py`

## For Lucas (shell files changed by the foundation)

- `caliper/app/agent/ui.py`: the assistant path in `AgentController` and `PromptBar` (`set_model`, `set_busy`); the scripted path is unchanged and its 25 tests pass untouched
- `caliper/app/panels/assistant.py` (new) and the Assistant tab in `main_window.py`
- `caliper/app/panels/checks.py`: `describe()` covers the position metrics, with a fallback for metrics added later
- Seen, not changed: the canvas's empty-sketch hint draws over a proposal's ghost geometry when the document is still empty

## Next

- [ ] A first run from Claude Desktop (docs/mcp.md, Manual test), and tuning the MCP instructions from what it does
- [ ] A first live run with Claude on this branch's tools, and prompt tuning from what it does
- [ ] A bench solver that reads each case's prompt through the assistant (bench/ already has prompts, reference scripts, and expectations)
- [ ] Streaming progress and cancelling a turn
