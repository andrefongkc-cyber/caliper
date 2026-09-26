Status: MCP stress-test fixes (tangency, proposal speed, bounded card) done on `shared/mcp-stress-fixes` (stacked on `shared/ai-mcp-server`, not pushed), next: Andre's review, then restart Caliper for a live Claude Desktop rerun

# AI workplan

The assistant: a model that understands a request and does it through Caliper's own commands and queries. Two ways in share one set of tools: **MCP (primary)**, where Claude Desktop is the model and calls Caliper's tools through `caliper-mcp`, and the **direct Anthropic API (secondary)**, the in-app assistant in the prompt bar. `caliper/ai/` is headless and imports `contracts` and `engine` only; the shell hosts it. Owner: Andre (Lucas reviews, as for every area). AI-only work goes on `ai/<topic>` branches, which may touch `caliper/ai/`, `tests/ai/`, this file, and `docs/adr/`; anything touching the shell or shared files goes on `stream/shell` or `shared/`.

Markers: `[ ]` not started · `[~]` in progress · `[x]` done

**Picking this up in a fresh session.** MCP work is on `shared/ai-mcp-server` from `main` 732122b, not pushed (cross-area: `caliper/ai`, the shell, `pyproject.toml`, docs); 1124 passed, 16 skipped. Offline: `uv run pytest tests/ai tests/app/test_mcp.py tests/app/test_assistant.py`. With Claude Desktop: [docs/mcp.md](../mcp.md). Direct API: `uv sync --extra ai`, `ANTHROPIC_API_KEY` in your shell, `CALIPER_ASSISTANT=claude uv run python -m caliper.app`.

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
