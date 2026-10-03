# Track 3: Agents on OperonX

*A focused production agent framework built on OperonX.*

**Date:** 2026-10-04 · **Against:** operonx 1.14.0 (`/home/thanglq/Operon`), callbot `refactor/operonx-studio` (`/home/thanglq/callbot-wt/refactor`) · **Status:** proposal. No repo was modified.

## 0 · TL;DR

- **What exists.** `operonx.agents` is about 4.7 kLOC and carefully hardened. It encodes the right invariants: one tool message per call, fail-closed permissions, exchange-safe compaction, cache-stable prompts. Three structural problems sit underneath them:
  1. A **process-global tool registry** that lets any agent execute any registered tool (§1.2 #1).
  2. A **ReAct loop expressed as a graph back-edge**, where nearly every feature needed a workaround for loop semantics (§1.2 #7).
  3. **Nothing durable**: approvals are in-process futures, sessions are Python lists, and the checkpointer only observes (§1.2 #5, #12).
- **Who uses it.** The one production consumer, the callbot, uses none of `operonx.agents`. Its LLM is a single intent classifier inside a YAML state machine. Its real needs are a deadline, fallback, runtime enum constraints, cancel-safe commits and redaction (§2).
- **What the 2026 frameworks converged on** (§3):
  - A small explicit loop.
  - Pause as data and resume from state plus decisions.
  - Errors as observations.
  - Three interception points.
  - Agents-as-tools.
  - Journaled durability.
  - Native or tool-mode structured output.
  - GenAI-convention spans.
  - Workflows before agents.
- **Proposal** (§4): a separate `operonx-agents` package with two front doors.
  - `llm_step`: a typed, deadline-bounded LLM op for workflows. This is the callbot's front door.
  - `Agent` + `Runner`: an explicit loop inside one op, with child steps traced as operonx executions. It has a serialisable `RunState` persisted at turn boundaries, so HITL, crash recovery and sessions are one mechanism.
  - Plus `UsageLimits`, hooks-as-guardrails, agents-as-tools, typed events and MCP over HTTP.
- **Core changes required** (§4.7): K1 child executions in traces, K3 op deadlines, K4 errors that raise, and K7 normalised provider tool-calls, native structured output and logprobs. K2, K5, K6 and K8 are smaller.
- **Roadmap** (§5): about 9–10 weeks.
  - Phase 0 is a measurement spike that gates the one contested decision (D1: the loop leaves the back-edge).
  - Phase 2 alone delivers the callbot's win.

**Decisions only the user can make:**
- **D1:** loop in an op rather than a back-edge, after Phase 0's numbers.
- **D2:** a separate repo rather than in-tree `operonx.agents`.
- **D3:** deprecate and then remove `operonx.agents` (`Heartbeat`, global `TOOL_REGISTRY`) one release after v1.0.

Everything else is resolved in this document.
## 1 · What exists today (read, not assumed)

All paths below are relative to `/home/thanglq/Operon/operonx/` unless stated. Line numbers are against the tree on 2026-10-04 (operonx 1.14.0).

### 1.1 Inventory

| Module | LOC | What it is | Verdict |
|---|---|---|---|
| `agents/tool.py` | 217 | `@tool(name, description, schema, readonly, destructive, concurrency_safe, max_result_chars, timeout)` → an `@op` factory with `_tool_meta`, stored in a **process-global** `TOOL_REGISTRY` | Keep the idea (tool = op + metadata); replace the global registry and hand-written schema |
| `agents/policy.py` | 129 | `ToolPolicy` allow/ask/deny, most-specific-first | Keep almost verbatim — this is good |
| `agents/redact.py` | 159 | Regex credential scrubbing on tool output and approval payloads | Keep as an opt-in output processor |
| `agents/memory.py` | 201 | `MemoryProvider` ABC (prefetch fails soft, write fails loud) + `LocalMarkdownMemory` (keyword overlap) | Keep the ABC and its failure policy; it is the right shape |
| `agents/session.py` | 223 | `AgentSession` — in-process list of messages; `send()` runs the graph and commits only valid histories | Replace with a persisted `Session` store; keep the commit-only-valid rule |
| `agents/mcp.py` | 759 | MCP client (stdio only), namespaced tool registration, annotation→flags, `structuredContent` handling | Keep the semantics; add Streamable HTTP; register into a toolset, not the global registry |
| `agents/skills.py` | 219 | `SKILL.md` loading, keyword matching, per-turn user-message injection | Later — move to progressive disclosure (index in prompt, body via a `load_skill` tool) |
| `agents/heartbeat.py` | 360 | In-process interval timer around `AgentSession.send` | **Drop** — duplicates `app/serve/triggers.py:99` `ScheduleTransport` |
| `agents/graphs/react.py` | 603 | ReAct loop as a `@graph` with a back-edge; turn budget with a final tools-disabled turn | Replace the loop mechanism; keep its invariants (see 1.3) |
| `agents/graphs/dispatch.py` | 403 | Per-call subgraph parse → policy → `InterruptOp` / auto → execute; every failure becomes a tool message | Keep the *contract*, re-host the mechanism |
| `agents/graphs/subagent.py` | 303 | `make_delegate_tool` — nested ReAct run as a tool, default-deny child policy, depth cap | Keep the policy logic; re-host as agent-as-tool |
| `agents/ops/model_ops.py` | 215 | `make_llm_caller` — wraps `LLMOp` + an adapter into the loop's contract | Replace with a `Model` abstraction |
| `agents/ops/compact_ops.py` | 321 | Exchange-safe compaction planner (`_exchanges` keeps tool_call/result pairs together) | Keep the planner; persist the summary |
| `agents/ops/prompt_ops.py` | 218 | Cache-friendly assembly, `apply_cache_control`, `prefix_is_stable` | Keep; extend breakpoints to the conversation tail |
| `agents/ops/memory_ops.py` | 192 | Fan-out prefetch with deadlines; `gather_memory` | Keep `gather_memory` |

Substrate the agent layer sits on:

- `providers/ops/llm.py` (1784 LOC) — `LLMOp`: `prompt=` vs `messages=` split (`:149-176`), `tools`/`tool_choice`/`response_format` passthrough (`:380-397`), outputs `content, tool_calls, finish_reason, usage, cost_usd, extras` (`:401-414`), `fallback=` chain on hard failures and refusals (`:729-796`), transport retry with jitter (`:824-950`), **structured mode** = `fields=["path: type"]` + `parser=xml|json|yaml` + `validators` + semantic `max_retries` (`:226-367`, `:986-1046`) — a *prompted-parse* approach, not provider-native JSON-schema / Pydantic output. Streaming falls back only before the first delta (`:1051-1075`).
- `checkpoint/` — `Checkpointer` is an **observer** of cell writes for replay/inspection (`checkpoint/base.py:1-25`), `InMemoryCheckpointer` only (`checkpoint/memory.py:18`). There is no "restore a run from step N" API in `core/engine.py` (`start()` at `:571-640` takes `checkpointer=` only to record).
- `core/ops/flow/interrupt_op.py` — `InterruptOp` is an **in-process future** (`core/states/state.py:382-394` `resume_interrupt`): an approval cannot outlive the process.
- `core/ops/flow/emit_op.py` + `engine.stream(mode="updates"|"values"|"frames"|"custom")` (`core/engine.py:810-1027`).
- `telemetry/` — per-op `OpExecution` traces (`core/workflow_trace.py:107`), consumers for local/Langfuse/ClickHouse, run roll-ups with `tokens_in/out/cached` and `cost_usd` (`telemetry/runs/model.py:63-153`). No OpenTelemetry export (no `opentelemetry`/`gen_ai.*` reference anywhere in the package). No API for an op to record *child* executions.
- `app/` — `Application`/`Service`/`Job`, session-based serve layer (`app/serve/protocol.py:1-18`), `schedule` trigger (`app/serve/triggers.py:99`), evals as jobs with `exact/contains/fuzzy/json_match/llm_judge` (`app/evals.py:1-60`).
- `cli/templates/agent/` — `operonx init --template agent` already ships an HTTP service whose graph embeds `build_react_agent` as a node (`cli/templates/agent/src/assistant/graph.py.tmpl`).

### 1.2 Concrete weaknesses (each read in the source)

**Correctness / security**

1. **Global tool registry = no isolation between agents.** `TOOL_REGISTRY` is process-wide (`agents/tool.py:289`) and dispatch resolves whatever name the model emits against it (`agents/graphs/dispatch.py:774`, `:908`). The default policy is `allow` for anything not destructive (`agents/policy.py:352`). So a top-level agent built with `make_llm_caller(tools=get_tool_definitions(["a"]))` will still *execute* tool `b` if the model names it and `b` is registered anywhere in the process. Only sub-agents compile a default-deny policy (`agents/graphs/subagent.py:439-461`), and its docstring admits the original `allow_tools` "was decoration" (`:9-14`). Multi-tenant services (callbot runs several campaigns per process) cannot safely share one registry.
2. **`concurrency_safe` is declared and never read.** Only `agents/tool.py:313,380` mention it; the loop fans every call out at `parallel(max=8)` regardless (`agents/graphs/react.py:401`).
3. **Tool calls are not traced as tool spans.** `execute` builds the op and calls `call.core(**args)` (`agents/graphs/dispatch.py:921-924`); `core` is the raw function (`core/ops/base.py:997`), so the comment "reuse the op's own execution path so the tool keeps its tracing" (`dispatch.py:922-923`) is not what happens — the tool appears only inside the `execute` span.
4. **Errors are swallowed by default.** Op exceptions go to `$errors`, not raised (`operonx/guide/04-gotchas.md:7-41`; finding S1 in `docs/design/OPEN_FINDINGS.md:326-348`). Half the agent layer's code exists to survive that: `normalize_messages` (`react.py:89-106`), `gather_tool_messages` (`react.py:129-165`), the "empty answer means an op raised" branches in `session.py:180-193` and `subagent.py:399-408`.
5. **HITL is not durable.** Approvals are futures in process memory (`core/states/state.py:382-394`); `approval_timeout` defaults to 300 s (`react.py:179`). A crash, deploy or a human answering tomorrow loses the run. The serve layer has no path to surface an interrupt to an HTTP/WS client (no `interrupt` reference in `app/serve/*.py`).
6. **Budget-notice detection is a string prefix.** `last_user_text` skips `"You have used your entire turn budget"` literally (`react.py:123`) while `budget_notice` is a parameter (`react.py:188`) — a custom notice becomes the memory/skill query.

**Architecture / ergonomics**

7. **Loop-as-back-edge costs a workaround per feature.** Documented in the code itself: outputs are a per-iteration stream so results must be read from cells via `agent_result` (`react.py:11-15`, `:550-603`); a terminal op after a loop containing a generator "never runs" (`react.py:168-172`); `collect()` behind `parallel()` inside a loop delivers partial batches, so memory fan-out had to be collapsed into one op (`memory_ops.py:162-171`); the cycle rewrite broke refs until the model call was wrapped in a nested `@graph` (`model_ops.py:137-144`). The callbot measured a back-edge turn loop and rejected it (project memory, "Callbot refactors rejected on measurement").
8. **Four concepts for hello-world.** `@tool` + `make_llm_caller` (not exported from `operonx.agents`, imported from `agents/ops/model_ops.py`) + `build_react_agent(...)(messages=None)` + `agent_result(result, agent)` (`docs/guide/05-agents.md:10-44`). Tools are frozen at build time (`agents/tool.py:439-441`, `mcp.py` "must be called before the agent graph is built").
9. **Hand-written JSON Schema per tool.** `schema=` is required (`tool.py:310`) on the claim it "cannot be derived" (`tool.py:8-14`). Every current SDK derives it from type hints + docstring (Pydantic `TypeAdapter`/`Field(description=)`), which also gives argument *validation* — today arguments are only JSON-parsed (`dispatch.py:754-772`), never type-checked.
10. **No typed output.** The loop's answer is "the last assistant message dict" (`react.py:502-515`). `LLMOp` structured mode is prompted XML/JSON parsing with open findings P3–P6 (`OPEN_FINDINGS.md:202-231`) and no native `response_format: json_schema` path despite the passthrough (`llm.py:396`).
11. **No streaming agent events.** `make_llm_caller` never sets `stream=True`; with `stream=True` the adapter `adapt_llm_output` (`model_ops.py:41-74`) does not look at `final`, so it would fire per token frame (by reading; not run). The reference harness "renders the final answer only" (`AGENT_EXTENSION_PLAN.md:976-979`).
12. **Sessions are a Python list.** `AgentSession._messages` (`session.py:64`); a new `Operon` per send with a random `session_id` (`session.py:118-122`, `core/engine.py:607`), so one conversation's turns are not grouped in traces.
13. **Sub-agents are untraced and rebuilt per call.** `delegate` rebuilds the child graph and runs `Operon(child).start(...)` with no `trace=`/`trace_id` (`subagent.py:372-379`) — the child is invisible in Langfuse/Studio and unlinked from the parent span.
14. **Compaction drops instead of summarising, and recomputes every turn.** The default loop wires `apply_compaction` with no `summary` (`react.py:374-378`), so the span is replaced by "N messages were removed" (`compact_ops.py:263-268`); the plan is recomputed from the full history each turn (`react.py:363-367`), so a wired summariser would re-run every turn and every compaction shifts the prompt prefix.
15. **Token estimation is 3.5 chars/token** (`compact_ops.py:54`) — off by a large factor for Vietnamese (the callbot's language), and the provider already returns exact `usage` (`llm.py:413`) that the loop never feeds back.
16. **Cache breakpoints only on the system prefix** (`prompt_ops.py:484-503`). In a long tool loop the growing history is never cached (Anthropic's recommended breakpoint is the last message).
17. **No run-level limits beyond turns.** No token, cost, tool-call or wall-clock budget enforced *during* a run; cost exists only as a post-hoc roll-up (`telemetry/runs/model.py:77-85`).
18. **MCP is stdio-only** (`agents/mcp.py:198-201`): no Streamable HTTP, no auth, so no remote/hosted MCP servers.
19. **Heartbeat duplicates the serve layer's `schedule` trigger** (`agents/heartbeat.py` vs `app/serve/triggers.py:99-144`), and carries 3 of the 5 open MCP/heartbeat findings (M3–M5, `OPEN_FINDINGS.md:267-287`).

What the current layer got **right** and must survive any redesign: every tool call yields exactly one tool message (dispatch.py:612-623); fail closed on permission, fail open on enrichment (`agents/CONTRIBUTING.md` §4, `memory.py:368-376`); deny ≠ ask (`policy.py:13-16`); exchange-safe compaction (`compact_ops.py:108-141`); the history is committed only when valid (`session.py:154-200`); the final budget turn runs with `tool_choice="none"` (`model_ops.py:77-86`); cache-stable prompt ordering (`prompt_ops.py:1-24`); namespaced MCP tools gated unless `readOnlyHint` (`mcp.py:546-562`). These are hard-won and are carried forward as invariants (§6).
## 2 · What the callbot actually needs (read-only audit of `/home/thanglq/callbot-wt/refactor`)

The callbot is the one production consumer, and **it uses none of `operonx.agents`** (no import anywhere). Its shape is not ReAct:

- **One LLM call site**, an intent classifier: `LLMOp.of(resource="inhouse", prompt={...}, fields=["intent: str"], parser="json", max_retries=1)` at `src/agents/graph.py:74-87`. Every spoken reply is a YAML template (`src/agents/ops.py:320-384`); the comment at `ops.py:378-379` reads "1 LLM call per turn max (intent classify)".
- **The outer loop is a deterministic state machine**: `scenario[state][intent] → next_state / hangup / transfer` (`src/agents/_config.py:183-213`), with regex/silence detectors that skip the LLM when they match (`src/agents/graph.py:96`, `src/agents/_detectors.py:40-77`). That's Anthropic's "workflow", not an "agent".
- **`validators=` cannot be used**: it's a constructor argument, so the per-state allow-list (a runtime value) never resolves (`src/agents/graph.py:82-84`). The allow-list is re-implemented by hand in `merge_intent` (`ops.py:144-193`), and confidence is invented: 0.85 or 0.5 (`ops.py:180-192`).
- **No deadline and no fallback**: the `inhouse` resource has transport `max_retries=0` and the effective timeout is httpx's 120 s read timeout (`operonx/providers/_utils/http.py:18`). The silence timer is suppressed while the turn runs (`src/call/ops.py:581-587`), so a hung gateway means dead air.
- **Superseded turns still commit**: `agent_turn["agent_state"] >> PARENT` runs unconditionally (`src/call/graph.py:239`), and the in-flight LLM call of a superseded speculative chain isn't cancelled.
- **History isn't sent to the model**: only `last_agent_response` reaches the classifier. `conversation_history` exists for the CRM transcript (`src/call/_record.py:41-49`).
- **PII is logged unredacted** (`src/call/ops.py:325`, `src/call/_terminal.py:97-101`).

**Requirements this puts on the framework** (each traced to the audit above):

| # | Requirement | Why |
|---|---|---|
| R1 | A typed LLM step with a **hard deadline** that degrades to a declared value, never raises | 120 s dead air today |
| R2 | **Runtime-valued output constraints**: a per-call enum, with real confidence from logprobs | Hand-rolled allow-list, fake confidence |
| R3 | **Cancel-safe commit**: a cancelled or superseded step writes nothing | Superseded chains commit state |
| R4 | **Streaming at sentence granularity**, plus prompt-prefix caching and warmup per resource | TTFA budget of about 0.9 s at 5 CCU |
| R5 | A **deterministic outer loop stays user code**. The framework must not force a ReAct loop on a workflow | The FSM is the product |
| R6 | Framework overhead per turn in **milliseconds**, and 5+ concurrent calls per process | Capacity doc: VAD already saturates past about 8 CCU |
| R7 | **Redaction** for traces and logs | PII in logs |
| R8 | **Per-tenant config as data**, with the model selectable per agent plus a `fallback` list | One literal resource today |

**Consequence for the design:** the framework has two front doors. One is a *typed LLM step* for workflows (the callbot today). The other is an *agent runner* for model-driven tool loops (future callbot features such as FAQ answering or CRM lookup, plus every non-voice project). The step is not a degenerate agent. It's the more common production primitive, and the agent's own model call is built on it.
## 3 · Research: production agent frameworks and patterns, late 2026

Primary docs were fetched on 2026-10-04. Claims marked *(unverified)* rest on third-party or search-snippet sources only. Two load-bearing claims were re-fetched independently: the langgraph-supervisor deprecation and the MCP 2026-07-28 stateless model.

### 3.1 OpenAI Agents SDK

- **Loop.** `Runner.run` / `run_streamed` loops: model → final output? return : handoff? switch agent : run tools. `max_turns` raises `MaxTurnsExceeded` unless `RunConfig.error_handlers` maps `max_turns` / `model_refusal` / `invalid_final_output` to a fallback. Output is final only when it is "text output with the desired type, and there are no tool calls". https://openai.github.io/openai-agents-python/running_agents/
- **Agent.** `Agent[TContext]` carries `output_type`, `handoffs`, dynamic `instructions(ctx, agent)`, `reset_tool_choice=True` against tool loops, and `tool_use_behavior` (`run_llm_again` | `stop_on_first_tool` | `StopAtTools` | a function). https://openai.github.io/openai-agents-python/agents/
- **Tools.** Errors and timeouts are returned to the model by default (`failure_error_function`, `timeout_behavior="error_as_result"`). Also available: `agent.as_tool(...)`, and `defer_loading` + `ToolSearchTool` for large tool surfaces. https://openai.github.io/openai-agents-python/tools/
- **HITL.** `needs_approval=True` or a predicate makes the run return `result.interruptions`. `result.to_state()` produces a `RunState` that serialises to JSON; the caller then calls `state.approve/reject` and `Runner.run(agent, state)`. Interruptions inside `as_tool` children surface on the outer run. https://openai.github.io/openai-agents-python/human_in_the_loop/
- **Sessions.** The `Session` protocol is `get_items / add_items / pop_item / clear_session`, with SQLite/Redis/SQLAlchemy backends and a compaction-wrapping session. https://openai.github.io/openai-agents-python/sessions/
- **Streaming and tracing.** Streaming has three tiers: raw deltas, run-item events, agent-updated. Tracing creates spans automatically per agent, generation, function, guardrail and handoff. https://openai.github.io/openai-agents-python/streaming/ · https://openai.github.io/openai-agents-python/tracing/
- **Copy:** the serialisable `RunState` + approve/reject API, errors as observations, and the two event granularities. **Criticised:** handoffs as a context-loss boundary *(unverified, third-party)*.

### 3.2 Claude Agent SDK and Anthropic engineering guidance

- **Result.** `ResultMessage.subtype` is one of `success | error_max_turns | error_max_budget_usd | error_during_execution | error_max_structured_output_retries`. It always carries `total_cost_usd`, `usage`, `num_turns` and `session_id`. `max_budget_usd` includes subagents. https://code.claude.com/docs/en/agent-sdk/agent-loop
- **Parallelism.** Read-only tools run concurrently; mutating tools and unannotated custom tools run sequentially (`readOnlyHint`). (same URL)
- **Hooks.** `PreToolUse` returns `allow|deny|ask|defer`, `updatedInput` and a reason that is sent to the model. Verdicts merge **deny > defer > ask > allow**, and `defer` ends the turn so it can be resumed. https://code.claude.com/docs/en/agent-sdk/hooks
- **Permissions.** The documented footgun: "Auto-approved tools never reach `canUseTool`". A mandatory gate belongs in a pre-tool hook. https://code.claude.com/docs/en/agent-sdk/permissions
- **Subagents.** Each gets a fresh context, and only its final message returns. Depth, concurrency and spend caps are enforced by the runtime. https://code.claude.com/docs/en/agent-sdk/subagents
- **Sessions advice.** "capture the results you need as application state and pass them into a fresh session… often more robust" than relying on resume. https://code.claude.com/docs/en/agent-sdk/sessions
- **Context editing.** `clear_tool_uses_20250919` (`trigger`, `keep`, `clear_at_least`) exists because clearing invalidates the prompt cache. https://platform.claude.com/docs/en/build-with-claude/context-editing
- **Advanced tool use.** Tool search (`defer_loading`) cuts about 85% of tool-definition tokens, and `input_examples` lifted accuracy from 72% to 90%. https://www.anthropic.com/engineering/advanced-tool-use
- **Building effective agents.** Workflows (chaining, routing, parallelisation, orchestrator-workers, evaluator-optimizer) come before agents. Frameworks "obscure underlying logic". https://www.anthropic.com/engineering/building-effective-agents
- **Context engineering.** Treat attention as a budget. Retrieve just in time by identifier. Use compaction, tool-result clearing, notes, and subagents that return 1–2k-token summaries. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- **Writing tools.** Consolidate and namespace tools, return semantic output, paginate and truncate, and write actionable errors. https://www.anthropic.com/engineering/writing-tools-for-agents
- **Multi-agent research system.** It scored 90.2% better than a single agent, at about 15× the tokens of chat. Token usage explains 80% of the variance. It needs checkpoints and resume, because errors compound. https://www.anthropic.com/engineering/multi-agent-research-system
- **Evals.** Start with 20–50 tasks taken from real failures, run multiple trials, report pass^k, grade outcomes rather than paths, and read transcripts. https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- **Copy:** the result-subtype enum with cost, deny-wins hook merging, runtime-enforced budgets and depth, and read-only-parallel. **Avoid for a library:** around 35 hook events and six permission modes, which belong to a coding-agent product.

### 3.3 Google ADK

- Callbacks `before_/after_ × agent|model|tool` **short-circuit by returning a value**: a `before_model` that returns a response skips the LLM. Plugins sit globally on the `Runner`. https://adk.dev/callbacks/ · https://adk.dev/plugins/
- Session state uses scoped prefixes (`user:`, `app:`, `temp:`). `SessionService` is kept separate from `MemoryService`. https://adk.dev/sessions/
- Resume restores finished tool results but re-runs the one in flight: "tools may execute multiple times". https://adk.dev/runtime/resume/
- ADK 2.0 moves to graph + dynamic workflows with HITL as a runtime primitive. https://adk.dev/2.0/ *(fetched by the research agent; post-dates the author's knowledge)*

### 3.4 Pydantic AI

- `RunContext[Deps]` provides typed DI. `UsageLimits` covers requests, tool calls, input/output tokens and cost. `history_processors` run before each request. https://pydantic.dev/docs/ai/core-concepts/agent/
- **Output modes:**
  - `ToolOutput` is the default and the most reliable.
  - `NativeOutput` uses a JSON schema, with limited model support.
  - `PromptedOutput` is the least reliable.
  - Validation raises `ModelRetry` → re-ask. https://pydantic.dev/docs/ai/core-concepts/output/
- **Deferred tools.** `requires_approval` makes the run **end** with `DeferredToolRequests`. To resume, call `run(message_history=…, deferred_tool_results=…)`. Pausing is a result, not an exception. https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/
- **Durable execution.** Temporal, DBOS, Prefect and Restate integrations wrap every model request and tool call as a durable unit. https://pydantic.dev/docs/ai/integrations/durable_execution/overview/
- `FallbackModel(fallback_on=…)` takes predicates. https://pydantic.dev/docs/ai/api/models/fallback/

### 3.5 LangGraph / LangChain v1

- `interrupt()` with `Command(resume=…)` needs a checkpointer and a thread id. On resume **the whole node re-runs**, so side effects before the interrupt must be idempotent. https://docs.langchain.com/oss/python/langgraph/interrupts
- Durability modes are `exit | async | sync`. https://docs.langchain.com/oss/python/langgraph/durable-execution
- Checkpointers hold thread state; a `Store` holds cross-thread memory. https://docs.langchain.com/oss/python/langgraph/persistence
- `create_agent` middleware (`before_model`, `wrap_model_call`, `wrap_tool_call`) ships with about 19 built-ins. https://docs.langchain.com/oss/python/langchain/middleware/built-in
- **`langgraph-supervisor` "is no longer actively maintained"**. The replacement is "the subagents pattern: a main agent coordinates specialized workers by calling them as tools" (re-fetched and confirmed). https://docs.langchain.com/oss/python/migrate/langgraph-supervisor
- **Criticised:** ceremony and API churn *(third-party)*: https://braindetox.kr/en/posts/langgraph_complexity_lesson_2026.html

### 3.6 Others

- **smolagents.** `CodeAgent` acts in Python. The docs say to "regularize towards not using any agentic behaviour". https://huggingface.co/docs/smolagents/conceptual_guides/intro_agents
- **Mastra.** Typed `suspend()` / `resume()` payloads, with snapshots that survive deploys. Memory is scoped by resource (user) vs thread, and adds working memory and semantic recall. https://mastra.ai/docs/workflows/suspend-and-resume · https://mastra.ai/docs/memory/overview
- **CrewAI.** Flows (`@start/@listen/@router`, `@persist`) are preferred over crews. The hierarchical manager is criticised as not really delegating *(unverified)*. https://docs.crewai.com/en/concepts/flows
- **Microsoft Agent Framework** (AutoGen + SK). "If you can write a function to handle the task, do that instead of using an AI agent". It has functional and graph workflows over one run model, plus agent/function/chat middleware. The docs warn that terminating mid-loop breaks call/result pairing. https://learn.microsoft.com/en-us/agent-framework/overview/ · https://learn.microsoft.com/en-us/agent-framework/concepts/agents/middleware/
- **DSPy.** Optimizers (BootstrapFewShot / GEPA / MIPROv2) each need a program, a metric and a trainset. The relevance here is to keep prompts as data plus a metric hook *(page partly unverified)*. https://dspy.ai/
- **Letta.** Memory blocks (`label`, `value`, `limit`, `description`, `read_only`) are always visible and edited through tools. Sleep-time agents consolidate memory. https://docs.letta.com/guides/agents/memory-blocks · https://docs.letta.com/guides/agents/architectures/sleeptime

### 3.7 Protocols

- **MCP.**
  - 2025-06-18: structured tool output and OAuth resource-server model. https://modelcontextprotocol.io/specification/2025-06-18/changelog
  - 2025-11-25: tool *input-validation errors must be tool execution errors* "to enable model self-correction". JSON Schema 2020-12. https://modelcontextprotocol.io/specification/2025-11-25/changelog
  - **2026-07-28: stateless by default.** No `initialize` handshake, no `Mcp-Session-Id`. Tasks become an extension. Roots and Sampling are deprecated, with at least one year before removal (re-fetched and confirmed). https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/
- **A2A 1.0.** An agent card at `/.well-known/a2a/agent-card`. Task states include `INPUT_REQUIRED` and `AUTH_REQUIRED`. Bindings over JSON-RPC, gRPC and REST. https://a2a-protocol.org/latest/specification/
- **Convergence:** a request completes, returns *input-required + opaque state*, or becomes a pollable task. That is the same shape as `RunResult(status="interrupted", state_id)`.

### 3.8 Durable agents

- **Temporal** wraps model calls as activities (`OpenAIAgentsPlugin`). https://docs.temporal.io/ai-cookbook/openai-agents-sdk-python
- **Restate** journals every LLM and tool call; approvals are durable promises. "an AI agent is just regular code". https://restate.dev/blog/durable-ai-loops-fault-tolerance-across-frameworks-and-without-handcuffs
- **DBOS** steps are "tried at least once but are never re-executed after they complete". https://docs.dbos.dev/python/tutorials/workflow-tutorial
- **Inngest AgentKit:** networks + `waitForEvent`. https://agentkit.inngest.com/
- **Common pattern:** journal steps by `(run_id, step)`, replay recorded results, re-run only the in-flight step, and require idempotency for side effects.

### 3.9 Observability

The OpenTelemetry GenAI conventions are all still at *Development* status:
- **Operations:** `invoke_agent`, `execute_tool`, `chat`.
- **Agent and tool attributes:** `gen_ai.agent.name`, `gen_ai.tool.name`, `gen_ai.tool.call.id`.
- **Conversation and usage attributes:** `gen_ai.conversation.id` / `.compacted`, `gen_ai.usage.{input,output}_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.response.time_to_first_chunk`.
- Message payloads are opt-in.

Source: https://github.com/open-telemetry/semantic-conventions-genai

### 3.10 Consensus production patterns (what the design adopts)

1. **Workflows first, and an agent loop only where the path is unpredictable.** (Anthropic, Microsoft, 12-factor https://github.com/humanlayer/12-factor-agents). The design answer is `llm_step` alongside `Agent`.
2. **A small explicit loop** with turn and budget caps that ends in a typed terminal status carrying cost (§3.1, §3.2). Adopted as `RunResult.status` + `UsageLimits`.
3. **Pause is a return value; resume = state + decisions** (§3.1, §3.4, §3.7). Adopted: interruptions are data.
4. **Tool errors, timeouts and validation failures are observations** (§3.1, §3.7). Kept from the current dispatch.
5. **Three interception points** (run, model, tool) that can short-circuit, with deny-wins merging (§3.2, §3.3, §3.5). Adopted as hooks.
6. **Durability = journal model and tool steps; idempotency for side effects; configurable durability** (§3.5, §3.8). Adopted at turn granularity, with inflight tracking.
7. **Multi-agent = agents-as-tools with isolation and runtime caps** (§3.2, §3.5). Adopted. Handoffs come later.
8. **Layered context management:** clear tool results, then compact, then notes or memory, all cache-aware (§3.2). Adopted.
9. **Structured output via tool or native mode with validate-and-retry; prompted mode as the fallback** (§3.4). Adopted.
10. **Read-only tools run in parallel, mutating tools serially** (§3.2). Adopted.
11. **Predicate-driven model fallback at the model layer** (§3.4). Adopted.
12. **GenAI-convention spans and cached-token usage** (§3.9). Adopted as attributes on child executions.
13. **Evals from real failures with trajectory and outcome graders** (§3.2). Adopted on `app/evals`.

### 3.11 Anti-patterns (what the design refuses)

- **LLM-routed swarms and manager hierarchies by default.** Error amplification is 17.2× for independent multi-agent setups *(Google research blog, via research agent)*. See https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/ and https://cognition.com/blog/dont-build-multi-agents
- **Graph-DSL ceremony for linear logic.** https://pydantic.dev/docs/ai/graph/graph/
- **Resume by re-running a whole node.** Side effects get duplicated (§3.5, §3.3).
- **Approval gates that allow-rules bypass** (§3.2).
- **Middleware that breaks call/result pairing** (§3.6).
- **A combinatorial matrix of built-ins.** Ship protocols plus one or two backends instead (§3.2, §3.1).
- **Sticky server-side protocol sessions.** MCP itself dropped them (§3.7).
- **Prompted JSON as the primary structured-output strategy** (§3.4).
- **Preloading every tool schema** (§3.2).
## 4 · Design

### 4.1 Principles (each one rules something out)

1. **Spec is data, the runner executes.** An `Agent` is a frozen dataclass: name, instructions, model, tools, output type, limits, policy, hooks. There is no base class to subclass and no `run()` on the agent. The consensus shape across OpenAI Agents SDK, Pydantic AI and ADK (§3) and the reason hermes-style god-classes are rejected (`AGENT_EXTENSION_PLAN.md:808-824`).
2. **Two front doors, one model layer.** `llm_step` for workflows, where the graph owns control flow, and `Agent` + `Runner` for model-driven loops. Both call the same `Model` layer.
3. **State is explicit and serialisable.** Everything a run needs to continue is held in a `RunState` (messages, usage, turn, pending approvals, in-flight tool calls). The runner saves it to a `StateStore` **at turn boundaries**. HITL, crash recovery and multi-turn sessions are all the same thing: load, continue.
4. **Every failure becomes something the model or the caller can read.** This keeps today's tool-message invariant (`dispatch.py:612-623`) and extends it to model errors, limits and guardrails. Nothing is recorded silently into `$errors` and left there.
5. **Fail closed on permission, fail open on enrichment.** Carried over verbatim (`agents/CONTRIBUTING.md` §4).
6. **No process-global state.** Each agent owns its tools. There's no registry lookup by name at dispatch time.
7. **Cheap when unused.** An agent with no memory, no guardrails and no MCP pays for none of them. The callbot path (`llm_step`) has single-digit-millisecond overhead or it ships nothing (R6).

### 4.2 Architecture

```
                    ┌──────────────────────────────────────────────────────────┐
  user code         │  Agent(spec)   llm_step(...)   @tool   Session   Eval     │
                    └──────────────┬───────────────────────┬───────────────────┘
                                   │                       │
                    ┌──────────────▼──────────┐   ┌────────▼─────────────────┐
  operonx_agents    │ Runner (loop, limits,   │   │ Model (LLM call, typed   │
                    │ policy, approvals,      │──▶│ output, fallback,        │
                    │ hooks, events, resume)  │   │ deadline, usage)         │
                    └───┬──────────┬──────────┘   └────────┬─────────────────┘
                        │          │                        │
           ┌────────────▼───┐ ┌────▼───────────┐   ┌────────▼────────┐
           │ Toolset (local │ │ StateStore /   │   │ operonx         │
           │ @tool, MCP,    │ │ Session        │   │ providers/llms  │
           │ agent-as-tool) │ │ (mem/redis/sql)│   │ (BaseLLM)       │
           └────────────────┘ └────────────────┘   └─────────────────┘
                        │
  operonx core  ────────▼──────────────────────────────────────────────────────
     AgentOp is an @op (generator, transient event port) ─ child steps traced as
     OpExecutions under its ctx ─ serve layer doors ─ Studio ─ evals as Jobs
```

**Decision D1 — where the loop lives. Proposed: inside one op, as an explicit `async` loop with traced child steps. Not a graph back-edge.**

This reverses Rule 2 of `agents/CONTRIBUTING.md` ("every loop … is a back-edge"), so it's flagged rather than assumed. The evidence:

- **Every feature of the back-edge loop paid a workaround.** See §1.2 item 7: results must be read from cells (`react.py:550-603`), a terminal op never runs (`react.py:168-172`), `collect` gives partial batches in loops so memory fan-out was collapsed (`memory_ops.py:162-171`), and model calls need graph-nesting to survive the cycle rewrite (`model_ops.py:137-144`).
- **Durable resume of a synthesized loop needs core to restore a scheduler position mid-iteration.** No such API exists (`checkpoint/base.py:1-25` observes only). An explicit loop resumes from a `RunState` with no core support.
- **The callbot measured a back-edge turn loop and rejected it** (project memory).
- **Every surveyed production SDK runs the agent loop as code** and gets visibility from spans (§3). LangGraph's `create_react_agent` is a graph, but its durable state is a checkpoint per super-step, which operonx lacks.

**The second job of the back-edge loop is visibility.** Studio shows the loop's zones (`react.py:357-360`). D1 keeps that job: every model call, tool call and sub-agent becomes a *child execution* in the trace tree (core change K1). Studio's ctx-tree view then shows `agent → turn[3] → tool:lookup_order`.

**Gate:** Phase 0 measures both before committing (§8). If the in-op loop can't match the graph loop's trace fidelity in Studio, or costs more per turn, D1 falls back to keeping the graph loop and fixing its workarounds in core.

### 4.3 Package structure

Proposed as a **separate repository and distribution**, `operonx-agents` (import name `operonx_agents`), in a sibling folder of `Operon/` with its own git init. It depends on `operonx>=<release with K1–K4>`. The existing `operonx.agents` is kept for one release as deprecated shims, then removed. The reasoning: opinions belong outside the substrate (`AGENT_EXTENSION_PLAN.md:950-955`, the operonx-code decision), the release cadence differs, and LangGraph made the same split.

```
operonx_agents/
├── __init__.py            Agent, Runner, RunContext, tool, llm_step, Model, ModelSettings,
│                          UsageLimits, ToolPolicy, RunResult, RunState, events
├── agent.py               Agent (frozen dataclass), Agent.as_tool(), Agent.as_op(), clone()
├── run/
│   ├── runner.py          Runner.run / .stream / .resume — the loop (≈400 LOC budget)
│   ├── state.py           RunState, PendingCall, Interruption — pydantic, versioned JSON
│   ├── result.py          RunResult (status, output, usage, interruptions, state, trace_id)
│   ├── events.py          typed stream events (§4.4.6)
│   ├── limits.py          UsageLimits + enforcement
│   └── context.py         RunContext[Deps] — deps, usage, run_id, session_id, agent, turn
├── model/
│   ├── model.py           Model(resource, fallback=[...], settings, deadline) over BaseLLM
│   ├── output.py          output_type → strategy (native json_schema | tool | prompted) + validation
│   └── step.py            llm_step — the typed workflow step (an @op)
├── tools/
│   ├── tool.py            @tool — schema from signature/docstring (pydantic), ToolSpec
│   ├── toolset.py         Toolset protocol, FunctionToolset, filtering, prefixing
│   ├── dispatch.py        execute calls: validate → policy → approval → run → ToolMessage
│   ├── policy.py          ToolPolicy (ported from operonx.agents.policy as-is)
│   └── mcp.py             MCPToolset (stdio + streamable HTTP), ported semantics
├── context/
│   ├── session.py         Session protocol + InMemory/Redis/SQL sessions
│   ├── compaction.py      ported planner + persisted summaries + tool-result clearing
│   ├── prompt.py          ported cache-stable assembly + breakpoints
│   └── memory.py          MemoryProvider (ported) + memory tools
├── safety/
│   ├── hooks.py           Hooks: before/after model, before/after tool, on_handoff
│   ├── guardrails.py      input/output guardrails built on hooks
│   └── redact.py          Redactor (ported)
├── serve.py               agent_service(): session id → Session, SSE/WS events, /resume
└── evals.py               trajectory evaluators over app/evals (tool_called, no_tool_errors, …)
```

Size target: **≤ 3.5 kLOC** excluding the ported MCP client. The current `operonx.agents` is about 4.7 kLOC, and much of that is loop workarounds (§1.2 item 7).

### 4.4 Core abstractions — API sketches

#### 4.4.1 Tools

```python
from operonx_agents import tool, RunContext, ModelRetry

@tool                                   # name = function name; description = docstring summary
async def lookup_order(ctx: RunContext[Deps], order_id: str) -> Order:
    """Look up an order by its code.

    Args:
        order_id: The 8-character order code the customer read out.
    """
    return await ctx.deps.crm.order(order_id)

@tool(approval="always", idempotent=False, timeout=10, sequential=True, max_result_chars=20_000)
async def refund(ctx: RunContext[Deps], order_id: str, amount: Annotated[Decimal, Field(gt=0)]) -> str:
    """Refund an order."""
    if amount > ctx.deps.limit:
        raise ModelRetry(f"amount exceeds the {ctx.deps.limit} limit; ask a human")  # → tool message
    ...
```

- **Schema and validation** come from one source: a pydantic `TypeAdapter` over the signature, minus the `ctx` parameter. The argument docs come from the docstring (Google, NumPy or Sphinx style). `schema=` stays as an explicit override for odd cases. Arguments are **validated** before the call. A validation error becomes a tool message naming the field, and the model retries (Pydantic AI's behaviour, §3).
- `ToolSpec` fields: `name, description, params_schema, approval: "never"|"always"|Callable[[ctx,args],bool], idempotent, sequential, timeout, max_result_chars, readonly/destructive hints` (used for MCP annotations and policy).
- **A tool is still an op** for tracing. Dispatch runs it as a child execution (K1), so it gets its own span with inputs and outputs. That fixes §1.2 item 3.
- **Parallel calls (Claude Agent SDK rule, §3.2):** the calls in one model turn run concurrently only when the tool is `readonly=True` (or explicitly `concurrent=True`). Everything else runs serially, in emitted order, after the concurrent batch. That's safe by default, and `concurrency_safe` finally means something.
- **Every outcome yields exactly one `ToolMessage`**, whether it's success, `ModelRetry`, a validation error, a timeout, a denial, an expired approval or an unknown tool. These are today's dispatch strings, kept.

#### 4.4.2 Model and typed output

```python
from operonx_agents import Model, ModelSettings

fast = Model("inhouse", fallback=["qwen-turbo"], deadline=0.9,
             settings=ModelSettings(temperature=0, max_tokens=64, logprobs=True))
```

`Model` is a thin object over `ResourceHub` `llm:` resources, using `BaseLLM.generate/stream`. It owns:

- **The retry taxonomy** from `agents/CONTRIBUTING.md` Rule 4. The transport retries in the provider. The **fallback chain** handles hard failures and refusals, and stops after the first streamed delta (same as `llm.py:1051-1062`). The output-validation retry is a re-ask that carries the error. Semantic failures go to the next turn.
- **Deadline:** `asyncio.timeout(deadline)` around the whole attempt chain, including fallbacks. On expiry it raises `ModelTimeout`, which callers map to a value (R1).
- **Output strategies**, chosen per provider capability:
  - `native`: `response_format={"type":"json_schema", strict}` on OpenAI-compatible providers, or the provider's structured-output feature.
  - `tool`: a forced `final_result` tool. This is the most portable option and the default when the agent also has tools.
  - `prompted`: today's `fields=`/parser path, a fallback for gateways with neither.
  
  Validation is always pydantic. Failure means a retry with the error, up to `output_retries`.
- **Normalised `Usage`** with `input`, `output`, `cached_input`, `reasoning`, `requests` and `cost_usd`, taken from the provider's real counts. This replaces the 3.5 chars/token estimate for every budget decision.

#### 4.4.3 `llm_step` — the workflow primitive (the callbot's front door)

```python
from operonx_agents import llm_step, Choice

classify = llm_step(
    model=fast,
    system="{analyzer_system_prompt}", user="{intent_prompt}",   # templates, as LLMOp prompt=
    output=Choice(from_input="allowed_intents"),               # enum built per call from a runtime input (R2)
    on_timeout={"value": "fallback", "confidence": 0.0},       # R1: degrade, never raise
    on_invalid={"value": "fallback", "confidence": 0.0},
)

@graph
def turn(...):
    c = classify(analyzer_system_prompt=..., intent_prompt=..., allowed_intents=PARENT["allowed"])
    # outputs: value, confidence (from logprobs when available), usage, latency_ms, error, model_used
```

`llm_step` is an **op factory**, so it lives in graphs exactly where `LLMOp.of` does today. It's built on `Model`, so it gets fallback, deadline and typed validation. The output can be `Choice(...)` (a static or runtime enum), any pydantic model or type (`output=Resolution`), or `str`. It is cancel-safe: it has no side effects, and its outputs are written only on completion, so a superseded call writes nothing (R3, given K5).

#### 4.4.4 Agent and Runner

```python
support = Agent(
    name="support",
    instructions="You resolve order problems for {ctx.deps.brand}.",  # str, or (ctx) -> str
    model=Model("qwen", fallback=["gpt-4o-mini"]),
    tools=[lookup_order, refund, MCPToolset.http("https://crm.internal/mcp", allow=["get_customer"])],
    output_type=Resolution,                     # default str
    limits=UsageLimits(turns=8, tool_calls=20, total_tokens=60_000, cost_usd=0.05, wall_s=90),
    policy=ToolPolicy(default="allow", destructive="ask"),
    hooks=[redact_pii, no_refund_over(500)],
    context=ContextPolicy(compact_at=0.75, keep_recent=6, summarizer=Model("qwen-turbo"),
                          clear_tool_results_after=3),
)

res = await Runner.run(support, "refund order A1B2C3D4", deps=deps,
                       session=RedisSession(key=f"chat:{user_id}"), store=RedisStateStore())
match res.status:
    case "completed":   res.output                     # Resolution
    case "interrupted": res.interruptions              # [ApprovalRequest(call_id, tool, args, reason)]
    case "limit":       res.limit_hit                  # which limit; res.output may hold a best-effort answer
    case "failed":      res.error                      # typed, never a silent {}
res2 = await Runner.resume(res.state_id, approvals={call_id: Approve()}, store=RedisStateStore())
```

**The loop** is about 150 lines and the whole contract:

```
load RunState (new or resumed) ─▶ for turn in range(...):
   check limits (pre)            ─ a projected overrun becomes a final turn with tool_choice="none" (kept from today)
   build context                 ─ session history + compaction + memory + cache marks
   hooks.before_model            ─ guardrails may short-circuit
   model call (child step)       ─ stream deltas → events; usage → RunState
   hooks.after_model
   if output is final → validate → hooks.output guardrails → commit → return completed
   for each tool call: validate args → policy → approval?
        approval needed → persist RunState(pending) → return interrupted
   run approved calls (child steps, concurrency rules) → ToolMessages
   commit turn to RunState (atomic, at the turn boundary) ─▶ next turn
```

- **Commit only at turn boundaries.** `RunState` and `Session` are written after a whole turn: model reply plus all its tool results. That keeps the "history is valid" invariant (`session.py:154-200`) structural instead of checked after the fact, and gives R3 for free: a cancel mid-turn writes nothing.
- **Durability knob** (LangGraph's `exit|async|sync`, §3.5): `durability="turn"` (the default) persists at every turn boundary. `"exit"` persists only on an interrupt or at the end, so a short callbot-style run pays nothing.
- **Crash recovery:** before running non-idempotent tools, the runner persists `RunState.inflight = [call_ids]`. On resume, an inflight call with no result is re-run if `idempotent=True`. Otherwise it's answered with *"interrupted, outcome unknown; check before retrying"*. This is the Temporal/DBOS "journal side effects" idea at turn granularity, with no workflow engine (§3).
- **`Runner.stream(...)`** yields the typed events in §4.4.6 and ends with a `RunResult`. `Runner.run` is `stream` drained.

#### 4.4.5 Composition

- **Agent-as-tool:** `billing.as_tool(name="ask_billing", description=..., input="task", max_turns=5)`. The child runs as a child execution, so it's traced and nested. It returns only its final output, never its transcript (kept from `subagent.py:236-238`). The child's tools are its own (§4.1 principle 6), so the global-registry escape (§1.2 item 1) can't happen. An `approval` inside a child **propagates up** as an interruption of the parent run, with a path. It isn't refused (fixes `subagent.py:225-234`). That works because interruptions are data, not futures.
- **Handoff** (Phase 4): `handoffs=[billing]` adds a `transfer_to_billing` tool. Calling it switches `RunState.active_agent` and continues the same conversation. Input filters (what history the next agent sees) are a plain function.
- **Supervisor / orchestrator–workers** is an agent whose tools are agents. It needs no extra abstraction.
- **Inside a graph:** `support.as_op()` returns an op factory with inputs `input, session_id, deps` and outputs `output, status, usage, interruptions, state_id`, plus a **transient** `event` port (operonx transient ports) for streaming. Workflow patterns (chaining, routing, parallel, evaluator–optimizer) are plain operonx graphs over `llm_step` and `as_op()`.

#### 4.4.6 Events (stream contract)

```
RunStarted(run_id, agent)        TurnStarted(turn)
TextDelta(text)                  ReasoningDelta(text)            # never mixed into the answer
ToolCallStarted(call_id, tool, args)                             # after validation + policy
ToolCallFinished(call_id, tool, ok, result_preview, ms)
ApprovalRequired(call_id, tool, args_redacted, reason)
Handoff(from_agent, to_agent)    Compacted(dropped, summary_tokens)
TurnFinished(turn, usage)        RunFinished(RunResult)
```

Events are a closed union of frozen dataclasses with a `.to_json()`. The serve layer maps them 1:1 onto SSE or WS frames. A voice front end consumes `TextDelta` and segments it into sentences itself (R4). The framework doesn't own TTS.

#### 4.4.7 Context: sessions, compaction, memory

- **`Session`** protocol, taken from the OpenAI SDK shape: `get_items(limit) / add_items / pop_item / clear`. Implementations: `InMemorySession`, `RedisSession` (Redis Cluster is already in the callbot), `SQLSession` (sqlite/postgres). A session stores **committed** items only.
- **Compaction** ports the exchange-safe planner (`compact_ops.py:108-236`) with three changes. It triggers on **real `usage.input_tokens`** of the last call. The summary is **persisted** in the session as a `summary` item, so the next turn doesn't recompute it and the prefix stays stable until the next compaction. Before summarising, it does a cheap **tool-result clearing** pass that replaces old tool outputs with a stub (§3, context editing).
- **Prompt assembly** ports `prompt_ops.py`, with breakpoints on both the system prefix *and* the last committed message (fixes §1.2 item 16).
- **Long-term memory** keeps `MemoryProvider` (prefetch fails soft, write fails loud). It's exposed two ways: prefetch into a per-turn user message (today's placement) and optional `remember` / `recall` tools. No vector store is bundled. `VectorSearchOp` already exists.

#### 4.4.8 Safety

- **`ToolPolicy`** is ported as-is. **Approval** is per call and never inherited (`CONTRIBUTING.md` §4). `approval=Callable` allows argument-dependent rules, for example refunds over 500.
- **Hooks** are one small interface: `before_model(ctx, request)`, `after_model(ctx, response)`, `before_tool(ctx, call)`, `after_tool(ctx, call, result)`, `on_output(ctx, output)`. Each may return a replacement or raise `Tripwire(reason)`. When several hooks answer `before_tool`, verdicts merge as deny > ask > allow (Claude SDK precedence, §3.2), and the reason is sent to the model. The policy check runs *in* this layer, before every call. It never runs in a callback that an allow rule can skip (the documented `canUseTool` footgun, §3.2).
- **Guardrails** are hooks with a name: input guardrails run on the first `before_model`, output guardrails on `on_output`, tool guardrails on `before_tool`. A tripwire ends the run with `status="blocked"` and the reason. There's no separate guardrail runtime.
- **Redaction** is a hook (`after_tool`, plus trace export). It is on by default only for trace export, never for what the tool returns to its own agent (today's caveat, `dispatch.py:745-749`).

#### 4.4.9 Limits and cost

`UsageLimits(turns, tool_calls, input_tokens, output_tokens, total_tokens, cost_usd, wall_s)` is checked before each model call (projected) and after it (actual). `turns` and `tool_calls` exhaustion keep today's graceful final turn with tools disabled (`react.py:17-25`). Token, cost and wall exhaustion end the run with `status="limit"` and the best output so far. Limits are per run. A parent's limits **include** its children's usage (agent-as-tool adds child usage to the parent `RunContext.usage`).

#### 4.4.10 Observability and evals

- Every model call, tool call and child agent is a child `OpExecution` (K1) with attributes named after the OpenTelemetry GenAI conventions (§3): `gen_ai.operation.name` (`chat`, `execute_tool`, `invoke_agent`), `gen_ai.agent.name`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.usage.*`, `gen_ai.response.finish_reasons`. Local, Langfuse and ClickHouse consumers get them for free. An OTel exporter is a later consumer, not a dependency.
- `trace_id` = `run_id`; `session_id` = the session key. One conversation is one Langfuse session (fixes §1.2 item 12).
- **Evals** reuse `app/evals.py` unchanged (an eval is a Job). This package adds trajectory evaluators: `tool_called(name, args=...)`, `tool_not_called`, `no_tool_errors`, `turns_at_most(n)`, `output_valid`, `cost_at_most(usd)`. It also adds `dataset_from_runs(filter)`, which turns production runs into eval cases.

### 4.5 Mapping onto OperonX primitives

| Framework concept | OperonX primitive | Notes |
|---|---|---|
| `@tool` | `@op` factory + `ToolSpec` | Op for tracing and bound routing, spec for the model |
| Tool call execution | child execution of the tool op under the AgentOp's ctx | needs K1 |
| `llm_step` | `@op` factory over `Model` (`BaseLLM` via `ResourceHub`) | replaces `LLMOp.of(fields=…)` for typed calls; `LLMOp` stays for raw/streaming text |
| `Model` resources, fallback | `resources.yaml` `llm:` blocks, `hub.get("llm:x")` | unchanged config surface |
| Agent in a workflow | `Agent.as_op()` → generator `@op` with a transient `event` port | transient ports (shipped) |
| Agent loop | plain `async` code in that op | D1 |
| Run state, sessions | `StateStore`/`Session` resources (`store:redis`, `store:sql`) | not `Checkpointer`, which stays an observer |
| HITL | `RunResult(status="interrupted")` + `Runner.resume` | `InterruptOp` remains for in-graph pauses; not used by the runner |
| Streaming | events on the transient port → `engine.stream(mode="custom")` / serve frames | needs C2 fixed (K6) |
| Tracing / Studio | `OpExecution` tree via ctx; GenAI attributes | needs K1 + K2 |
| Serve | `agent_service()` builds a `Service` (http/ws/webhook door) + a `/resume` route | interruptions return as data |
| Scheduled agents | serve `schedule` trigger (`app/serve/triggers.py:99`) | `Heartbeat` dropped |
| Evals | `app/evals.Eval` (a Job) + trajectory evaluators | no new runtime |
| Config | `operonx.toml` / `resources.yaml` / `Application` | agents declared in Python, not YAML |

### 4.6 Must-have / Later / Avoid

**Must-have (v0.1–v1.0)**

| Feature | Reason |
|---|---|
| Typed tools from signatures, with arg validation + `ModelRetry` | Removes the two-schema tax; models self-correct from named field errors (§3) |
| Per-agent toolsets (no global registry) | Security bug today (§1.2 item 1) |
| `llm_step` with deadline / fallback / runtime `Choice` / logprobs confidence | The callbot's real needs, R1–R2 |
| `Model` with fallback, native/tool/prompted structured output, normalised usage | Every SDK has it; LLMOp's prompted path has open bugs P3–P6 |
| Runner: turns budget with a graceful final turn, parallel tool calls, exactly-one-tool-message | Kept invariants |
| `RunState` + `StateStore` + resume; HITL as interruption data | Approvals must survive restarts; the same mechanism gives crash recovery |
| `UsageLimits` incl. cost and wall clock | Runaway cost is the #1 production incident class (§3) |
| Sessions (memory/redis/sql) + persisted compaction | Multi-turn chat; the callbot already runs Redis |
| Hooks (guardrails as named hooks) + redaction | One extension point instead of three subsystems |
| Agent-as-tool with nested usage, limits and approvals | The only multi-agent pattern with consistent production evidence (§3) |
| Typed event stream | Required for any UI and for voice TTFA |
| Child-step tracing with GenAI attributes | Without it D1 is a regression |
| MCP: stdio + streamable HTTP (incl. the stateless 2026-07-28 revision), per-agent `MCPToolset`, annotation-based gating | Remote MCP is the norm, and the spec dropped sessions (§3.7) |

**Later (when a concrete consumer asks)**

| Feature | Trigger |
|---|---|
| Handoffs (`transfer_to_x`) | A second conversational agent sharing one session |
| OTel exporter consumer | A team using an OTel backend |
| Skills with progressive disclosure (`load_skill` tool) | More than ~5 procedures per agent |
| Memory tools + Letta-style editable memory blocks | Cross-session personalisation |
| A2A server/client | An external agent we must interoperate with |
| MCP elicitation / sampling / resources / prompts | A server that needs them |
| Durable-engine adapters (Temporal/DBOS) | Runs longer than a deploy cycle with expensive non-idempotent steps |
| Prompt optimisation (DSPy-style) over eval datasets | An eval suite with enough cases to optimise against |
| Realtime speech-to-speech model adapter | A voice project choosing S2S over STT→LLM→TTS |

**Avoid (with reasons)**

| Anti-feature | Why |
|---|---|
| Agent base classes / subclassing | god-class failure mode (`AGENT_EXTENSION_PLAN.md:815-824`) |
| Process-global tool registry | §1.2 item 1 |
| Role/backstory "crews", group chat, swarms | Unpredictable cost, hard to test; agent-as-tool covers delegation (§3) |
| Framework-owned planning module (plan-and-execute) | A plan is just output_type + a workflow graph; models plan natively |
| Bundled vector DB / RAG inside the agent layer | `VectorSearchOp` exists; memory stays the small layer (`memory.py:7-13`) |
| Code-executing agents without a sandbox | Security surface; out of scope |
| In-process approval futures with long timeouts | §1.2 item 5 |
| Per-turn keyword skill injection into history | Cache churn and wrong-skill risk; progressive disclosure later |
| Heartbeat timer | Duplicates the serve `schedule` trigger |
| YAML-defined agents | Config-as-code drifts; the callbot's YAML FSM is a product decision, not a framework feature |
| Auto-written long-term memory without review | Self-poisoning memory (§3) |
| Retry wrappers that cross the transport / validation / fallback streams | Rule 4, kept |

### 4.7 What operonx core must change

| # | Change | Why the agent layer can't do it | Size |
|---|---|---|---|
| **K1** | **Child executions**: a public `await invoke(op_factory, **inputs)` (or `ctx.step(name, fn)`) inside an op that records an `OpExecution` with ctx `parent_ctx + (name[i],)`, inputs/outputs/status/timing, honouring `exclude=` | Trace structure is core; today an op can't add nodes (`core/workflow_trace.py:107-170` has no parent/child API) | M |
| **K2** | `OpExecution.attrs: dict` for semantic attributes, propagated by the Local/Langfuse/ClickHouse consumers | GenAI attributes, agent/tool names, usage | S |
| **K3** | **Op deadline**: `@op(timeout=s, on_timeout=outputs_dict \| "raise")`, enforced by the scheduler with cancellation | R1; nothing in `core/ops/base.py` bounds an op | S–M |
| **K4** | **Errors that raise**: `Operon(..., errors="raise")` / `engine.run(raise_errors=True)` and a typed `OpFailed` carrying op + traceback; fix S1's dead handlers | Today's silent `{}` (`04-gotchas.md:7-41`) is the root of 4+ workarounds | M |
| **K5** | **Cancel-safe writes**: guarantee that a cancelled op (`handle.cancel()` or a superseded ctx) writes no outputs and no reducer merges, with a test | R3 | S (verify, then fix) |
| **K6** | Fix C2 (`stream(updates\|custom)` swallows fatal errors) and C3 (`__interrupt__` leaks into results) | The event stream is the agent's API | S |
| **K7** | Providers: one normalised tool-call shape (`{id, name, args: dict}`) across openai/anthropic/gemini; native structured output (`json_schema` / Anthropic tool-forcing / Gemini `response_schema`); per-resource `timeout`; `usage.cached_input`/`reasoning`; logprobs passthrough | `call_identity` juggling two shapes (`dispatch.py:714-719`) is a symptom; R1/R2 | M |
| **K8** | Serve: a route for `POST /<door>/resume` and SSE framing of transient-port events | Interruptions must reach HTTP clients | S |
| K9 | (Optional) `ResourceHub` `store:` category (redis/sql/memory) shared by sessions, state and caches | Avoid each project hand-rolling Redis wiring | S |

K1, K3, K4 and K7 are the load-bearing ones. Each passes the Footprint Ladder's rung-6 test: none can be done from an op (rung 1) because each changes what the scheduler, tracer or provider contract guarantees.
## 5 · Phased roadmap

Each phase ends with something usable, ships behind its own tests, and is one branch (project memory: one branch per phase, batch commits). "Live" tests run against `qwen3.7-plus` (the only in-house model verified to tool-call, `AGENT_EXTENSION_PLAN.md:67-84`) and are paired with offline tests, per `agents/CONTRIBUTING.md` §5. Every phase includes **at least one run against a real model**: four bugs in the current layer hid behind scripted doubles until a live run (`AGENT_EXTENSION_PLAN.md:115-121`).

### Phase 0 — Evidence spike (3–4 days, no package yet)

Answer the two questions D1 rests on, and write `docs/AGENTS_V2_PLAN.md` before any implementation (project memory: plan doc before a big refactor).

| Measurement | Method | Pass criterion |
|---|---|---|
| Per-turn framework overhead: back-edge `build_react_agent` vs a 150-line in-op loop | scripted model (0 ms), 1 and 3 tool calls per turn, 10 turns, 200 runs; `perf_counter` around `run()`; event-loop lag probe at 5/10/20 concurrent runs | In-op loop ≤ graph loop; < 2 ms/turn at 5 concurrent |
| Trace fidelity | prototype K1 on a branch of operonx; render both in Studio's tree/workflow views | Model, tool and child-agent nodes visible with inputs/outputs; screenshot desktop and phone (memory: UI work → screenshots) |
| `llm_step` deadline against the `inhouse` gateway | 200 classifier calls, deadline 0.9 s, measure p50/p95 and fallback rate; control = current `LLMOp` path | p95 within 50 ms of control; deadline honoured within 20 ms |
| Native structured output support | probe `inhouse`, `qwen3.7-plus`, `qwen-turbo` for `response_format: json_schema` and forced tool choice, with a control request that must fail | A table of which strategy each endpoint supports |

The benchmark must measure the shape being decided: an agent loop with tools, not a straight-line graph (project memory: "check the benchmark measures your shape"). **Exit:** the user approves or rejects D1 with the numbers in hand.

### Phase 1 — operonx core prerequisites (1–1.5 weeks, in the operonx repo)

K1 child executions, K2 attrs, K3 op deadline, K4 raise mode, K6 C2/C3 fixes, K5 verification. One operonx minor release.

Tests:
- K1: a nested invoke inside a generator op and inside a `.parallel()` fan-out produces the right ctx tree. `exclude=` is honoured. A cancelled child shows `STATUS_CANCELLED`. Consumers (local, Langfuse, ClickHouse) round-trip children and attrs.
- K3: an op exceeding `timeout` is cancelled, emits `on_timeout` outputs and its downstream runs. `"raise"` lands in `errors` / raises under K4. The wall-clock tolerance is asserted.
- K4: `ValueError` in an op is raised from `run(raise_errors=True)` as `OpFailed` with the op name. The default behaviour is unchanged (compat).
- K5: cancel while a reducer write is pending, then assert no write event reaches a checkpointer.
- K6: the C2 and C3 repros from `docs/design/repros/` become regression tests.

### Phase 2 — Tools, Model, `llm_step` (1.5 weeks; repo `operonx-agents` created)

`tools/tool.py`, `toolset.py`, `dispatch.py`, `policy.py` (port), `model/model.py`, `output.py`, `step.py`, plus K7 provider work in operonx (normalised tool calls, native structured output, per-resource timeout, logprobs).

Tests:
- Schema generation across 25 signature shapes (Optional, Literal, Enum, nested pydantic, `Annotated[Field]`, defaults, docstring styles). The generated schema must be accepted by the OpenAI and Anthropic validators, checked live once.
- Argument validation errors become a tool message naming the field. `ModelRetry` round-trips. A timeout, an unknown tool or a denied tool each produce exactly one message (ported from `tests/internal/agents/test_dispatch*.py`).
- `sequential` tools never overlap (an assertion based on recorded timestamps).
- `Model`: fallback on 5xx and on refusal, no fallback after the first delta, the deadline covers the whole chain, and usage is normalised for the openai and anthropic adapters (recorded fixtures).
- Output strategies: native, tool and prompted each validate the same pydantic model, and an invalid answer is re-asked with the error at most `output_retries` times.
- `llm_step` `Choice(from_input=…)` rejects out-of-set labels. `on_timeout` emits the value. **Callbot shadow test:** replay 200 recorded turns from ClickHouse through `llm_step` and through today's `LLMOp`, and diff the intents (gate: ≥ 99% agreement, p95 latency no worse).

### Phase 3 — Runner, RunState, limits, events, sessions (2 weeks)

`run/*`, `context/session.py`, `context/prompt.py` (port), `context/compaction.py` (port + persisted summary + tool-result clearing), `StateStore` (memory, redis).

Tests:
- Loop invariants ported from `test_react.py` / `test_session.py`: a graceful final turn at `turns`, no unanswered tool call ever stored, a history committed only when valid, and a failed model call leaving the session unchanged.
- Limits: each of the seven limits trips correctly, and child usage counts toward the parent.
- Crash recovery: kill the process (subprocess + `SIGKILL`) between "inflight persisted" and "tool finished". On resume, an idempotent tool re-runs and a non-idempotent one gets the "outcome unknown" message.
- Cancel mid-turn writes nothing to the session or the state store (R3).
- The event stream is complete and ordered (property test over scripted models). `stream()` and `run()` give the same `RunResult`.
- Compaction triggers on real usage, the summary persists, and the cache prefix is stable across turns between compactions (`prefix_is_stable`, ported).
- Live: a 3-tool agent on `qwen3.7-plus`, and a 20-turn session over Redis.

### Phase 4 — Approvals, composition, safety (1.5 weeks)

Approvals as interruptions plus `Runner.resume`, `as_tool`, `as_op`, handoffs, hooks/guardrails, redaction (port), MCP toolset (port + streamable HTTP).

Tests:
- An approval survives a process restart (persist, new process, resume). A denial, an expiry, a "deny ≠ ask" case and argument-dependent approval all behave correctly.
- A child agent's approval surfaces as a parent interruption with a path, and resuming completes both.
- Isolation: a model naming a tool outside its agent's toolset gets "unknown tool", even when another agent in the process owns that tool (the regression for §1.2 item 1).
- Hooks: a tripwire ends the run `blocked`, and a replacement in `before_tool` is honoured.
- MCP: the existing 33 stdio tests are ported, plus streamable HTTP against the reference server, and the annotation gating is kept.
- `as_op` inside a graph: transient events reach `engine.stream(mode="custom")`, and outputs bind downstream.

### Phase 5 — Serve, Studio, evals, migration (1.5 weeks)

`serve.py` (`agent_service`, SSE/WS events, `/resume` via K8), `evals.py` trajectory evaluators, `dataset_from_runs`, the Studio check that the run tree renders, an `operonx init --template agent` update, and docs (a guide page with tested snippets, following `operonx/guide/` practice). `operonx.agents` gets deprecation shims.

Tests:
- `agent_service` end to end over HTTP and WS with an approval round-trip.
- An eval over a 20-case dataset with trajectory evaluators, gated in CI.
- Every guide snippet executes (as `tests/guide` already does).
- Studio screenshots, desktop and phone.

### Phase 6 — Callbot adoption (on `refactor/operonx-studio` only; memory: never toward staging)

Replace `llm_classify` with `llm_step(Choice(from_input="allowed_intents"), deadline, on_timeout)`. Delete `merge_intent`'s allow-list and the invented confidence. Add redaction for logs and traces. Gate on the shadow-replay diff from Phase 2 and a 5-CCU load test (TTFA p50 not worse than 0.86 s, `docs/CAPACITY_2026-09-29.md`).

**Total:** about 9–10 weeks of focused work. Phases 1–3 deliver a usable chat agent. Phase 2 alone delivers the callbot's real win.
## 6 · Invariants carried forward (tests ported, not rewritten)

1. Every tool call produces exactly one tool message, whatever the outcome. Source: `agents/graphs/dispatch.py:612-623`; tests `tests/internal/agents/test_dispatch*.py`.
2. The stored history never holds an unanswered `tool_call`. Calls the loop abandons are answered "not run". Source: `react.py:295-333`.
3. The final budgeted turn runs with `tool_choice="none"`, so exhaustion exits the way success does. Source: `react.py:17-25`, `model_ops.py:77-86`.
4. Deny is not ask. A policy refusal never reaches a human. Source: `policy.py:13-16`.
5. Fail closed on permission, fail open on enrichment (memory, skills). Source: `CONTRIBUTING.md` §4, `memory.py:368-376`.
6. Redaction runs before truncation. Approval payloads are redacted, but tool arguments are not. Source: `dispatch.py:745-749`, `:877-894`.
7. Compaction moves whole exchanges. System messages are pinned. Source: `compact_ops.py:108-236`.
8. The system prompt is byte-stable, and per-turn context goes after the history. Source: `prompt_ops.py:1-24`.
9. MCP tools are namespaced and gated unless they declare `readOnlyHint`. Third-party descriptions are truncated. Source: `mcp.py:52-60`, `:546-562`.
10. A sub-agent returns text, not its transcript. Source: `subagent.py:236-238`.

## 7 · Risks

| Risk | Mitigation |
|---|---|
| D1 loses Studio's loop picture | K1 plus a Phase 0 screenshot gate. Fallback: keep the graph loop and fix its workarounds in core. |
| Native structured output unsupported on in-house gateways | `tool` and `prompted` strategies; Phase 0 probe table |
| Two agent APIs coexist during migration | Shims for one release, a dated removal, and a `MIGRATION.md` section |
| Scope creep toward a platform | The Avoid list (§4.6) and a ≤ 3.5 kLOC budget, enforced in review |
| Live-model-only bugs (the history of this codebase) | Every phase has a live test against `qwen3.7-plus`, plus the callbot shadow replay |
