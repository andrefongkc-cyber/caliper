Status: AI interface foundation built and verified on `ai/interface-foundation` (not pushed), next: Andre's review, then an owner and branch rule for `caliper/ai/`, and a first live run with Claude

# AI workplan

The assistant: a model that understands a request and does it through Caliper's own commands and queries. `caliper/ai/` is headless and imports `contracts` and `engine` only; the shell hosts it. No owner or branch prefix is set for `caliper/ai/` yet (see Decisions needed).

Markers: `[ ]` not started · `[~]` in progress · `[x]` done

**Picking this up in a fresh session.** Branch `ai/interface-foundation`, 9 commits plus this update on `main` 5102c2b, not pushed, clean tree; 1054 passed, 16 skipped. Blocked on Andre: review, the owner and branch rule for `caliper/ai`, and SDK + credentials for a live run. Try it offline with `uv run pytest tests/ai tests/app/test_assistant.py`; live with `CALIPER_ASSISTANT=claude uv run python -m caliper.app` once `anthropic` is installed.

## Foundation (branch `ai/interface-foundation`, 2026-09-24)

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

## Decisions needed (maintainers)

- [ ] **Branch prefix and owner for `caliper/ai/`.** `.github/scripts/check_boundaries.py` rejects any branch not named `stream/core`, `stream/shell`, `contracts/`, `shared/`, or `phase-0/`, so a PR from `ai/interface-foundation` fails the boundaries check. Either add an `ai/` rule (and a CLAUDE.md ownership row), or rename the branch (`shared/` is unrestricted)
- [ ] **Dependency:** add `anthropic` as an optional extra (e.g. `ai`) in `pyproject.toml` and `uv.lock`, and `caliper/ai` to mypy's `files` (it passes `mypy --strict` today)
- [ ] **docs/architecture.md:** the package table should say `caliper/app` may import `caliper/ai`, which hosts it
- [ ] **WORKPLAN.md:** link this file
- [ ] **.env.example:** document `CALIPER_ASSISTANT` and `CALIPER_AI_MODEL`

## For Lucas (shell files changed here)

- `caliper/app/agent/ui.py`: the assistant path in `AgentController` and `PromptBar` (`set_model`, `set_busy`); the scripted path is unchanged and its 25 tests pass untouched
- `caliper/app/panels/assistant.py` (new) and the Assistant tab in `main_window.py`
- `caliper/app/panels/checks.py`: `describe()` covers the position metrics, with a fallback for metrics added later
- Seen, not changed: the canvas's empty-sketch hint draws over a proposal's ghost geometry when the document is still empty

## Next

- [ ] A first live run with Claude on this branch's tools, and prompt tuning from what it does
- [ ] A bench solver that reads each case's prompt through the assistant (bench/ already has prompts, reference scripts, and expectations)
- [ ] Streaming progress and cancelling a turn
