# Contributing to `operonx/agents/`

This package has a lower ceiling than the rest of operonx. Core is a
general execution substrate; this is a **composition layer**. Code that
belongs in core does not belong here, and — more often the live risk —
code that belongs in a user's own repo does not belong here either.

Two rules govern every PR: the **Footprint Ladder** (where does this go?)
and the **op-worthy bar** (does this deserve to be an op at all?).

---

## 1 · The Footprint Ladder

```
 6. core          ██████  ← reserve for absolute universals
 5. MCP           █████
 4. plugin        ████
 3. gated tool    ███
 2. CLI + skill   ██
 1. extend op     █       ← START HERE
```

**Start at rung 1 and climb only when the rung below provably cannot
work.** Each rung up multiplies the number of users who pay for the
decision — in import time, in API surface they must learn, and in the
compatibility promise we then owe them.

| Rung | Means | Cost of getting it wrong |
|---|---|---|
| 1 · extend op | A new `@op` or `@graph` in the caller's own code | None — delete it |
| 2 · CLI + skill | A `SKILL.md` or an `operonx` sub-command | Low — opt-in |
| 3 · gated tool | A tool in `TOOL_REGISTRY` behind a permission policy | Medium — security surface |
| 4 · plugin | A registered extension point others build against | High — versioned contract |
| 5 · MCP | An external protocol server | High — cross-process contract |
| 6 · core | A primitive in `operonx/core/` | Permanent — every user, every release |

**A PR that adds a rung-6 primitive must state in its description why
rungs 1–5 don't work.** Not "it's cleaner at rung 6" — why the lower
rungs *fail*. The 1.2.0 removal of `OnnxOp`/`TritonOp` is the cautionary
tale: both were rung-6 primitives that named a *transport* rather than a
semantic, so every backend needed its own copy. They should have been
rung-1 `@op`s over a plain client class, which is exactly what replaced
them.

---

## 2 · The op-worthy bar

Not every function deserves to be an op. Before adding one, it must clear
**all four** criteria — the same bar `OP_TAXONOMY_REFACTOR_PLAN.md` used
to reject six speculative ops:

1. **Complex I/O contract** — enough inputs/outputs that wiring it by
   hand is genuinely error-prone. A one-in-one-out pure function is a
   helper, not an op.
2. **Rich tracing metadata** — there is something worth seeing in a span
   that the caller could not see otherwise (latency of a real I/O call,
   a model name, a token count).
3. **Reusable shape** — at least two *concrete, existing* call sites
   want it. Not "users might." Zero demand is a rejection, and
   demonstrating demand is the proposer's job.
4. **Non-trivial code volume** — if the op body is thinner than its
   `Param` declarations, the declarations are the product and you have
   inverted the cost.

Name ops for the **semantic** they provide, never for the transport that
implements it. `VectorSearchOp` (semantic, many backends) is right;
`TritonOp` (transport) was not.

---

## 3 · Design rules specific to this package

These come from `AGENT_EXTENSION_PLAN.md` §6 and are not negotiable
without amending that document.

**Rule 1 — Yield + fan-out beats imperative iteration.** Never write a
`for` loop inside an op if you can `yield` instead. A generator op plus
`Ref.parallel(max=N)` downstream buys per-item concurrency, per-item
trace spans, per-item ctx isolation, and streaming-to-caller. An
in-op loop buys none of them and hides all four.

**Rule 2 — Loops go through back-edges.** Every loop — outer ReAct,
inner retry, sub-agent turns — is a back-edge inside a `@graph`, compiled
to a hidden `_GraphLoop` at build time. No imperative loop wrapper.

**Rule 3 — Reducers own accumulation.** Accumulating cells (messages,
cost, tool stats) use `PARENT.declare(..., reducers={...})`. Do not write
a merger op; the framework merges under a bounded lock.

**Rule 4 — Retry taxonomy is honest.** Transport failures belong to the
SDK, parse/validate failures to `LLMOp.of(max_retries=…)`, refusals to
`fallback=`, and semantic failures to another loop iteration. Do not
build a retry wrapper that crosses those streams — it just doubles
someone else's exponential backoff.

---

## 4 · Security posture

Agent tools execute model-chosen actions. Two invariants:

- **Destructive tools route through `InterruptOp`.** Anything that
  writes, deletes, spends, or sends must pass a permission gate the
  caller can answer. Approval is per-call; it does not carry to the next
  call, and a policy that says "allow" must be set by a human, never
  inferred from a previous approval.
- **Fail closed on permission, fail open on classification.** If the
  permission check itself errors, deny. This is the opposite of a
  quality classifier — that one fails open so a broken model never
  silences a real user — and mixing up the two directions is how a gate
  becomes decorative.

---

## 5 · Test expectations

- Unit tests run on every PR. If a test needs a live service, mark it
  `@pytest.mark.integration` — but then it is **not** covering you in
  CI, so pair it with a unit-level test that does run.
- **Test through the engine, not just the builder.** A graph that
  compiles is not a graph that runs. Assert on `Operon(...).run(...)`
  output — output-inference bugs (operonx infers declared outputs by
  AST-parsing the literal return dict) are invisible at build time and
  fatal at runtime.
