# Agent plan — audit archive

Historical record from the operonx agent-framework build (Aug 2026).
Everything here is **resolved**: the defects are fixed and described in
the [CHANGELOG](https://github.com/batman1m2001-cyber/operonx/blob/main/CHANGELOG.md),
and the durable lessons are distilled in
[Architecture → Failure modes](../architecture/failure-modes.md).

Kept because the reasoning is worth more than the conclusions — in
particular §16.2, the three high-severity findings that a probe
**disproved**. Read this when you want the evidence trail; read
`failure-modes.md` when you want the rule.

The live plan is `AGENT_EXTENSION_PLAN.md` at the repository root.

---

## 14 · Cross-implementation audit (11 Aug 2026)

Read `NousResearch/hermes-agent`, `langchain-ai/langgraph` and
`langchain-ai/langchain` at HEAD and measured them against this plan.
Two conclusions: **the skeleton is confirmed correct**, and **the plan
mistakes the loop for the work**.

### 14.1 · Sizes

| Codebase | Agent layer | Shape |
|---|---|---|
| `langgraph/libs/prebuilt` | **3,676 LOC** | `tool_node.py` 2,030 · `chat_agent_executor.py` 1,015 |
| `langchain/libs/langchain_v1/langchain/agents` | **13,702 LOC** | `middleware/` ~9,400 · `factory.py` 2,062 |
| `hermes-agent/agent` | **136,329 LOC** (~140 modules) | `conversation_loop.py` 7,757 · `run_agent.py` 8,303 |

Our estimate (~13 files, most <200 LOC) is the right order of magnitude
against langgraph's prebuilt layer. Hermes is a hosted product, not a
framework — ~45 of its modules are provider adapters, credential pools
and billing views that `ResourceHub` + `LLMOp` already own or that are
correctly out of scope.

**Hermes is not a structural teacher.** `run_conversation` is a single
**6,335-line function** — zero nested defs, 35 loops, 46 `try` blocks,
and the module exports exactly one symbol. `AIAgent` carries **260
methods**. §11's "biggest reject" is empirically confirmed. Its value
here is as a *checklist of concerns*, and those live in its small
modules, never in the loop.

### 14.2 · The one structural hole — `wrap_*` has nowhere to live

LangChain v1 exposes six middleware hooks, and they compile in two
fundamentally different ways:

| Hook | Compiles to | We have it? |
|---|---|---|
| `before_agent` · `before_model` · `after_model` · `after_agent` | A real graph node — `graph.add_node(f"{m.name}.before_model", …)` | **Yes, free.** That is just an op in the graph |
| `wrap_model_call` · `wrap_tool_call` | A **Python closure chain folded inside one node** — `_chain_model_call_handlers` / `_chain_tool_call_wrappers`, composed right-to-left by `compose_two` | **No. Nothing in this plan corresponds to it** |

The compiled LangChain agent graph is `model` + `tools` plus one node per
state-hook middleware. The wrappers are invisible to the graph.

They must be. A `wrap_*` handler receives a `handler` continuation it may
call **zero times** (cache hit, short-circuit), **once** (passthrough), or
**N times** (retry with a modified request):

```python
def wrap_tool_call(self, request, handler):
    for attempt in range(3):
        result = handler(request)           # same step, re-invoked
        if result.status != "error":
            return result
    return result
```

**A DAG edge cannot express that.** Back-edge loops give us *iteration*
across turns; they do not give *re-invocation with modified input inside
a single step*. Retry-around-a-call, response caching, and short-circuit
are all this shape.

**Resolution — rung 1, not core.** Compose wrappers as a plain Python
fold inside `exec_tool` and the LLM-call op. No operonx change, no new
primitive, no graph feature. This is precisely what LangChain does, and
it is the same conclusion the Footprint Ladder reaches independently:
start at rung 1 and climb only when it provably fails. It does not fail
here.

Design note for P1: the fold belongs in `operonx/agents/graphs/dispatch.py`
as a `wrappers: list[Callable]` parameter threaded to `exec_tool`, not as
an `@op` argument — wrappers are control flow, and control flow that the
tracer sees as data flow produces spans that lie about what ran.

### 14.3 · Gaps confirmed by an independent implementation

Every gap found by reading `ToolNode` also has a shipped LangChain
middleware behind it. These are not speculation.

| Missing from this plan | Their implementation | Consequence if skipped |
|---|---|---|
| **Tool errors must become tool messages** | `tool_error.py` + `tool_retry.py` (612 LOC); langgraph `handle_tool_errors` | `exec_tool` has no error path. **The first tool that raises kills the run and the model never learns.** Highest-severity item in P1 |
| **Unknown tool name** | `_validate_tool_call` → `ToolMessage(status="error")` listing valid names | `TOOL_REGISTRY[name]` raises `KeyError` in both `parse_call` and `exec_tool` |
| **Every `tool_call` needs a matching tool message** | `_validate_chat_history` | Most providers 400 on an unmatched call. `blocked_result` covers denial; crash, timeout and cancel are uncovered |
| ~~**Concurrent HITL**~~ 🔴 **WRONG — see §15.1 V4** | `human_in_the_loop.py` (500 LOC); hermes `_ConcurrentToolAuthorizationGate` | ~~§7.2 fans out `.parallel(max=10)` and each arm can raise its own `InterruptOp` — ten simultaneous approval prompts~~ Probed: the pump serialises, one event outstanding at a time. **No gate needed.** |
| **Repeat / loop cap** | `tool_call_limit.py` (495); hermes `ToolCallSignature` + `LoopCapConfig` | Model calls the same tool with identical args forever, inside `max_iterations` |
| **State injection into tools** | `InjectedState` / `InjectedStore` / `ToolRuntime` | A tool needing session context has no channel that isn't also in the LLM schema |
| **Iteration budget** | `model_call_limit.py` (267) | Already logged as gap #6 in §10 |
| **Redaction / PII** | `pii.py` + `_redaction.py` (1,332) | Nothing in this plan. Tool output flows to the model and the tracer unfiltered |
| **Context compaction depth** | `summarization.py` + `context_editing.py` (1,208); hermes `context_compressor.py` (7,386) | P2 budgets one `compact_messages` op for what two teams treat as a subsystem |

### 14.4 · What changes in the plan

1. **Fold rows 1–6 of §14.3 into P1.** They are not polish. Without the
   first three, the first tool exception ends the run — the most common
   thing that happens to a real agent.
2. **Resolve the branch-convergence question before writing
   `dispatch.py`.** §7.2 instantiates `exec_tool` and `collect_result`
   twice, once per branch arm, because it is not established that two
   arms can converge on one node. No test in
   `tests/internal/core/ops/flow/` covers it. If they cannot converge,
   the dispatch subgraph's shape changes and the duplication grows with
   every branch.
3. **P2 grows.** Compaction is a subsystem, not an op.
4. **Add redaction to P3**, next to the permission policy modes — it is
   the same trust boundary.
5. **Leave the loop alone.** Both implementations confirm §5 and §7.3.
   The ReAct loop really is ~35 lines on our primitives; the reason
   theirs are bigger is scaffolding we genuinely do not need.

### 14.5 · One decision worth revisiting

`create_react_agent` is **deprecated in langgraph v1.0** and moved to
`langchain.agents`, along with `HumanInterrupt` and `ActionRequest`. The
team that shipped this at scale concluded the agent layer does not belong
in the graph-runtime package.

This plan puts `operonx/agents/` in-tree. That is not obviously wrong —
we have no equivalent of the langchain/langgraph split, and P4's
`operonx-code` harness is already out of tree. But it is a reversal by
the closest prior art, and worth an explicit answer in the P1 ADR rather
than an assumption.

---

## 15 · Verification ledger

**Why this exists.** §2 lists fifteen primitives as "the substrate —
everything resolves back to one of these." It was written from docstrings
and design docs, not from running code. Two rows have since been probed
and **one was wrong**: `interrupt_op.py`'s own docstring says engine
integration is *future* Phase 2b3 work, and §2 wrote it up as shipped.

Two out of two proves nothing statistically. It does establish that a
docstring is not evidence. So: **before you build on a §2 row, probe it.**
Not all of them up front — that trades a week for certainty we do not
need yet. One at a time, when the phase that needs it comes up.

Convention: 🔴 confirmed wrong · 🟡 unverified · 🟢 verified by a probe
that lives in `tests/`.

### 15.1 · Confirmed issues — fix when the owning phase starts

| # | § | Issue | Owner phase |
|---|---|---|---|
| V1 | §7.2 | ✅ **resolved in P1.** 🔴 Duplicates `exec_tool` + `collect_result` per branch arm. Unnecessary — arms **can** converge on one node by writing `PARENT` cells (`a["raw"] >> PARENT["picked"]`, `collect(raw=PARENT["picked"])`). Direct `collect(raw=a["raw"])` genuinely fails, so the sketch's caution was right; the conclusion was not. **Dispatch gets simpler, not more complex.** Built that way: both approval arms write `PARENT["decision"]` and one `execute` reads it. | P1 |
| V2 | §7.2 | ✅ **resolved in P1.** 🔴 `InterruptOp(payload={"tool": …, "args": …})` — a Ref nested in a dict was silently unresolved, so the human approved blind. Now raises (`_params._reject_nested_ref`). Until nested-ref support lands, build the payload in an upstream `@op` and pass one bare Ref — `parse_call` does. | P1 |
| V3 | §2 · §7.2 | 🔴 `engine.stream()` does **not** auto-subscribe to the interrupt bus and `handle.resume(id, value)` does **not** exist. `bind_interrupt_bus` has zero callers outside its own export. Working path today: `handle = engine.start(...)`, `bind_interrupt_bus(handle.state, sink)`, `handle.state.resume_interrupt(iid, value)` — verified end to end. Wiring it properly is a contained `engine.py` change. | P1 |
| V4 | §14.3 | 🔴 **My error.** The "concurrent HITL → N simultaneous approval prompts, needs a serialising gate" row is wrong. The pump serialises: exactly one `InterruptEvent` outstanding at a time, 2.76s wall for 3 calls. No gate needed. Delete the row when §14.3 is next edited. | — |
| V5 | new | ✅ **resolved in P1.** 🔴 **Errors never leave `Operon.run()`.** `GraphOp._process` catches everything, logs it, writes `state[full_name, "error", ctx]`, and returns a partial result. Intended (`ex08_error_handling`: "workflow doesn't crash"), but it means a raising tool produces no output, the downstream collector *also* fails, no tool message is emitted, and the provider 400s on the next turn. **`exec_tool` must catch every exception itself and always return a tool message.** Two silent failures stack otherwise. `execute` now catches everything and always returns a tool message. | P1 |
| V6 | §5 | ✅ **done — and it did not compile.** §7.3 used `@graph(max_iterations=…)`, which does not exist; the kwarg was removed with the 1.0.0 retry sugar. Four further defects came out of running it: V7–V10 below. | P1 |
| V7 | §7.3 | 🔴 **No turn cap exists at graph level.** The synthesized loop is pinned to 1000 at `cycle_rewrite.py:402`. Deliberately **not** plumbed through `@graph`: a graph-level cap cuts mid-flight, tells the model nothing and leaves partial state. `count_turn` in the agent layer injects a notice at the limit and lets the model take one final turn, so exhaustion exits the way success does. | P1 ✅ |
| V8 | core | ✅ **fixed.** A back-edge source below a generator fan-out never fired, so any loop of the ReAct shape stopped after one iteration — silently. `end_time` lands at `('main','[0]','__collect__')`, never at the iteration ctx, and the spec (`STATE_LOOP_REFACTOR_PLAN.md:518`) required an exact match. Termination now accepts any ctx below the iteration's. 14 regression tests; none of the 32 prior cycle-rewrite tests put a generator in a loop. | P1 ✅ |
| V9 | §7.3 | 🔴 **An op wired after a generator-containing loop never runs.** The loop emits at item contexts, so the downstream op never reaches ready and is skipped with no error. Kills the obvious "terminal `finish` op reads the cells and emits them" design. `agent_result(result, agent)` reads the cells directly instead, which is why it needs the graph. | P1 ✅ |
| V12 | new | 🔴 **`LLMOp` templates its prompt.** `_format_value` runs `format_map` over any string containing `{`, so a tool returning `{"city": "Hanoi"}` makes the *next* model call raise `PromptError: Missing template variable(s)`. Any JSON, code or CSS the agent reads does the same. `prepare_prompt` doubles braces; `format_map` collapses them back. No opt-out exists in core — worth one (C6). **Resolved:** `messages=` is now a separate input that is never formatted; `prompt=` no longer accepts a list. | P1 ✅ |
| V13 | new | 🔴 **A subgraph emits `None` for outputs a frame did not write.** Straight into an `add_messages` cell that raises, and since op errors go to state rather than propagating, the run ends quietly mid-conversation. `normalize_messages` absorbs it. | P1 ✅ |
| V14 | new | 🔴 **`add_messages` upserts on id, so a stable id silently overwrites history.** Every assistant turn stamped `assistant-0` meant each turn replaced the last: the tool-calling turn vanished, its tool messages answered nothing, and the conversation came back `user → tool → assistant` — a *plausible* result, which is why it survived. Fresh uuid per turn. | P1 ✅ |
| V15 | new | 🟡 **`LLMOp` prepends `llm:` to the resource key**, so `resource="llm:qwen"` resolves as `llm:llm:qwen`. Documented on `make_llm_caller`; arguably the error message should say so. | — |
| V11 | §2 | 🔴 **Checkpointer API mis-stated.** Binding is `engine.start(inputs, checkpointer=cp)` — `Operon(g, checkpointer=)` raises `TypeError`. `get_state(step)` and `list_steps()` are methods on the **checkpointer**, not on `ExecutionHandle`, which has neither. The primitive itself works: 6 steps recorded for a 3-iteration loop, each a per-step cell snapshot. `AgentSession` uses the real API. | P2 ✅ |
| V10 | §7.2 | 🔴 **`collect()` behind `parallel()` inside a loop invokes per item**, not per batch — measured `[[0],[2],[0],[2]]` for 2 items × 2 iterations. Consumers must tolerate a partial batch. Harmless when the target cell has a reducer, since each write merges; `gather_tool_messages` handles both shapes. | P1 ✅ |

### 15.2 · §2 rows — verification status

Probe the row, then flip its light and link the test.

| § 2 row | Status | Needed by |
|---|---|---|
| **HITL suspend/resume** (`InterruptOp`) | 🔴 partly wrong — see V3 | P1 |
| Tool fan-out (generator op + `Ref.parallel(max=N)`) | 🟢 `test_loop_generator_backedge.py` — but see V8/V10 | P1 |
| Ordered gather (`Ref.collect()` — yield-index order) | 🔴 **per-item inside a loop, not per batch** — V10 | P1 |
| Turn loop (back-edge → synthesized `_GraphLoop`; termination when no back-edge source fired) | 🟢 after the V8 fix; was broken for any loop containing a generator | P1 |
| Shared cells + reducers (`PARENT.declare(reducers=)`) | 🟢 verified — the cell merges correctly; `run()`'s output dict does not (V6 F2) | P1 |
| Message accumulation (`add_messages` id-upsert, `RemoveMessage`) | 🟢 id-upsert verified end to end; **raises on a non-list**, which is why `gather_tool_messages` exists | P1 |
| Structured LLM output (`LLMOp.of(fields=, validators=, max_retries=)`) | 🟢 after §16 F6/F7 — a missing field is now an error, so `max_retries` fires; a lone XML root is descended into. **The agent layer does not use `fields=`** | — |
| Refusal vs parse failure (`_is_refusal` → `fallback=`) | 🟡 unverified — the agent layer does not rely on it | — |
| LLM streaming (`stream=True` per-token frames) | 🟢 after three fixes — tool-call deltas are merged (F1), `stream(mode="updates")` is live for intermediate ops (F5), and the closing frame is marked `final=True` so joining deltas no longer double-counts (F8). Handle frames remain outputs-only, by design and now documented | P4 |
| Custom progress events (`EmitOp` + `stream(mode="custom", channels=)`) | 🟡 | P2 |
| Cross-run persistence (`Checkpointer`) | 🟢 works — but §2 named the wrong binding site and the wrong accessors (V11). `AgentSession` uses the real API | P2 ✅ |
| Observability shaping (`@op(exclude=, include=, observe_max=)`) | 🟢 after §16 F3/F4 — `exclude=`/`include=` now filter the V3 trace, and `observe_max` is enforced on every run. The agent layer still uses its own `Redactor`, which is scoped to tool output rather than op vars | — |
| Per-run scratchpad (`SCRATCH[key]` through the observer bus) | 🟡 | P2 |
| Sub-agent isolation (nested `@graph`, hermetic parent refs, nested spans) | 🟡 | P3 |
| Preemptive cancel (`yield Interrupt(ctx_to_cancel=…)`) | 🟢 after §16 F2 — a bare `Interrupt()` now cancels the emitter's branch; the whole run needs `Interrupt.ALL` | — |
| Async I/O dispatch (`@op(bound=)`) | 🟢 — exercised throughout the existing suite | — |
| Config + secrets (`ResourceHub`) | 🟢 — exercised throughout the existing suite | — |

### 15.3 · Core work surfaced, not scheduled

| # | Item | Status |
|---|---|---|
| C1 | Reject Refs nested in containers | **done** — `_params._find_nested_ref`, PR #29 |
| C2 | *Support* nested Refs (hoist each buried ref into its own cell, reassemble at read time). Needs a decision first: teach operonx-rs the new `serialize()` form, or refuse to serialize graphs using it as synthetic loops already do. | open — blocks nothing in P1 given V2's workaround |
| C3 | Wire `engine.stream()` + `handle.resume()` to the interrupt bus (V3) | open — ~½ day, contained to `engine.py` |
| C4 | Loop termination below a generator fan-out (V8) | **done** — `task_scheduler._ctxs_within` |
| C5 | An op after a generator-containing loop never becomes ready (V9). Worked around in the agent layer; the underlying readiness rule is untouched. | open — no owner |
| C6 | No way to pass a message list to `LLMOp` without prompt templating (V12). Every agent must escape braces defensively. | **Closed** — `messages=` (never formatted) split from `prompt=` (a template; no longer takes a list). A `template=False` flag was built and rejected: it can express `template=False` *with* template variables, a meaningless state needing a runtime check. `messages=` makes that unrepresentable. `_escape_braces` and `prepare_prompt` deleted. |

### 15.4 · Scripted doubles verify code, not contracts

Every unit test in `tests/internal/agents/` scripts the model. That
verified the loop, dispatch, budget, policy and approval — and missed
**four consecutive bugs** at the `LLMOp` seam (V12–V14 plus a build
failure), because a scripted double reproduces the shape you assumed
rather than the one the dependency has.

`test_live_agent.py` is the answer for the model seam. The same gap
applies to every 🟡 row in §15.2 that is currently exercised only through
a stub.

### 15.5 · The rule

A row stays 🟡 until a **committed test** exercises it — not a scratch
probe, not a docstring, not this plan. When a phase starts, promote only
the rows that phase needs. Anything found wrong gets a line in §15.1 and
a fix in the same PR that needed it.

---

## 16 · Core findings from adversarial review (11 Aug 2026)

Four agents were asked to find silent failures in the primitives §2
rests on. Their claims were then **verified empirically rather than
believed** — three of the highest-severity ones turned out to be wrong,
which is the reason for this section's structure. Nothing below is
reported unless a probe reproduced it.

### 16.1 · Confirmed

| # | Finding | Impact |
|---|---|---|
| F1 | **Streamed tool calls were appended, not assembled.** One call arrived as N fragments, each holding a slice of JSON that parses as nothing. Streaming worked, tool calling worked, only the combination broke. | **Fixed** — `LLMOp._merge_tool_call_deltas`, 13 tests |
| F2 | **`Interrupt()` with no `ctx_to_cancel` cancels the entire run and returns cleanly.** The default `()` makes `_is_descendant_or_equal` true for every ctx. Outputs come back as `{"__interrupt__": …}` with no error — a one-word user mistake silently loses the run | **Fixed** — default is `Interrupt.SELF`, resolved by the scheduler to the emitter's ctx; whole-run cancel is `Interrupt.ALL`. 8 tests |
| F3 | **`@op(exclude=…)` does not filter the V3 trace.** A var excluded from observation still appears in `handle.trace`. `base.py`'s own comment claims "Checkpointer + Tracer both respect these"; only the checkpoint/custom/interrupt buses consult it. Security-relevant — it is the documented way to keep a secret out of the trace | **Fixed** — `BaseOp._filter_for_trace` on both the per-yield and batch records; `should_emit_for_channel` moved to core. 9 tests |
| F4 | **`observe_max` is a no-op unless a checkpointer is bound.** The counter lives in `bind_checkpointer`'s closure, which `engine.start` only creates when `checkpointer is not None`. A runaway generator under `engine.run()` is uncapped — 50 frames emitted against a budget of 5 | **Fixed** — `bind_observe_budget`, bound on every run, binds nothing when no op declares a budget. 8 tests |
| F5 | **Streaming frames are dropped unless the op is PARENT/END-bound.** Measured: 3 frames when bound, **0** when the same streaming op feeds a downstream consumer — the normal agent shape. §2's "frames forwarded to `ExecutionHandle._queue`" is conditional on graph topology, not on `stream=True` | **Fixed, and split in two** — see §16.1a. 6 tests |
| F6 | **`parser="xml"` returns `None` with `error=None` for a wrapped element.** `<r><result>X</result></r>` with `fields=["result: str"]` yields nothing, and because `error` is `None`, `max_retries` never fires. `parsing.py`'s own docstring example is wrong. Repeated sibling tags likewise collapse to `None` | **Fixed** — a lone dict root is descended into (XML only); repeated leaves build a list. 8 tests |
| F7 | **Valid JSON with the wrong keys parses "successfully".** `{"bad": 1}` against `fields=["result: str"]` returns `{"result": None, "error": None}` — no retry, no error. Structured mode cannot tell "model answered wrongly" from "model answered" | **Fixed** — absent ⇒ error naming the field and the keys present; explicit null is still an answer; a validator `@default` still applies. 6 tests |
| F8 | **The final streaming frame repeats the full accumulated `content`.** `_stream_final` re-emits the whole text through the same frame path as the per-token deltas, so a consumer joining frames double-counts. Only the incidental presence of `finish_reason` distinguishes them | **Fixed** — deltas are `final=False`, the closing frame `final=True`. Verified live. 2 tests |

### 16.1a · F5 was two defects wearing one label

The finding said "streaming frames are dropped". Probing it split the
claim in half, and only one half was a bug:

- **`handle` frames are the graph's outputs, by design.** `result()` and
  `collect()` are built from the same frames, so forwarding every
  intermediate op's yields would put every internal var into the result.
  Left as-is and **documented** on `ExecutionHandle.__anext__` and
  `stream(mode="frames")`, which promised "operonx frames" and said
  nothing about the restriction.
- **`stream(mode="updates")` did see every op — and was not live.** It
  was paced by `async for _ in handle`, so it only advanced when an
  *output* frame arrived. Measured: four generator yields 150ms apart,
  all released together at the end. A graph that streams a model into a
  consumer emits its single output last, which is precisely the shape
  that broke. Pacing now comes from the write bus: the same four yields
  arrive at 185/336/487/640ms.

The reason this matters for P4 is the second half, not the first. A
coding agent doesn't need intermediate ops in `result()`; it needs the
tokens while they are being produced.

### 16.1b · Confirmed in the agent layer — all fixed

These were **my** bugs, found by adversarial review rather than by the
tests written alongside the code. The shape they share: the tests
exercised *one* tool call, or answered *every* approval the same way, so
a per-call boundary that did not exist looked like one that did.

| # | Bug | Why the tests missed it |
|---|---|---|
| A1 | **A human-denied destructive tool executed anyway.** The approval decision travelled through a `PARENT` cell, which is shared across contexts by definition — with calls fanned out, the last arm to answer overwrote every sibling. Denying one and approving another ran **both**. Now the two arms are separate inputs to `execute` | every test answered all approvals identically |
| A2 | **Sub-agent `allow_tools` was never enforced** — privilege escalation. The allowed names were computed and used only for an empty check; the child resolved against the global registry and ran tools its parent had excluded. Now compiled into a default-deny `ToolPolicy` | the test asserted the *helper* reported the right names, never that a child was restricted |
| A3 | **Tool exception text bypassed the redactor** — a stack trace with a connection string went to the model and the tracer verbatim, the exact case redaction exists for | scrubbing was only asserted on the success path |
| A4 | **Truncation ran before redaction**, cutting the END marker off a PEM block so the pattern no longer matched and key material shipped | no test combined a secret with a truncation limit |
| A5 | **A synchronous `@tool` failed every call** with "object dict can't be used in 'await'" | every fixture was `async def` |
| A6 | **A failed turn was reported as the previous turn's answer.** operonx returns a partial result rather than raising, so `session.send()` committed a history ending on an unanswered user turn and returned a stale `final` | no test made a model call fail mid-session |
| A7 | **Compaction declined to act while 114× over budget.** One oversized exchange inside the keep window means nothing is "older". A test asserted this behaviour and **locked the bug in** | the test used a small conversation, where the assertion looks right |

### 16.1c · The one that was not a bug but a gap

`plan_compaction`, `assemble_api_messages`, `apply_cache_control`,
`inject_skills` and `merge_memory` were built, tested, exported — and
**wired into nothing**. `build_react_agent` never called them. A
deployment grew context until the provider rejected it, with a
fully-tested compactor sitting unused and zero prompt-cache benefit.

Every unit test on those modules passed, because each piece worked. The
gap was only visible by asking what the model actually *receives*, which
is what `test_context_wiring.py` now does.

They are now a stage inside the loop: `count_turn → last_user → plan →
apply → memory → skills → assemble → cache_control → call_model`.

Two decisions worth recording:

- **Compaction shapes the prompt, not the stored conversation.** The
  history stays whole, so nothing is lost irrecoverably and
  `agent_result` still returns everything that happened. The cost is
  re-planning each turn, which is computation over a list.
- **Memory is gathered in one op rather than the §7.5 fan-out.**
  `collect()` behind `parallel()` *inside a loop* invokes its consumer
  per item with a partial batch (V10), and the assembler needs one
  merged context per turn — a fan-out would silently hand the model a
  fraction of its memory. `gather_memory` documents this; the fan-out
  ops remain right outside a loop.

### 16.2 · Refuted — reported by an agent, disproved by probe

Recorded because the temptation to act on a plausible report is the point.

| Claim | Reality |
|---|---|
| Shared-cell defaults bleed across runs | **No.** Two runs of the same engine each ended with one message. `add_messages` returns a new list rather than mutating `old` — verified directly |
| A nested subgraph's `PARENT.declare()` makes a separate cell, so a sub-agent's writes never reach the parent | **No.** The outer cell held both the parent's and the child's message |
| `add_messages` mutates its `old` argument | **No.** `old` was unchanged after merge |

### 16.3 · Scope

**All eight are fixed** (12 Aug 2026). F1 went first because it broke the
agent layer directly; F2–F8 were recorded rather than fixed silently
because several change published behaviour, and were then done as one
pass with the decisions written into the CHANGELOG.

Three of them changed a contract, so they are worth knowing about before
upgrading:

| | What a caller may notice |
|---|---|
| F2 | `Interrupt()` with no target now cancels the emitter's branch, not the run. Nothing in-tree relied on the old default — every call site already passed `ctx_to_cancel` |
| F7 | `fields=[…]` reports a missing field as an error where it used to return `None` silently. **A union schema must mark its optional entries `"name?: type"`** or every call fails — see §16.4 |
| F8 | Streamed `LLMOp` frames carry `final`. Additive, but a consumer asserting exact frame dicts will see the new key |

Two tests asserted the old behaviour and had to be rewritten rather than
kept passing — `test_missing_field_becomes_none` (F7) and the
`bind_checkpointer`-bound budget tests (F4). That is the same shape as
A7: a test can hold a bug in place, and passing is not evidence of
correctness when the assertion was written from the implementation.

Verification: 1593 unit tests green in both fixed and random order, ruff
and `mkdocs --strict` clean, and the 5 live tests re-run against
`qwen3.7-plus`. F8 was additionally confirmed against a real streaming
response — the deltas join to exactly the final frame's content, and a
naive join double-counts.

### 16.4 · What the follow-up review caught

Fixing the eight was not the end of it. Re-reading the diff against real
callers found two things the regression tests could not, because both
tests and code were written from the same assumption.

**F7 would have broken callbot's `ahamove_hr` agent on every call.** Its
extractor declares twelve fields and its own comment says non-compound
states emit only `intent` — a *union schema*, where absence is the normal
case. Requiring every declared field turned each turn into a semantic
failure plus `max_retries` retries plus an all-`None` result. Fixed by
adding `"name?: type"` for optional fields, required by default.

The general lesson: "a missing field is an error" is right for a schema
describing *one* response and wrong for a schema describing *several*.
Only the author knows which they wrote, so the framework has to ask.

**F2 was not fixed on three of its five paths.** Auditing every route an
`Interrupt` can take — rather than the one the first test happened to
exercise — found the fix held for batch ops on a parallel branch and for
inline `bound="sync"` ops, and failed everywhere else. All five now pass:

| Path | Before the audit |
|---|---|
| batch op on a parallel branch | ✅ fixed in the first pass |
| inline `bound="sync"` op | ✅ fixed in the first pass |
| generator, mid-stream | ❌ resolved to the op ctx, so it swept its siblings |
| nested `GraphOp` | ❌ interrupt unreported **and** a phantom `None` result |
| synthetic loop (`__loop_0__`) | ✅ once the nested path was fixed — it is a nested graph |

**Nested subgraphs lost the cancellation entirely.** A subgraph runs its
own scheduler with `output_queue=None`, which is right for frames — the
outer scheduler forwards those via `_out_vars`, and passing the queue down
would double-emit — but it also dropped the `__interrupt__` record. Worse,
`GraphOp.run` then yielded the cells as they stood, all-`None`, and the
parent forwarded that as a result. Six branches with one interrupted came
back as `[0, 1, None, 3, 4, 5]` with zero interrupts: the cancellation was
invisible *and* replaced by a plausible wrong value. `Scheduler.run` now
returns `root_interrupted`, and the record falls back to the run-level
queue.

**Generators resolved `SELF` too coarsely.** `_pump` stamps `result.ctx`
with the op's dispatch ctx because the self-cancel guard needs that key,
so resolving `SELF` against it made a top-level generator's bare
`Interrupt()` sweep everything under `("main",)` — the original bug,
disguised. It resolves against the per-yield `item_ctx` now. 3 of 5
sibling yields survived before, 5 of 5 after.

Two things checked and found acceptable rather than fixed:

- **Repeated XML leaves with a `: str` hint stringify the list** —
  `<item>a</item><item>b</item>` yields `"['a', 'b']"`. Ugly, but it only
  happens where a value was previously dropped outright, and the shape is
  visible rather than silent.
- **`observe_max` counts for the whole run, so a loop accumulates.** That
  is what a per-run budget means, and the docstring already says so.

---

## Sources studied

- [openclaw/openclaw](https://github.com/openclaw/openclaw) — heartbeat scheduler, SKILL.md frontmatter, serialized session lane
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — deep-inspected across core loop, memory, tools, subagents, learning loop (see v1 for full evidence trail); re-measured 11 Aug 2026 (§14)
- [opencode-ai/opencode](https://github.com/opencode-ai/opencode) — canonical tool set, persistent shell, read-before-edit invariant
- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) — `libs/prebuilt`: `ToolNode` error handling, tool-call validation, state injection, `tools_condition` (§14)
- [langchain-ai/langchain](https://github.com/langchain-ai/langchain) — `libs/langchain_v1/langchain/agents`: the middleware stack, and the `wrap_*`-as-closure-chain finding that §14.2 turns on
- [BA-CalderonMorales/agent-harness](https://github.com/BA-CalderonMorales/agent-harness) — auto-compaction with headroom, layered permission rules, capability flags
- [huggingface/smolagents](https://github.com/huggingface/smolagents) — prompts-as-YAML
- [LangGraph](https://langchain-ai.github.io/langgraph/) — `add_messages` reducer semantics (RemoveMessage + REMOVE_ALL_MESSAGES sentinels are LangGraph-compatible in operonx 1.0.0), stream modes taxonomy
- [Instructor](https://python.useinstructor.com/) — semantic retry with error-guided prompts (mirrored in `LLMOp.of(retry_hint=True)`)

Operonx internals studied to ground the recast:

- `operonx/core/ops/base.py` — `BaseOp` lifecycle, ContextVars, tracing hooks, `@op(exclude=…, include=…, observe_max=…)`
- `operonx/core/ops/graph/graph_op.py` — `GraphOp`, nesting, `strict_dag=` opt-out
- `operonx/core/ops/graph/cycle_rewrite.py` — Phase 3 back-edge → `_GraphLoop` rewrite
- `operonx/core/ops/graph/task_scheduler.py` — streaming, `_on_eof` loop re-dispatch, `Ref.parallel()`/`.collect()`, `_sweep_ctx` interrupt handling
- `operonx/core/ops/flow/branch_op.py` — `if_(…).else_()`, soft edges, back-edge classification
- `operonx/core/ops/flow/interrupt_op.py` — `InterruptOp` HITL primitive
- `operonx/core/ops/flow/emit_op.py` — `EmitOp` custom event bus
- `operonx/core/states/*` — `Ref`, `Cell`, `MemoryState`, per-context isolation, `_notify_scratch` observer bus
- `operonx/core/states/parent.py` + `_edges.py` — `PARENT.declare(reducers=…)`
- `operonx/reducers.py` — `add_messages`, `dict_merge`, `RemoveMessage`, `REMOVE_ALL_MESSAGES`
- `operonx/checkpoint/{base,memory,bridge}.py` — Checkpointer protocol, `CellWriteEvent`, `ScratchWriteEvent`, `StepEvent`, `InterruptEvent`, `CustomEvent`
- `operonx/core/registry/resource_hub.py` — singleton, lazy, `${VAR}` interpolation
- `operonx/core/workflow_trace.py` — V3 auto-record
- `operonx/providers/ops/llm.py` — real op example with streaming + fallback + tools passthrough + `LLMOp.of` structured mode
- `operonx/providers/parsing.py` — pure text parsing without an LLM call

