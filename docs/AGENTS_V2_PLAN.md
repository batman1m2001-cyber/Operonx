# Agents v2: `operonx-agents`

**Status:** plan. Phase A0, the evidence spike, is done (2026-10-04). D1 is waiting on your decision.

**Design source:** `docs/roadmap/track3_agents.md` §4 and `docs/roadmap/ROADMAP.md` §4. This doc
does not repeat the design. It records what A0 measured, what that changes, and the phases.

**Evidence:**
- Spike repo: `/home/thanglq/operonx-agents-spike`. Its README reproduces every number below.
- Throwaway branch `spike/child-exec` (never merged).

## 1. Decisions

| # | Decision | Recommendation | Evidence |
|---|---|---|---|
| **D1** | The agent loop lives in one op as plain `async` code with traced child executions, not as a graph back-edge | **Yes** | §2: under the 2 ms/turn gate, 7–9× less CPU per turn than the back-edge loop, 6× fewer trace records, and a Tree view that reads as the conversation. The Workflow view regresses until A5 (§2b), so A5 carries a screenshot gate for it |
| D2 | A separate repo and distribution, `operonx-agents` | Yes (ROADMAP §0) | n/a |
| D3 | Deprecate `operonx.agents` one release after `operonx-agents` 1.0 | Yes (ROADMAP §0) | n/a |

**If D1 is rejected**, the back-edge loop stays. Its measured cost after the dispatch fix is
1.7–2.6 ms CPU per turn of pure orchestration, which is 9.4–13.4 ms per turn at 5 concurrent runs.
That is over the gate at every setting measured. The four workarounds in track3 §1.2 item 7 then
move into core work.

## 2. What A0 measured

### a) Per-turn framework overhead

**Setup:**
- A scripted 0 ms model and 10 turns per run.
- 200 runs per cell, after 20 warm-up runs.
- Both loops do the same work: the in-op loop calls the very functions behind the graph loop's
  nodes, and every run's transcript is checked against the graph loop's.

**Controls:**
- A no-engine floor doing the same work.
- A one-op engine run (0.35 ms per run).
- The idle event-loop lag (p99 0.39 ms).

| loop | calls/turn | CPU ms/turn | orchestration ms/turn (minus floor) | latency ms/turn at 5 concurrent, p50 / p95 | event-loop lag p99 at 5 |
|---|---|---|---|---|---|
| back-edge, today | 1 | 3.73 | +1.94 | 17.03 / 18.56 | 10.3 ms |
| back-edge, direct tool call | 1 | 1.99 | +1.72 | 9.43 / 10.77 | 3.9 ms |
| **in-op + child executions, direct tool call** | 1 | **0.22** | **−0.05** | **0.99 / 1.03** | **1.3 ms** |
| back-edge, today | 3 | 7.39 | +2.66 | 36.15 / 37.62 | 30.9 ms |
| back-edge, direct tool call | 3 | 2.92 | +2.63 | 13.40 / 14.79 | 6.6 ms |
| **in-op + child executions, direct tool call** | 3 | **0.42** | **+0.12** | **1.76 / 1.85** | **1.5 ms** |

How it scales, with 3 calls per turn (latency per turn p50):

| loop | 1 concurrent | 5 | 10 | 20 |
|---|---|---|---|---|
| back-edge, direct tool call | 2.85 ms | 13.40 ms | 28.73 ms | 61.85 ms |
| in-op + child executions | 0.39 ms | 1.76 ms | 2.99 ms | 6.06 ms |

**The gate is "in-op ≤ graph, and < 2 ms/turn at 5 concurrent".** It passes in every cell.
- Child executions cost +0.0 to +0.07 ms per turn.
- **A finding independent of D1:**
  - Today's `execute` (`agents/graphs/dispatch.py:318-321`) builds a `FuncOp` per tool call only to
    call its `.core`, which is the raw function. That costs about 1.7 ms per call: 97% of the work
    floor is `auto_name` bytecode disassembly plus `getsource`. It also buys no tracing (track3 §1.2
    item 3).
  - With it, even the in-op loop is 8.5 ms per turn at 5 concurrent (1 call) and 23.8 ms (3 calls).
  - The fix is one line (spike commit `ae4b140`). It is item K0 in A1.

### b) Trace fidelity

**The K1 prototype** is `operonx/core/child.py` on `spike/child-exec`. It is 143 lines, plus 49 lines
across `base.py`, `workflow_trace.py` and `build_tree`, plus 7 tests.
- The tests fail on main with `ModuleNotFoundError: No module named 'operonx.core.child'`. The full
  suite passes on the branch.
