# AGENTS.md — operonx-agents

Typed LLM steps and agents on **operonx** (editable from this repo, `../..`). Users import it as
`operonx.agents` (operonx >= 1.16 aliases this package); inside the package, `operonx_agents`. Read the operonx guide
(`../../operonx/guide/`, then `09-agents.md`) before using an operonx API; do not write one
from memory.

## Rules

- **Every `@graph` at module level, never inside a function** (operonx guide 05). Settings are
  graph inputs; another shape is another module-level graph or an `if_`. Keep the edges written
  out. Benchmark scripts under `scripts/` that build graphs in functions are old and not a
  pattern to copy.
- The agent loop is plain async code inside one op (decision D1); every model call, tool call and
  sub-agent is a traced child execution. Do not turn it into a graph back-edge.
- Never `print()`; log with `from operonx.core.loggings import LOGGER`.
- Tests are offline: script models with `operonx_agents.testing` (`scripted`, `ScriptedLLM`).
