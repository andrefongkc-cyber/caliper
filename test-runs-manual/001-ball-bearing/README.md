# 001 · Ball bearing

- **Test:** 001
- **Name:** ball-bearing
- **Date:** 2026-09-26
- **Tests:** a short, ambiguous prompt for a real part: whether Claude picks a sensible
  standard size, draws the individual balls, and fully constrains it over MCP
- **Description:** "a 5mm bearing with the individual balls", sent in Claude Code (the Code
  tab of the Claude app) to Caliper over MCP. This ran before Caliper had its timing system,
  so `timing.md` comes from the session's own timestamps, and Longest tool call, Proposal
  creation, and Accept weren't measured.

## Files

- `test.caliper`: the drawing
- `prompt.md`: the prompt, exactly as sent
- `claude-output.md`: Claude's output, as-is
- `timing.md`: the timing
- `claude-results.md`: Claude's summary