- `async with child("model", inputs=..., op_type="llm") as c:` records an `OpExecution` under the
  running op.
- Its ctx is the parent's plus `model[n]`, and its `op_full_name` is the parent's plus `.model`. So
  the parent's op_id is derived by construction, the same rule `UpstreamRef` uses.
- `build_tree` nests the child with no new stored field, so the Local, Langfuse, ClickHouse and Studio
  readers needed no change.

| one 4-turn × 2-tool run | back-edge | in-op + K1 |
|---|---|---|
| trace records | 90 (80 code, 10 branch) | 15 (agent, 4 turn, 4 llm, 6 tool) |
| tree rows / roots | 126 / 8 | 15 / 1 (control, same records on main: 43 / 5 with stand-ins) |
| Studio Tree view | per turn: context zone, model, router, dispatch subgraphs | agent → turn[i] → model, lookup; model input/output readable |
| Studio Workflow view | the loop drawn, run painted on it | "This run's graph isn't drawn here" |

Screenshots of both runs, Tree, Tree with a model selected, and Workflow, at 1600×1000 and 390×844:
`/home/thanglq/operonx-agents-spike/shots/{graph,inop}_{desktop,phone}_{tree,tree_model,workflow}.png`.

**The Workflow regression has one cause.**
- Studio's `graphForRun` (`static/studio.js:2948`) matches a run to a graph by the share of op names
  that ran. Child names count, so the score is 1/4.
- Even matched, the canvas would paint a single `agent` node.
- A5 fixes both: match on root records only, and let the canvas expand an op into its child
  executions.

Two smaller findings feed A5 and K7:
- Child rows named `model` borrow an unrelated graph's icon, because the icon is looked up by name.
- Flat `{name, args}` tool calls show `null` arguments in the inspector.

### c) Native structured output (15 requests, raw HTTP)

Each endpoint got a control that must fail: an invalid schema, and a `tool_choice` that names no
tool.

| endpoint | `response_format: json_schema` | forced `tool_choice` | logprobs |
|---|---|---|---|
| `inhouse` (gemma-4-E2B, vLLM) | **enforced**: in-enum under a prompt demanding "banana"; invalid schema → 400 | **unsupported**: 400, server lacks `--tool-call-parser` | returned |
| `qwen3.7-plus` (Siraya) | constrains a valid schema; **an invalid schema is silently ignored** (200, "banana") | forced, but **arguments not schema-checked** (`{"intent": "banana"}`) | returned |
| `qwen-turbo` (Siraya) | **404 "no vendors found"** | 404 | 404 |

### d) `llm_step` deadline on the in-house gateway

**Setup:**
- The callbot's real `REMINDER` classifier prompt (19.6k characters, 20 intents) and 10 utterances.
- 200 calls per arm, the arms alternated call by call.
- **Control:** today's `LLMOp.of(fields=["intent: str"], parser="json", max_retries=1)`.

| arm | p50 | p95 | max | outcome |
|---|---|---|---|---|
| control `LLMOp` | 103 ms | 120 ms | 165 ms | 200 ok |
| `llm_step` prototype, deadline 0.9 s | 102 ms | 120 ms | 127 ms | 200 ok, 0% fallback, 200/200 same intent as control |
| `llm_step`, deadline 0.05 s (must cut) | 52 ms | 53 ms | 54 ms | 30/30 timeout, late by p50 2.4 ms, max 4.4 ms |

**Gates:**
- p95 within 50 ms of control: passes (−0.6 ms).
- Deadline honoured within 20 ms: passes (4.4 ms).

## 3. What the evidence settles in the design

1. **Dispatch calls the tool's function directly** (K0, now). Tracing comes from K1 child executions,
   not from an op object built per call.
2. **K1 is `child()`.** It is an async context manager, and a child's parent is derived from its ctx
   and name with no stored link. `invoke(op_factory, **inputs)` is sugar over `child()` in
   `operonx-agents`, not core API.
   - **A child of a generator op hangs under the yield record current when it opens.** The spike
     hung it under the op's base ctx, and for a non-transient generator no record exists there. A1
     must test this.
3. **Structured output is declared per resource, never guessed.**
   - `llm:` resources take `structured_output: native | tool | prompted`, defaulting to `prompted`.
     Gateways silently ignore what they do not support (c), so a guessed default could give an
     unconstrained answer that looks constrained.
   - Every strategy validates the answer with pydantic and re-asks on failure. `qwen3.7-plus` proved
     that a forced tool's arguments are not enforced.
   - The A0 probe ships as `operonx-agents probe <resource>`, which prints what to declare.
   - `inhouse` declares `native`. `qwen3.7-plus` declares `tool`.
