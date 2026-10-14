# Claude Desktop and Caliper (MCP)

MCP (the Model Context Protocol) is the main way Claude works in Caliper. Claude Desktop is
the model; Caliper supplies the tools. Claude never touches the sketch directly. Every
change is a Caliper command, validated by Caliper, and lands as a proposal on the canvas that
you accept or reject. No API key is involved: Claude Desktop uses your Claude account.

```
Claude Desktop ──MCP (stdio)──▶ caliper-mcp ──local socket──▶ Caliper window
                                 (lists the tools,            (runs each call on a draft of
                                  forwards each call)          your sketch; you accept it)
```

- **`caliper-mcp`** (`caliper/ai/mcp_server.py`) is a small program Claude Desktop starts. It
  lists Caliper's tools and forwards each call. It holds no document and reads no key.
- **The Caliper window** listens on `~/.caliper/mcp.sock`, a Unix socket in a directory only
  your user account can open (`caliper/app/agent/mcp_host.py`). Each call runs in a draft,
  the same scratch copy the in-app assistant uses (`caliper/ai/draft.py`, `caliper/ai/tools.py`).
- **The tools** are the same 21 the in-app assistant uses: one per Caliper command (create,
  edit, delete, constrain, dimension, ...) plus `inspect_document`, `inspect_entities`,
  `measure_distance`, `run_check`, `solve_status`, `applicable_constraints`, and `undo`
  (which only takes back changes that aren't applied yet). There are no shell, file, code, or
  network tools.

The direct Anthropic API path still works as a second option: the Assistant in Caliper's own
prompt bar (`CALIPER_ASSISTANT=claude`, `uv sync --extra ai`, `ANTHROPIC_API_KEY` in your
shell). It uses the same tools. MCP doesn't need it, and it doesn't need MCP.

## Set up

1. **Install Caliper with the shell and MCP:**

   ```bash
   uv sync --extra app --extra mcp
   ```

2. **Find two absolute paths.** Claude Desktop doesn't start programs from your shell, so it
   needs full paths:

   ```bash
   command -v uv
   ```

   and the folder you cloned Caliper into (`pwd` inside it).

3. **Add Caliper to Claude Desktop.** In Claude Desktop, open Settings → Developer → Edit
   Config. That opens `claude_desktop_config.json` (on macOS in
   `~/Library/Application Support/Claude/`). Add a `caliper` entry under `mcpServers`,
   replacing both placeholders with the paths from step 2:

   ```json
   {
     "mcpServers": {
       "caliper": {
         "command": "/ABSOLUTE/PATH/TO/uv",
         "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/caliper", "--no-dev", "--extra", "mcp", "caliper-mcp"],
         "env": {"UV_PROJECT_ENVIRONMENT": "/ABSOLUTE/PATH/TO/caliper/.venv-mcp"}
       }
     }
   }
   ```

   The `env` line gives the server its own environment, `.venv-mcp`, built from the same
   checkout: it runs the same code as the app, so its tools always match the window's, but
   syncing `.venv` for development (or the server starting) never changes the other. Before
   this, one shared `.venv` meant a `uv sync` without the `mcp` extra could take the server's
   tools away, and a server start could remove development tools (known issue AI-3). There's
   no key: the server needs none.

4. **Restart Claude Desktop.** Caliper's tools show up under the tools (connectors) menu in
   the chat box.

5. **Start Caliper:** `uv run python -m caliper.app`. Claude Desktop reaches the open window.
   If Caliper isn't running, the tools still list, and each call says to open Caliper.

## Settings

| Variable | Read by | What it does |
|---|---|---|
| `CALIPER_MCP=off` | the Caliper app | Don't listen for Claude Desktop at all. |
| `CALIPER_MCP_SOCKET` | the app and `caliper-mcp` | Use another socket path (both sides must agree; set it in the config's `env` too). |

## How Claude's changes reach your sketch

Claude works on the tab you're in ([ADR 0015](adr/0015-sketching-in-3d-from-the-parts-planes.md)):

- **2D:** the sketch you test on, as before.
- **3D:** the part. Drawing goes into the sketch you have open. In a part with no sketch yet,
  Claude's first drawing makes one on the Top plane. A proposal is shown facing the sketch it
  draws in, over the part; Accept leaves that sketch open. Claude can extrude too.
- **Sketches on faces** ([ADR 0016](adr/0016-sketching-at-any-angle-and-on-faces.md)):
  `create_sketch` starts a sketch on a plane (`"xy"`, `"xz"`, `"yz"`) or on a flat face of an
  extrude, such as `{"feature": "e3", "face": "end"}` for its top, and Claude's drawing then
  goes into it. `inspect_faces` lists an extrude's faces and where each is. A sketch on a face
  follows it when the extrude changes, and a cut from a face goes into the part unless
  `reversed` says otherwise.

Switching tabs while a proposal is pending leaves it waiting on its tab: it's on the card
again when you come back, and Claude adds to it there. Meanwhile Claude's calls work on the tab
shown, and its first call after the switch is told where its changes are. If that first call
would change something, it's refused once and nothing changes: Claude sent it for the tab its
other changes are on, and can send it again if it does belong on the tab shown. Opening
another document in a tab drops that tab's proposal, as before.

A proposal that changes the part's solid (an extrude, or a change to a sketch one reads) is
drawn in the 3D view in the agent's colour before you accept it.

- A call that changes something (create, edit, constrain, ...) runs on a draft of your
  sketch. The draft appears as a proposal on the canvas, with ghost geometry and each check
  before and after. Further changes join the same proposal.
- **Accept** applies the whole proposal as one undo step, credited to the agent. It applies
  what the draft already built and checked, without solving any of it again, so even a
  250-change proposal applies at once. **Reject** discards it. Claude can't accept.
- To stop Claude part-way, stop it in Claude Desktop, then press **Reject**: the draft is
  dropped and your sketch is as it was. Nothing Claude does touches the sketch before Accept.
- A check Claude runs is saved in your sketch with its other changes, so it's part of the
  proposal, and it stays in the Checks panel (and the file) once you accept. A check run while
  nothing else is pending (just after you accepted, or when it only looked) is a proposal of
  its own. Running the same check again corrects its value; `remove_check` deletes one.
- If you edit the sketch or open another document while a proposal is pending, it's
  dropped. Claude's next call starts from your sketch as it is, and its result begins with a
  note saying what happened (accepted, rejected, sketch changed, another document opened).
- While Caliper's own assistant is working on a request, changes from Claude Desktop are
  refused with a "try again" message; looking still works.
- Each call shows in the Assistant tab, marked with the client's name (e.g. `claude-ai →`).
- **Mirror, linear pattern, and circular pattern** (`mirror_entities`, `linear_pattern`,
  `circular_pattern`) repeat geometry in one call: the copies and the constraints that tie
  them to the original. A mirrored copy is held by symmetric constraints about the line. A
  linear pattern's copies are joined by construction lines equal and parallel to the first,
  which carries the spacing as one dimension. A circular pattern's copies sit on construction
  circles round the centre, joined by equal chords, so a full circle's spacing comes from the
  count (a partial one has one angle dimension), and copies whose points land together, like
  a star's corners, are joined so the outline closes. Editing the original, the line or
  centre, or that one dimension moves every copy. They're made of the same commands Claude
  could send one by one, so they join the proposal like any change; Claude's `undo` takes
  back a whole mirror or pattern. Arcs mirror but don't pattern; rectangles don't turn.
- A proposal of more than 6 changes shows a summary ("187 changes · 57 checks, all passing",
  failures in red) with the list of changes behind **Show changes**, so the card keeps its size
  and Accept stays in reach; errors and failing checks are always shown.
- Only one Caliper window serves Claude Desktop at a time; a second one says so in its status
  bar. macOS and Linux only (Unix sockets).

### What a change's result tells Claude

A call that changes the sketch answers with `applied`, the undo `label`, the ids it
`created`, and `changed`: each entity `added`, in full; each one `modified`, in full for the
first 8 and then by id in `also_modified` (a solve can move dozens of points a little); and
the ids `removed`. `inspect_entities` gives any of them in full. The command isn't echoed
back: the stored entities say the same, resolved, in fewer tokens. A change that changes
nothing answers `"changed": "nothing: the document already was that way"`.

A mirror or pattern answers with its `label`, each original's `copies`, the `construction`
geometry, `constraints`, and `dimensions` it made (by id), anything it `skipped` and why, a
`note` if something is left free, and `changed` with the copies and construction geometry in
full. If Caliper rejects any step, none of it is kept, and the error names the step.

## Manual test

Use this after setup, and after changes to `caliper/ai` or the bridge. It needs no API key and
makes no API request of Caliper's own; Claude Desktop uses your Claude account.

1. Start Caliper: `uv run python -m caliper.app`. Check `~/.caliper/mcp.sock` exists and is
   yours only: `ls -l ~/.caliper/` shows `srwx------`.
2. Make sure the `caliper` entry is in Claude Desktop's config (Set up, step 3).
3. Restart Claude Desktop.
4. In a new chat, open the tools menu: **caliper** is listed with 28 tools.
5. Ask: *"Show me the entities near the origin in Caliper."* Claude calls
   `inspect_document` (allow it when asked). The Assistant tab in Caliper shows
   `claude-ai → inspect_document`, and nothing changes on the canvas.
6. Ask: *"Create a rectangle 100 mm by 50 mm with its corner at the origin, and check its
   width."* Claude calls `create_rectangle` and `run_check`.
7. In Caliper: a proposal card "Create Rectangle" with ghost geometry, and the width check
   passing. The sketch itself is still empty (the Sketch tab lists nothing).
8. Ask Claude for something invalid, e.g. *"make a circle with radius -5"*. Caliper rejects
   it (`value.not_positive`), Claude sees why, and nothing is added.
9. Click **Accept** (⌘Return). The rectangle is now real; History shows one agent step.
10. Ask Claude: *"What's in the sketch now?"* Its tool result begins with a note that you
    accepted its changes.
11. **Edit → Undo** (⌘Z): the rectangle goes, in one step.
12. **Edit → Redo** (⇧⌘Z): it comes back.
13. Optional: ask for another change, then press **Reject**, or draw something yourself
    before accepting. Claude's next result says its changes were dropped, and the sketch
    keeps only what you accepted.

If step 4 shows no tools, check Claude Desktop's MCP log (Settings → Developer, or
`~/Library/Logs/Claude/mcp-server-caliper.log`) for the error, usually a wrong path in the
config.

To keep a record of a test (the prompt, Claude's output, the drawing, the timing, and your
own notes), follow [test-runs-andre/README.md](../test-runs-andre/README.md).

## Timing

Caliper times each Claude Desktop task by itself; no stopwatch. The **Timing** panel, above
Properties, shows the latest run on one line, e.g. `▸ 4m 07s · ~1m 20s left`. Click the
line for every field. While the run is live its time ticks every second. The numbers stay up
after the run ends. To keep it in sight with the right panel folded away (⌘⌥B, or the strip on
the view's right edge), pop it out: Agent → Pop Out Timing, or the button on its title bar. It
then floats over the part's top right corner until you dock it again.

**Time left.** At the start of a task of more than about ten calls, Claude says how many calls
it plans, and, if it knows, how many are mirrors or patterns and how many are checks (the
`report_progress` tool, one quick call). The line then shows the time left:
`▸ 1m 12s · ~3m 40s left`. The call count isn't on the line; it's in the fields below.

- **How it's worked out.** A call costs two things, both measured by Caliper: the time
  between calls (Claude working out the next one, the MCP round trip, and redrawing, which is
  most of it) and the time inside Caliper (the command, the solve, and the proposal). They
  depend on the kind of call: a mirror or pattern is a long solve that Claude thinks about
  first, and checks come in quick batches. So each kind of call still to go is priced at what
  that kind has cost so far in this run, and the calls Claude didn't sort by kind at the
  run's average. Claude does none of the arithmetic.
- **At first, and as it learns.** The first estimate uses typical costs from test 003 (1.7 s a
  call, 0.8 s a check, 5 s a repeat), blended out as the run's own calls are measured. Until
  ten calls are measured it's rough, and says so: `about 4 min left`, or `under a minute
  left`. After that it's to the second.
- **Steady on screen.** It counts down every second. Every 15 seconds, and as soon as a
  mirror or pattern finishes, it moves halfway to the latest figure, so one slow or quick call
  doesn't make it jump. A new plan from Claude, or a figure more than half out from what's
  shown, replaces it at once.
- **When it can't say.** `no estimate` means Claude hasn't said how many calls it plans.
  `finishing…` means the plan's calls are done (or the countdown ran out on the last one)
  and Claude is still working. When the run stops, the line shows just the final time.
- Claude calls `report_progress` with 0 when it's done, which stops the time at once. The
  estimate is shown only on screen, never recorded.

**Saving the drawing into its test folder** (`test-runs-andre/NNN-name/`) writes that
folder's `003-timing.md` for you, from this panel. It never overwrites one that's already
there, so save after you accept or reject. **Copy for 003-timing.md** puts the same text on
the clipboard. The format is exactly:

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

Every AI/MCP test uses these fields, these names, and these definitions:

| Field | What it measures |
|---|---|
| **Date** | The day the run started. |
| **Total run** | From sending the task until Claude's last tool call has its answer. Sending is when you pressed Start run; without it, when Claude's first call reached Caliper. |
| **MCP/tool calls** | Every tool call that reached Caliper in the run, including refused and failed ones. |
| **First response** | From sending the task (Start run) until Claude's first tool call reached Caliper. |
| **Longest tool call** | The slowest single call, from when it reached Caliper until its answer was ready: the command, the solve, and redrawing the proposal. |
| **Proposal creation** | From the start of the first call that changed the pending proposal to the end of the last one, including any looking in between. If you accepted part-way and Claude carried on, each proposal's time adds up. |
| **Accept** | From pressing Accept until the changes are in the sketch (every command through the solver, as one undo step). Several accepts in one run add up. |

A value Caliper couldn't measure shows **N/A**, never an estimate:

- **First response** is N/A unless you pressed Start run.
- **Proposal creation** is N/A when Claude only looked and made no proposal.
- **Accept** is N/A when you rejected the proposal, it's still pending, the sketch changed
  under it, or there was none.
- **Total run** and **Longest tool call** are N/A until Claude's first call.

Rounding: `Xm XXs` is to the nearest second, and `X.Xs` to a tenth of a second (so a call
under 0.05 s shows `0.0s`). MCP/tool calls is a plain count.

### When a run starts and ends

Caliper sees a task only through Claude's tool calls. It can't see you press Enter in Claude
Desktop, or Claude's closing message after its last call, so:

- **Press Start run just before you send the prompt**: the button in the Timing panel, or
  Agent → Start Timing Run (⌘⇧R). First response and Total run then count from that press,
  including the second it takes to switch to Claude Desktop and send.
- **Without it, a run starts at Claude's first call.** A new run also starts when another
  document is opened (File → New or Open), or at a call that comes after 3 minutes with
  none. Pressing Start run and then File → New keeps the run you started.
- **A run ends when Claude's last call has its answer.** Claude's closing message isn't
  counted. Accepting part-way doesn't end the run.
- **On screen, the time keeps ticking while the run is live**, and stops by itself: when
  Claude says it's done (`report_progress` with 0), or after 60 seconds with no call (3
  minutes while Claude is still short of the calls it planned: it's thinking, not done). It
  also stops when you accept or reject the proposal, press Start run, or open another
  document. A call after that starts it again. Then Total run settles on the recorded value
  above, from the start to the end of Claude's last call; your review time isn't counted.
- If you send a new task within 3 minutes without Start run or File → New, it joins the
  previous run. For tests, press Start run every time.

Only Claude Desktop's MCP tasks are timed; the in-app assistant's aren't. The clock is the
system's monotonic clock, read twice per call and once each side of Accept, inside Caliper
(`caliper/app/agent/timing.py`), so timing adds no noticeable time to a call.
