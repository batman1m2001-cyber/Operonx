# Jev as an agent's tool selector — measured, not built (2026-10-18)

**The idea.** An operonx agent would let Jev (TypeSafe's decision model on OpenRouter, `typesafe/jev-1.14`) decide
which tools a turn needs, and a real LLM would still write the answer. It would be configured at `Agent(...)` init,
with a decision op in operonx core. The goal is to cut the cost of tool choice.

**The decision.** Do not build the selector. On a real agent, it would raise the cost of each request about 3× and
add about 0.6 s to every turn. Its picks agree with the model's on 59% of turns. The reasons and numbers follow.

## What was measured

- **The agent.** The OneWealth assistant (operonx-agents 0.2.x), client role: 27 tools, `anthropic/claude-haiku-5.5`
  via OpenRouter.
- **The turns.** The 153 model turns of its stress test's final run, 81 real-customer conversations
  (mr-finance `docs/brd/testing/assistant/`).
- **Spend.** US$0.0125 for the 153 Jev decisions, plus about US$0.001 for three cache probes.

### 1. Jev's picks

Jev gets the latest message, plus the previous one for follow-ups. It answers one Choice over the role's tools plus
"no tool needed". Each criterion is the tool's own description.

**How often its top pick is a tool the model really called:**

| Measure | Result |
|---|---|
| All turns | 90 of 153 (59%) |
| Turns where the model used a tool | 70 of 114 |
| Turns where it used none | 20 of 39 |
| Confidence ≥ 0.9 | 33% of turns, 82% right |
| Confidence ≥ 0.8 | 44% of turns, 79% right |

For writes, the call shows only after the person confirms, so it was read from the `wait` event.

**As a pruner** (keep every tool with probability ≥ 0.02), the kept set holds every tool the model called on 118 of
153 turns, with 3.2 tools on average. So one turn in four would lose a tool it needed.

**Many "misses" are not Jev's fault.** In follow-ups ("Còn nợ thì bao nhiêu?") the model answers from the
conversation, which Jev does not see. Jev also cannot see the agent's context: the graph node the person picked, the
active scenarios. Real confusions remain too: "send this plan to my RM" was picked as the scenario tool, with
confidence 0.95.

**Latency.** A median of 0.57 s per decision (p90 0.60 s), and it has to finish before the model call starts.

### 2. What tool choice costs today

Three identical-shape calls to the agent's model, with its real schemas and instructions:

| Call | Prompt tokens | Cached | Cost (US$) |
|---|---|---|---|
| All 27 tools, cold | 7,294 | 0 | 0.00092 |
| All 27 tools, warm (every turn after the first) | 7,294 | 7,198 | **0.00009** |
| Three tools (a Jev-pruned set) | 1,497 | 0 | 0.00019 |

**The schemas are ~80% of the prompt, but they are cached.** operonx-agents puts cache breakpoints on the system
prompt and the last message, and the provider caches tools, then system, then messages, as one prefix. A cached read
costs a tenth of the price.

**A list pruned per turn changes the prefix.** The tools and everything after them (system, history) miss the cache.

| Per model request | Cost (US$) | Added wait |
|---|---|---|
| Today (full list, cached) | 0.00009 | 0 |
| Jev + pruned list (cache miss) | 0.00008 + 0.00019 ≈ 0.00027 | +0.57 s |
| Jev + pruned list, even if that exact set were cached | ≈ 0.00010 | +0.57 s |

**Even in the best case it saves nothing.** It adds a serial network call to every turn of an assistant whose users
already find it slow.

## When it would pay

1. **A provider without prompt caching, or very large tool sets.** For example, hundreds of tools behind an MCP
   gateway, where even a cached prefix is heavy or exceeds the model's limits. There, pruning to the top-k with a
   fallback to the full list is the right shape. It would be an `Agent(tool_selector=...)` hook on `before_model`,
   which can already rewrite `ModelRequest.tools` with no runner change.
2. **Cutting latency, not cost.** A confident Jev pick of a tool that takes no arguments could run that tool directly
   and skip the first model round-trip (~1.9 s here). At confidence ≥ 0.9, that is a third of turns and 82% right.
   That means about one wrong extra tool in six, which the answer model then has to ignore. It is worth trying only
   as part of a latency plan, measured against the alternatives (a smaller tool-choice model, parallel tool
   prefetch).
3. **A generic decision op in core.** A `DecisionOp` for typed classification (choice, score, box) has uses beyond
   tools: routing a message, triage, or an eval grader. It does not depend on this result. It is a separate,
   smaller proposal.

## How to reproduce

- **The benchmark and the cache probe.** These are the session's scratch scripts (`bench.py`, `cache_probe.py`).
  Both read the stress test's run files and the agent's own tool registry.
- **The Jev request.** `POST https://openrouter.ai/api/alpha/decisions` with `{"model": "typesafe/jev-1.14",
  "state": ..., "questions": {"tool": {"type": "choice", "instructions": ..., "criteria": {name: description}}}}`.
  The answer carries `choice`, `probabilities` and `confidence`.
- **Not the router.** `typesafe/jev-router` is a different product: a model router. It is not the decision model.
