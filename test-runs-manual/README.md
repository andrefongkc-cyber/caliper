# Test runs

Every Caliper AI/MCP test that's run by hand (a task sent from Claude Desktop, or Claude Code,
to Caliper over MCP) gets one folder here. Each folder records exactly what was asked, what
Claude said, what it drew, how long it took, and what we both thought of it. Setup and the
quick check that MCP works: [docs/mcp.md](../docs/mcp.md).

## Folder structure

```
test-runs-manual/
└── 001-test-name/
    ├── README.md
    ├── test.caliper
    ├── prompt.md
    ├── claude-output.md
    ├── timing.md
    ├── claude-results.md
    └── my-results.md
```

## Folder names

`NNN-short-name`:

- **`NNN`** is a three-digit number, one more than the highest folder here: `001-`, `002-`,
  `003-`, ... Never reuse a number, even if a test is deleted or abandoned.
- **`short-name`** is lowercase words joined by hyphens, saying what's drawn or tested.

Examples: `001-inscribed-rectangle`, `002-complex-plate`, `003-ball-bearing`,
`004-symmetric-bracket`, `005-constraint-stress-test`.

## Files

Each file has one job. Don't repeat one file's content in another.

| File | Holds | Written by |
|---|---|---|
| `README.md` | The test's index: what it is, not what happened | Claude or you |
| `test.caliper` | The drawing | Caliper (File → Save As) |
| `prompt.md` | Exactly what you asked Claude | You (copied) |
| `claude-output.md` | Exactly what Claude said | You (copied) |
| `timing.md` | The measured performance | Caliper (Copy for timing.md) |
| `claude-results.md` | Claude's short assessment | Claude |
| `my-results.md` | Your own evaluation | **You only** |

### README.md

A short index:

```markdown
# 001 · Ball bearing

- **Test:** 001
- **Name:** ball-bearing
- **Date:** 2026-09-26
- **Tests:** what the test is testing, in one line
- **Description:** one or two sentences about the task

## Files

- `test.caliper`: the drawing
- `prompt.md`: the prompt, exactly as sent
- `claude-output.md`: Claude's output, as-is
- `timing.md`: Caliper's timing
- `claude-results.md`: Claude's summary
- `my-results.md`: my evaluation
```

List only the files the folder has. Don't copy in the prompt, Claude's output, the timing,
or any results.

### test.caliper

The drawing the test produced, saved from Caliper with File → Save As. It's Caliper's
native file and nothing else: no notes, timing, or evaluation.

### prompt.md

The exact prompt sent to Claude, character for character. Don't rewrite, summarise, tidy, or
correct it, typos included.

### claude-output.md

Claude's whole output from the task, as it was: progress updates, explanations,
assumptions, errors, workarounds, and the final response. It isn't a summary.

### timing.md

Caliper measures it. When Claude has finished, and you've accepted or rejected the proposal,
open the Timing section at the top of Caliper's Assistant tab and press **Copy for
timing.md**. Paste it as the whole file, in exactly this structure:

```
# Timing

Date: YYYY-MM-DD

Total run: Xm XXs

MCP/tool calls: XX

First response: X.Xs
Longest tool call: X.Xs

Proposal creation: Xm XXs
Accept: X.Xs
```

Don't add a Notes section. What each field means, and when a value is `N/A`:
[docs/mcp.md, Timing](../docs/mcp.md#timing).

### claude-results.md

Claude's short post-test summary: a 5 to 15 second read. Exactly this structure:

```markdown
# Claude Results

## Created
What was created, briefly.

## Result
The important final result: fully constrained or the DOF left, checks passed or failed,
and whether the requested geometry is complete.

## Assumptions
Only important assumptions Claude made because the prompt was ambiguous.

## Problems
Only meaningful problems, errors, workarounds, or unexpected behaviour.
```

Rules:

- Keep it concise and factual. No narrative, and no repeating Claude's progress updates.
- Under Assumptions or Problems, write `None.` when there were none.
- No timing of any kind: no durations, call counts, response times, proposal times, or
  Accept times. They're in `timing.md`.
- Longer only when a significant problem genuinely needs explaining.

Caliper's MCP tools can't write files, and they aren't given that ability for this. In
Claude Desktop, ask Claude for its results in this format and paste them in. In Claude Code,
Claude can write the file itself.

### my-results.md

Yours only, for example:

- whether the drawing looked correct
- whether Claude was fast enough
- how the UI behaved
- visual problems
- unexpected behaviour
- what to test next
- overall observations

Claude never writes or fills in `my-results.md`, not even a template.

## Running a test

1. Make the next folder, `NNN-short-name`.
2. In Caliper, File → New, then press **Start run** (⌘⇧R) just before you send the prompt.
3. Send the prompt, and copy it into `prompt.md`.
4. When Claude is done, review the proposal and accept or reject it.
5. Copy Claude's whole output into `claude-output.md`.
6. Assistant tab → Timing → **Copy for timing.md**, pasted into `timing.md`.
7. File → Save As, `test.caliper` in the folder.
8. Get `claude-results.md` from Claude, and the folder's `README.md`.
9. Write `my-results.md`.
