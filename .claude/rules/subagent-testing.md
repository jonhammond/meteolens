# Subagent Testing & Output Discipline

## Testing tier
- Testing (running test suites, validation passes, verification checks, smoke tests)
  MUST be dispatched to lower-tier subagents: `Agent(model: 'haiku')` for quick
  mechanical runs, `Agent(model: 'sonnet')` for test authoring or multi-step flows.
- Never run test phases inline on the top-tier session model when a subagent can
  do it; the orchestrator reviews results only.
- In this repo, "testing" includes `pbir validate --all`, filter enumeration
  checks, and screenshot/ExportTo render verification — all suited to Haiku.

## Subagent output contract (append to every test dispatch prompt)
- Minimize conversational output; do NOT narrate your train of thought or process.
- Final summary: 10–15 lines maximum, concise, one fact per line.
- Report only: pass/fail counts, failing test names with file:line, error messages
  verbatim (trimmed), and anything blocking. No restating the task, no preamble.
