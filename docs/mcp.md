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
         "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/caliper", "--extra", "mcp", "caliper-mcp"]
       }
     }
   }
   ```

   No `env` block, and no key: the server needs none.

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

- A call that changes something (create, edit, constrain, ...) runs on a draft of your
  sketch. The draft appears as a proposal on the canvas, with ghost geometry and each check
  before and after. Further changes join the same proposal.
- **Accept** applies the whole proposal as one undo step, credited to the agent. **Reject**
  discards it. Claude can't accept.
- If you edit the sketch or open another document while a proposal is pending, it's
  dropped. Claude's next call starts from your sketch as it is, and its result begins with a
  note saying what happened (accepted, rejected, sketch changed, another document opened).
- While Caliper's own assistant is working on a request, changes from Claude Desktop are
  refused with a "try again" message; looking still works.
- Each call shows in the Assistant tab, marked with the client's name (e.g. `claude-ai →`).
- A proposal of more than 6 changes shows a summary ("187 changes · 57 checks, all passing",
  failures in red) with the list of changes behind **Show changes**, so the card keeps its size
  and Accept stays in reach; errors and failing checks are always shown.
- Only one Caliper window serves Claude Desktop at a time; a second one says so in its status
  bar. macOS and Linux only (Unix sockets).

## Manual test

Use this after setup, and after changes to `caliper/ai` or the bridge. It needs no API key and
makes no API request of Caliper's own; Claude Desktop uses your Claude account.

1. Start Caliper: `uv run python -m caliper.app`. Check `~/.caliper/mcp.sock` exists and is
   yours only: `ls -l ~/.caliper/` shows `srwx------`.
2. Make sure the `caliper` entry is in Claude Desktop's config (Set up, step 3).
3. Restart Claude Desktop.
4. In a new chat, open the tools menu: **caliper** is listed with 21 tools.
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
