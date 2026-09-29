# Test runs

Every Caliper AI/MCP test that's run by hand (a task sent from Claude Desktop, or Claude Code,
to Caliper over MCP) gets one folder here. Each folder records exactly what was asked, what
Claude said, how long it took, what Claude and you made of it, and what it drew. Setup and
the quick check that MCP works: [docs/mcp.md](../docs/mcp.md).

## Structure

```
test-runs-andre/
└── 001-test-name/
    ├── 001-prompt.md
    ├── 002-claude-output.md
    ├── 003-timing.md
    ├── 004-claude-results.md
    ├── 005-my-results.md
    └── test-name.caliper
```

Every test folder has exactly these six files. The five `.md` files are numbered in the order
to read them, with these exact names. The drawing is named after the test, without a number:
`001-ball-bearing/ball-bearing.caliper`. A test folder has no `README.md`: this file is the
only one.

## Folder names

`NNN-short-name`:

- **`NNN`** is a three-digit number, one more than the highest folder here: `001-`, `002-`,
  `003-`, ... Never reuse a number, even if a test is deleted or abandoned.
- **`short-name`** is lowercase words joined by hyphens, saying what's drawn or tested.

Examples: `001-ball-bearing`, `002-complex-plate`, `003-inscribed-rectangle`,
`004-symmetric-bracket`, `005-constraint-stress-test`.

## Files

Each file has one job; don't repeat one file's content in another.

| File | What it's for | Written by |
|---|---|---|
| `001-prompt.md` | The exact prompt sent to Claude | You (copied) |
| `002-claude-output.md` | Claude's full output, as-is | You (copied) |
| `003-timing.md` | How long it took, as Caliper measured it | Caliper |
| `004-claude-results.md` | Claude's short summary of the result | Claude |
| `005-my-results.md` | Your own observations and notes | **You only** |
| `test-name.caliper` | The drawing the test produced | Caliper (File → Save As) |

**`001-prompt.md`**: the prompt, character for character. Don't rewrite, summarise, or
correct it, typos included.

**`002-claude-output.md`**: everything Claude said during the task: progress updates,
explanations, assumptions, errors, workarounds, and the final response. Not a summary.

**`003-timing.md`**: Caliper writes it when you save the drawing into the test folder, from
its Timing panel (above Properties); save after you accept or reject. It never overwrites an
existing one. By hand instead: open the Timing panel, press **Copy for 003-timing.md**, and
paste it as the whole file. Exactly this structure, with no Notes section:

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

What each field means, and when it's `N/A`: [docs/mcp.md, Timing](../docs/mcp.md#timing).

**`004-claude-results.md`**: a 5 to 15 second read, in this structure:

```markdown
# Claude Results

## Created
What was created, briefly.

## Result
Whether it was accepted; fully constrained or the DOF left; checks passed or failed; whether
the requested geometry is complete.

## Assumptions
Only important assumptions made because the prompt was ambiguous, or `None.`

## Problems
Only significant problems, errors, workarounds, or unexpected behaviour, or `None.`
```

Keep it factual and short. Don't repeat Claude's output or its progress updates, and put no
timing in it (no durations, call counts, or response times): that's `003-timing.md`.
Caliper's MCP tools can't write files, and aren't given that ability for this: in Claude
Desktop, ask Claude for its results in this format and paste them in; in Claude Code, Claude
can write the file.

**`005-my-results.md`**: yours: whether the drawing looked right, whether Claude was fast
enough, how the UI behaved, visual problems, anything unexpected, what to test next, and your
overall view. It starts as an empty template. Claude never fills it in.

**`test-name.caliper`**: Caliper's native file, saved at the end of the test and named after
the test's folder, without its number (`ball-bearing.caliper` in `001-ball-bearing`).
Nothing else goes in it: no notes, timing, or evaluation.

## Running a test

1. Make the next folder, `NNN-short-name`.
2. In Caliper, File → New, then press **Start run** (⌘⇧R) just before you send the prompt.
3. Send the prompt, and copy it into `001-prompt.md`.
4. When Claude is done, review the proposal and accept or reject it.
5. Copy Claude's whole output into `002-claude-output.md`.
6. File → Save As, `test-name.caliper` in the folder. Caliper writes `003-timing.md` next
   to it.
7. Get `004-claude-results.md` from Claude.
8. Write `005-my-results.md`.
