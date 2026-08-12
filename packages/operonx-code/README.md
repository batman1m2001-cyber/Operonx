# operonx-code

A reference coding agent built on `operonx.agents` — read, search, edit
and run code in a directory, with a human gate on anything destructive.

It exists to answer a question the framework cannot answer about itself:
**how much does a real agent have to add?** The whole harness is around
900 lines, of which roughly 200 are the agent. Everything else is tools,
a sandboxed filesystem view, and a terminal. The loop, tool dispatch,
approval, compaction, prompt-cache shaping and redaction all come from
`operonx.agents`.

Deliberately a sibling package, not part of the framework. A coding agent
needs opinions — which tools exist, what the prompt says, when to ask a
human — and opinions in a framework become defaults nobody can change.

## Install

```bash
pip install -e packages/operonx-code       # plus [web] for the webfetch tool
```

## Run

`resources.yaml` needs an entry for the model, which **must support tool
calling** — several OpenAI-compatible gateways accept `tools` and answer
in prose instead:

```yaml
llm:
  code:
    api_type: openai
    api_key: ${LLM_API_KEY}
    base_url: https://your-endpoint/v1
    model: your-tool-calling-model
```

```bash
operonx-code --root . --resource code
```

```
› what does the scheduler do when an op yields an Interrupt?

  needs approval: bash grep -rn "Interrupt" operonx/core/ops/graph/
  [y]es / [n]o / [a]lways: y
```

`/help` lists the commands. `Ctrl-D` exits.

## Tools

| Tool | Gate | Notes |
|---|---|---|
| `read` | — | Numbered lines. Reading is required before editing |
| `glob` | — | By filename, newest first, skipping `node_modules` and friends |
| `grep` | — | By content; uses `rg` when present, pure Python otherwise |
| `edit` | asks | Exact-string replace; refuses an ambiguous match |
| `write` | asks | Whole-file; refuses to overwrite something unread |
| `bash` | asks | Persistent session — `cd` and `export` carry over |
| `webfetch` | — | Blocks loopback and link-local, including cloud metadata |

## The three invariants worth knowing

**Path containment.** Every filesystem tool resolves through the
workspace, which calls `realpath` *before* testing containment. A symlink
inside the root pointing at `/etc` is the whole attack, and a lexical
prefix check passes it.

**Read before edit.** `edit` refuses a file the agent has not read, and
refuses again if it changed since. A model editing from memory produces a
patch that applies cleanly to the file it imagined — the edit succeeds,
and you find out later.

**The shell is one process.** `cd src` then `ls` lists `src`. On timeout
the shell is killed and replaced, and the error says so: a command
abandoned mid-flight leaves unknown state, and silently starting fresh is
how an agent ends up confused about its own `cd`.

## `--yes`

Pre-approves every destructive tool for the session. It removes the only
check between the model and `rm -rf`, so it is a flag rather than a
setting, and it prints a warning.

## Using it as a library

```python
from operonx.agents import AgentSession
from operonx_code import build_coding_agent

async with build_coding_agent(root=".", resource="code") as agent:
    session = AgentSession(agent.graph)
    result = await session.send("what does workspace.py do?")
    print(result["final"]["content"])
```

`build_coding_agent` owns a subprocess — close it, or use the context
manager.

## Tests

```bash
uv run pytest tests -m "not integration"     # no network
uv run pytest tests -m integration           # needs a live tool-calling model
```
