# Handoff — read this first

Written 12 Aug 2026 for whoever picks this branch up next, human or agent.
It exists because the useful context from this work is spread across five
documents and none of them says *where to start*.

**Branch:** `fix/core-f2-f8` → PR against `main` for **1.3.0**.
**State:** green. 1682 unit tests, 70 harness unit tests, 25 live tests
against a real tool-calling model. `ruff` and `mkdocs --strict` clean.

---

## 1 · What this branch did

Three things, in order:

1. **Closed eight core defects** (§16 F1–F8 of the plan) — cancellation,
   trace filtering, the observability circuit breaker, stream liveness,
   and structured-output parsing.
2. **Built the agent framework's last two phases** — `operonx/agents/mcp.py`
   (MCP client) and `operonx/agents/heartbeat.py` (scheduled agents), plus
   `packages/operonx-code/`, a reference coding agent that exists to
   measure how much a real agent has to add. Answer: ~165 lines.
3. **Ran four adversarial reviews**, which produced **31 reproduced
   findings**. Nine are fixed here. **Twenty-two are open.**

That last number is the important one.

---

## 2 · Where to start

Read in this order. Each is short and each answers a different question.

| # | File | Answers |
|---|---|---|
| 1 | **`docs/design/OPEN_FINDINGS.md`** | What is known-broken *right now*, with a runnable repro for each |
| 2 | `docs/architecture/failure-modes.md` | The nine mistakes this codebase keeps making. Read before any non-trivial fix |
| 3 | `CHANGELOG.md` → 1.3.0 | What changed and what breaks on upgrade |
| 4 | `AGENT_EXTENSION_PLAN.md` §0 | What the agent layer is and what is left |
| 5 | `docs/design/AGENT_PLAN_ARCHIVE.md` | The evidence trail, including three plausible findings a probe **disproved** |

Do **not** read the plan documents for current behaviour. They describe
intent, and several of their rows were measured to be wrong.

---

## 3 · What I would do next

In priority order. My reasoning, not instructions — override it freely.

**First: the four open findings that are silently wrong.** Silent beats
loud every time in this codebase, and each of these hands a caller a
plausible value:

| | Where | What a caller sees |
|---|---|---|
| **C1** | `core/ops/base.py:513` | `Media` unwrapped only on an op's first-ever call — the op body gets a different *type* depending on invocation order |
| **A1** | `agents/graphs/react.py:270` | A budget-exhausted turn strands an unanswered `tool_call`; the provider rejects the request *one turn later* |
| **P1** | `operonx-code/tools/search.py:145` | `grep` answers differently depending on whether `ripgrep` is installed |
| **M1** | `agents/mcp.py:173` | An `MCPClient` closed from the wrong task cancels an *unrelated* task |

**Second: A2 and A3** — `AgentSession.send` not rolling back on timeout,
and tool *arguments* never being redacted in the approval payload. Both
small, both real.

**Third: the `operonx-code` gaps** — `webfetch` has no live test, no
sub-agent tool is wired, and the REPL renders only the final answer even
though token streaming became possible when F5 was fixed.

**Never dogfooded.** The coding harness has only ever run against toy
fixtures. Every single live run in this project's history found a defect a
full unit suite had missed — four on the first agent run, one on the first
coding-agent run, three on the first MCP run. Pointing it at a real repo
with a real task is, by track record, the highest-yield hour available.

**Do not build** the remaining P5 heartbeat features without a use case.
The scheduler that exists covers the known need.

---

## 4 · Running things

```bash
uv sync --all-extras
uv run pytest tests/ -m "not integration"          # 1682, ~90s
uv run ruff check operonx/ tests/ packages/
uv run mkdocs build --strict
```

The harness is a separate package and is not on the default path:

```bash
PYTHONPATH=packages/operonx-code uv run pytest packages/operonx-code/tests -m "not integration"
```

### Live tests

They cost money and need a **tool-calling** model. Many OpenAI-compatible
gateways accept `tools` and answer in prose anyway — vLLM needs
`--enable-auto-tool-choice`, and an agent pointed at such a server never
calls a tool and merely looks unhelpful.

```bash
export OPERONX_TEST_LLM_URL=https://llm.siraya.ai/v1
export OPERONX_TEST_LLM_KEY=<QWEN_API_KEY from the callbot .env>
export OPERONX_TEST_LLM_MODEL=qwen3.7-plus          # no "siraya/" prefix
uv run pytest tests/internal/agents/ -m integration
```

| Suite | Covers |
|---|---|
| `test_live_agent.py` | the `LLMOp` ↔ loop seam |
| `test_live_e2e.py` | multi-turn, compaction, parallel calls, sub-agents, redaction, budget |
| `test_live_mcp.py` | a real model driving real MCP tools |
| `packages/operonx-code/tests/test_live_coding.py` | the coding harness |

Expect ~9–13 s per test; that is the model, not the code.

---

## 5 · Traps that cost me time

- **Credentials** live in `/home/thanglq/educa-reminder-agent/.env`, which
  uses ` = ` spacing and **cannot be shell-sourced**. Parse it.
- **The model id is `qwen3.7-plus`**, not `siraya/qwen3.7-plus`.
- **One live test flakes** —
  `test_tool_result_json_does_not_break_the_next_call` fails maybe one run
  in five on model non-determinism. It passes on rerun; it is not a
  regression.
- **An unannotated MCP tool is gated by default.** That is deliberate
  (absent hints mean unknown), but a live test with no `on_approval`
  callback will sit out the full 300 s approval timeout *per test* and look
  like a hang. It cost me two debugging rounds.
- **`str.replace` in a patch script that silently matches nothing.** I did
  this twice and both times shipped a validation that validated nothing.
  Assert the replacement, or use a real editor.

---

## 6 · The one rule worth carrying

Every serious defect in this project came from an unmeasured assumption
about operonx — not from a hard problem.

> A passing test proves the code does what you wrote. It never proves your
> assumption about the dependency was right.

`exclude=` was documented as filtering the trace and did not.
`stream(mode="updates")` was assumed live and was not.
`glob("**/*.py")` was assumed to work and returned zero.
`Interrupt()` was assumed to cancel a branch and cancelled the run — and
the fix for it was *still wrong* on three of five paths, because the
regression test shared an assumption with the fix.

Probe the primitive. Run it live. Check what the consumer actually
receives. The unit test is the weakest of the three.