4. **`Choice` confidence comes from logprobs.** Both live endpoints return them. The invented 0.85/0.5
   in `merge_intent` goes in A6.
5. **The callbot gets no model fallback.**
   - `qwen-turbo` is dead on its gateway.
   - `qwen3.7-plus` took 1.5 s or more on every probe request, which is past the 0.9 s deadline
     that covers the whole fallback chain.
   - So the degrade path is `on_timeout`/`on_invalid` → `"fallback"`. `Model.fallback` stays for
     agents with looser budgets.
   - The callbot's `qwen-turbo` resource should be repointed or removed. That is flagged to the
     callbot, not fixed here.
6. **Deadlines use `asyncio.timeout`**, with a backport on Python 3.10 (operonx's floor). It cut 30/30
   calls within 4.4 ms.
7. **Durable approvals do not wait for R3.** An explicit loop resumes from its own `RunState`
   (track3 §4.2), so A4 needs A3's `StateStore`, not the scheduler journal. R3 is needed only to
   resume a graph that contains an agent op mid-run.

## 4. Phases

These are one branch per phase in the repo named, with commits batched. The phase merges when its
gate is recorded. Every phase includes at least one run against a real model:
- `inhouse` for steps;
- `qwen3.7-plus` for tool-calling agents (the in-house gateway cannot tool-call, per §2c).

| Phase | Wave | Repo | Deliverable | Gate |
|---|---|---|---|---|
| A0 | W1 | spike | Evidence (§2) and this plan | **Done**; D1 awaits your decision |
| A1 | W2 | operonx | K0 dispatch fix; K1 `child()`; K2 `attrs`; K3, K4 and K6 via R1 and R2; K5 check | Tests below; spike re-run on the release: in-op + children < 2 ms/turn at 5 concurrent, tree 15 rows / 1 root |
| A2 | W2–3 | operonx-agents (new) + operonx K7 | Tools, `Model`, output strategies, `llm_step`, `probe` | Callbot shadow replay of 200 recorded turns: ≥ 99% intent agreement, p95 no worse |
| A3 | W3 | operonx-agents | `Runner`, `RunState`, `UsageLimits`, events, sessions, compaction | SIGKILL between in-flight and finished: an idempotent tool re-runs, a non-idempotent one gets "outcome unknown"; cancel mid-turn writes nothing; < 2 ms/turn at 5 concurrent through `Runner` |
| A4 | W3–4 | operonx-agents | Approvals as interruptions, `as_tool`/`as_op`, hooks, redaction, MCP (stdio + streamable HTTP) | An approval survives a process restart; a model naming another agent's tool gets "unknown tool" |
| A5 | W4 | operonx-agents + studio | `agent_service`, `/resume`, trajectory evals, studio agent view, `operonx.agents` shims | HTTP and WS end to end with an approval round-trip; Workflow view screenshots (§2b) |
| A6 | W4 | callbot `refactor/operonx-studio` only | `llm_classify` → `llm_step` | Shadow diff and a 5-CCU load test, TTFA p50 ≤ 0.86 s |

### A1: core prerequisites (one operonx minor release)

**K0 (dispatch).** `execute` calls `factory.__wrapped__`.
- Test: a spy on `FuncOp.__init__` sees zero constructions across a 3-call turn.
- Test: the dispatch-only benchmark stays under 0.2 ms per call.

**K1 (child executions).** Tests:
- nesting: agent → turn → model/tool;
- order and inputs/outputs;
- error recorded and re-raised;
- cancel recorded as `cancelled`;
- untraced is a no-op;
- names with `.` or `[` rejected;
- a child inside a generator op hangs under its yield record;
- a child inside a `.parallel()` fan-out gets the right ctx;
- Local, Langfuse and ClickHouse round-trip the records, and studio's tree API returns them nested;
- `exclude=` is honoured on child inputs.

**K2 (`OpExecution.attrs`).** It holds the GenAI attribute names (`gen_ai.operation.name`,
`gen_ai.tool.name`, `gen_ai.usage.*`).
- Test: all three consumers round-trip `attrs`.

**K3 / K4 / K6.** These ship as R1 and R2 (`@op(timeout=)`, `errors="raise"`, the C2/C3 fixes),
with track3 §5 Phase 1's tests. The agent track adds one test: a timed-out op emits its
`on_timeout` outputs and its downstream runs.

**K5.** Cancel while a reducer write is pending: no write reaches a checkpointer.

### A2: Tools, Model, `llm_step`

**Tools:** `@tool` with schema and validation from the signature, `ToolSpec`, per-agent
`Toolset`, dispatch, `ToolPolicy` (ported).

**Model:**
- `Model` over `ResourceHub` `llm:` with fallback, a deadline over the whole chain, and normalised
  `Usage`.
- Output strategies per §3.3, with `structured_output:` read from resources.
- K7 in operonx: one tool-call shape, a native `json_schema` passthrough, per-resource timeout,
  logprobs.

**`llm_step`:** an op factory with `Choice(from_input=)`, logprobs confidence, and
`on_timeout`/`on_invalid`. Plus `operonx-agents probe`.

Tests:
- Schema generation across 25 signature shapes, accepted live by OpenAI- and Anthropic-shaped
  validators once.
- An argument error becomes one tool message naming the field. `ModelRetry` round-trips.
  Sequential tools never overlap (recorded timestamps).
- `Model`: fallback on 5xx and on refusal, none after the first delta, the deadline covers the
  chain, `Usage` normalised from recorded fixtures.
- Each strategy validates the same pydantic model and re-asks at most `output_retries` times.
  A `tool`-mode answer with an out-of-enum argument is caught (the `qwen3.7-plus` case).
- `llm_step`: an out-of-set label → `on_invalid`. A slow fake model → `on_timeout` within 20 ms of
  the deadline (the A0 cut measured 4.4 ms).
- **Live:**
  - `inhouse` native `json_schema` returns an in-enum answer under an adversarial prompt.
  - `probe` reproduces the §2c table.
  - Callbot shadow replay of 200 recorded turns from ClickHouse through `llm_step` vs `LLMOp`:
    ≥ 99% agreement, p95 no worse. A0's 200/200 used synthetic utterances; the gate uses
    recorded ones.

### A3: Runner, RunState, limits, events, sessions

Port the loop invariants from `test_react.py` and `test_session.py`. Then:
- Each of the seven limits trips, and child usage counts toward the parent.
- **SIGKILL crash recovery** (subprocess) between "in flight persisted" and "tool finished".
- Cancel mid-turn leaves the session and the store untouched.
- The event stream is complete and ordered (property test), and `stream()` equals `run()`.
- Compaction triggers on real `usage` and its summary persists.
- **Overhead gate:** `bench_overhead` re-pointed at `Runner` holds < 2 ms/turn at 5 concurrent with
  tracing on.
- **Live:** a 3-tool agent on `qwen3.7-plus`, and a 20-turn Redis session.

### A4: Approvals, composition, safety, MCP

- An approval survives a restart: persist, new process, `Runner.resume`.
- Deny, expiry, deny ≠ ask, and argument-dependent approval each behave correctly.
- A child agent's approval surfaces on the parent with a path, and resuming completes both.
- **Isolation regression:** a tool owned by another agent in the process answers "unknown tool".
- A hook tripwire ends the run `blocked`, and a `before_tool` replacement is honoured.
- MCP: the 33 stdio tests ported, plus streamable HTTP against the reference server.
- `as_op` events reach `engine.stream(mode="custom")`.

### A5: Serve, Studio, evals, migration

- `agent_service` (SSE/WS, `/resume` via K8).
- Trajectory evaluators and `dataset_from_runs`.
- `operonx init --template agent` on the new API.
- A guide page with tested snippets.
- Shims for `operonx.agents`.

**Studio:**
- `graphForRun` counts root records only.
- The canvas expands an agent op into its turn → model/tool children.
- Child-row icons come from `op_type`.
- Tool-call arguments render for the flat shape.

**Gate:** HTTP and WS end to end with an approval round-trip, and a 20-case eval in CI. Plus
screenshots, desktop 1600×1000 and phone 390×844, of an agent run where:
- the Tree view shows agent → turn → model/tool with inputs and outputs;
- the Workflow view paints the agent node and opens it to the same structure.

### A6: Callbot adoption (`refactor/operonx-studio` only)

**The change:** `llm_classify` becomes
`llm_step(model=Model("inhouse", deadline=0.9), output=Choice(from_input="allowed_intents"),
on_timeout="fallback", on_invalid="fallback")`.
- It uses native `json_schema` and logprobs confidence, with no model fallback (§3.5).
- `merge_intent` drops its allow-list and its invented confidence.
- Redaction is added for logs and traces.

**Gate:** the A2 shadow diff, plus a 5-CCU load test with TTFA p50 ≤ 0.86 s
(`docs/CAPACITY_2026-09-29.md`).
