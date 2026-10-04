# operonx-agents

Typed LLM steps and agents on the [operonx](../Operon) workflow engine.
Design: `Operon/docs/roadmap/track3_agents.md` §4; plan and phases:
`Operon/docs/AGENTS_V2_PLAN.md`.

Two front doors over one model layer:

- **`llm_step`**: a typed, deadline-bounded model call as a graph op. The
  graph owns control flow (the callbot's front door).
- **`Agent` + `Runner`**: a model-driven tool loop in one op, with child
  executions in the trace (phase A3).

Phase A2 shipped tools, the model layer, `llm_step` and
`operonx-agents probe`; phase A3 the `Runner`, `RunState`, `UsageLimits`,
the event stream, sessions, state stores and compaction; phase A4
approvals as interruptions, `as_tool` / `as_op`, hooks, redaction and MCP
over stdio and streamable HTTP; phase A5 (this state) `agent_service`
(HTTP JSON / server-sent events / websocket, approvals on `/resume`),
trajectory evaluators, `dataset_from_runs` and `operonx_agents.testing`.
The guide page with tested snippets is operonx's `operonx/guide/09-agents.md`.

```python
from operonx_agents import Choice, Model, ModelSettings, llm_step

classify = llm_step(
    model=Model("inhouse", deadline=0.9, settings=ModelSettings(logprobs=True)),
    system="{analyzer_system_prompt}",
    user="{intent_prompt}",
    output=Choice(from_input="allowed_intents", field="intent"),
    on_timeout="fallback",
    on_invalid="fallback",
)
# inside a @graph: c = classify(analyzer_system_prompt=..., intent_prompt=..., allowed_intents=...)
# outputs: value, confidence, outcome, error, usage, latency_ms, model_used
```

```python
from operonx_agents import Agent, Model, RedisSession, RedisStateStore, Runner, UsageLimits, tool


@tool(readonly=True)
async def order_status(order_id: str) -> str:
    """The shipping status of an order."""
    ...


support = Agent(
    name="support",
    model=Model("qwen3.7-plus", deadline=90),
    instructions="You answer customer-support questions. Use the tools.",
    tools=[order_status],
    limits=UsageLimits(turns=8, tool_calls=20, total_tokens=60_000, wall_s=90),
)
session = RedisSession("chat:42", url="redis://localhost:6379/0")
res = await Runner.run(
    support,
    "Has A1B2C3D4 shipped?",
    session=session,
    store=RedisStateStore(url="redis://localhost:6379/0"),
)
# res.status: completed | limit | failed; res.output, res.usage, res.run_id
async for event in Runner.stream(support, "And when?", session=session):
    ...
# RunStarted, TurnStarted, TextDelta, ToolCallStarted/Finished, TurnFinished, RunFinished
```

```python
from operonx_agents import (
    Agent,
    Approve,
    MCPServer,
    MCPToolset,
    Model,
    RedactToolOutput,
    Runner,
    SQLiteStateStore,
    tool,
)


@tool(idempotent=False, approval=lambda ctx, args: args["amount"] > 500)
async def refund(order_id: str, amount: int) -> str:
    """Refund an order (over 500 needs a human)."""
    ...


billing = Agent(name="billing", model=Model("qwen3.7-plus"), tools=[refund])
crm = await MCPToolset.connect(
    MCPServer("crm", url="https://crm.internal/mcp"), allow=["get_customer"]
)
support = Agent(
    name="support",
    model=Model("qwen3.7-plus"),
    tools=[order_status, crm, billing.as_tool(name="ask_billing")],
    hooks=[RedactToolOutput()],  # Agent(redact=...) already scrubs traces
)
store = SQLiteStateStore("runs.db")
res = await Runner.run(support, "refund 900 on A1B2C3D4", store=store)
# res.status == "interrupted"; res.interruptions[0].path == ("support", "billing")
res = await Runner.resume(
    support,
    res.run_id,
    store=store,  # any process, any time
    approvals={i.id: Approve() for i in res.interruptions},
)
```

```python
from operonx.app import Application, http, websocket
from operonx_agents import agent_service

APP = Application("shop", services=[
    agent_service(support, http("POST", "/support"), store=store),   # + POST /support/resume
    agent_service(support, websocket("/support/ws"), store=store, max_inflight=64,
                  name="support_ws"),
])
# POST /support {"input": "..."}            -> {"status", "output", "run_id", "interruptions", ...}
#   Accept: text/event-stream               -> one SSE frame per event, the last RunFinished
# POST /support/resume {"run_id", "approvals": {"<id>": "approve" | "deny" | {"deny": "why"}}}
```

## Layout

| module | what |
|---|---|
| `tools/tool.py` | `@tool`: schema and validation from the signature and docstring; `ToolSpec` |
| `tools/toolset.py` | `Toolset`: the tools one agent owns, the only ones its dispatch runs |
| `tools/dispatch.py` | one tool message per call; concurrency rules; child executions |
| `tools/policy.py` | `ToolPolicy`, ported from `operonx.agents` |
| `model/model.py` | `Model`: fallback, a deadline over the chain, transport retry, `Usage` |
| `model/output.py` | `Choice`, the `native` / `tool` / `prompted` strategies, validation and re-asks, logprobs confidence |
| `step.py` | `llm_step` |
| `probe.py`, `cli.py` | `operonx-agents probe <resource>` |
| `agent.py` | `Agent`: the spec, a frozen dataclass |
| `run/runner.py` | `Runner.run` / `.stream` / `.resume` / `.resume_stream`: the loop |
| `run/state.py`, `run/store.py` | `RunState` (versioned JSON, the crash journal); `StateStore`: memory, Redis, SQLite |
| `run/limits.py` | `UsageLimits` (the seven caps) and the meter a child run adds to |
| `run/events.py`, `run/result.py` | the event stream; `RunResult` |
| `context/session.py` | `Session`: memory, Redis, SQLite; committed turns only |
| `context/compaction.py` | `ContextPolicy`; the ported exchange-safe planner, persisted summaries, tool-result clearing |
| `context/prompt.py` | cache-stable assembly and breakpoints, `prefix_is_stable` (ported) |
| `run/interruption.py` | `Interruption`, `Approve`, `Deny`: approvals as data, ids from operonx's `invocation_key` |
| `safety/hooks.py` | `Hooks` (before/after model and tool, on_output), `Ask`, `Tripwire` → `blocked` |
| `safety/redact.py` | `Redactor` (ported), `RunRedaction` (a run's records' export-time `redact`), `RedactToolOutput` |
| `compose.py` | `Agent.as_tool` (a child run per call) and `Agent.as_op` (`AgentOp`) |
| `tools/mcp.py` | `MCPServer`, `MCPClient`, `MCPToolset`: stdio and streamable HTTP (ported) |
| `serve.py` | `agent_service`: an agent as an operonx `Service` (http: JSON or SSE, `POST <path>/resume`; websocket) |
| `evals.py` | `tool_called`, `tool_not_called`, `no_tool_errors`, `turns_at_most`, `output_valid`, `cost_at_most`, `dataset_from_runs` |
| `testing.py` | `ScriptedLLM`, `asks`, `says`, `scripted(...)`: a project's own offline tests |

## Structured output is declared, never guessed

Each `llm:` resource declares `structured_output: native | tool | prompted`
(operonx K7; default `prompted`). Measure it:

```bash
operonx-agents probe inhouse qwen3.7-plus --resources resources.yaml --env .env
```

## Development

```bash
uv sync
uv run pytest -m "not live"          # offline
uv run pytest -m live                # real gateways, credentials from the callbot's .env
REDIS_URL=redis://127.0.0.1:6391/0 uv run pytest   # also the Redis session/store/crash tests
uv sync --extra mcp                  # MCPToolset; tests/test_mcp_reference.py also needs npx
```

operonx is an editable path dependency on `../Operon`. That relative path
is why this repo is not worked on from git worktrees. The checkout at
`../Operon` must contain `child()`'s `redact` (operonx main `10fe40a`, #90, or later); to test
against another checkout, put it first on `PYTHONPATH`.

## A2 gate (2026-10-04)

| bullet | test |
|---|---|
| schema from 25 signature shapes, accepted by OpenAI- and Anthropic-shaped validators | `tests/test_tool_schema.py` (2020-12 metaschema and OpenAI parameter shape, 25 × 4); live: `tests/live/test_live_models.py::test_the_25_schemas_are_accepted_by_openai` (api.openai.com, gpt-4o-mini) and `…_by_an_openai_shaped_gateway` (qwen3.7-plus) |
| an argument error is one tool message naming the field | `tests/test_dispatch.py::TestOneMessagePerCall::test_argument_error_names_the_field`, `test_missing_argument_names_the_field` |
| `ModelRetry` round-trips | `…::test_model_retry_round_trips` |
| timeout, unknown tool, denied tool: exactly one message each (ported) | `…::test_timeout`, `test_unknown_tool`, `test_denied_tool_never_runs`, `test_every_outcome_in_one_turn_answers_its_own_id` |
| sequential tools never overlap (recorded timestamps) | `tests/test_dispatch.py::TestConcurrency::test_sequential_tools_never_overlap` |
| per-agent toolsets: another agent's tool is "unknown tool" | `tests/test_dispatch.py::TestIsolation::test_a_tool_another_agent_owns_is_unknown` |
| `ToolPolicy` ported | `tests/test_policy.py` (resolution order, validation, default) |
| `Model` fallback on 5xx | `tests/test_model.py::TestFallback::test_on_5xx` (+ transport retry first, 4xx not retried) |
| fallback on refusal | `…::test_on_refusal`, `test_refusal_field_counts_as_refusal`, `test_all_refuse` |
| no fallback after the first delta | `tests/test_model.py::TestStreaming::test_no_fallback_after_the_first_delta` |
| the deadline covers the chain | `tests/test_model.py::TestDeadline::test_covers_the_whole_chain` |
| `Usage` normalised from recorded fixtures | `tests/test_model.py::TestUsage` (bodies recorded from inhouse and qwen3.7-plus; the Anthropic body is the documented shape through operonx's Anthropic backend: no Anthropic endpoint is reachable here) |
| each strategy validates the same pydantic model, re-asks ≤ `output_retries` | `tests/test_output.py::TestEachStrategy` (native, tool, prompted × validate, re-ask, 0/1/2 retries) |
| a `tool`-mode out-of-enum argument is caught | `tests/test_output.py::TestQwenToolCase::test_out_of_enum_tool_argument_is_caught` |
| `llm_step`: out-of-set label → `on_invalid` | `tests/test_step.py::test_out_of_set_label_goes_to_on_invalid` |
| slow model → `on_timeout` within 20 ms | `tests/test_step.py::test_slow_model_goes_to_on_timeout_within_20ms` (also on Python 3.10, the backport) |
| live: inhouse native in-enum under an adversarial prompt | `tests/live/test_live_models.py::test_inhouse_native_answers_in_enum_under_an_adversarial_prompt` |
| live: `probe` reproduces §2c | `…::test_probe_reproduces_the_a0_table[inhouse,qwen3.7-plus,qwen-turbo]`; CLI output in `results/probe_2026-10-04.txt` |
| live: a tool-calling turn on qwen3.7-plus | `…::test_a_tool_calling_turn_on_qwen` |
| callbot shadow replay, 200 recorded turns | **not met as written**: see below |

**Shadow replay** (`scripts/shadow_replay.py`, `results/shadow_replay.json`).
ClickHouse `callbot_traces.nodes` holds **58** recorded `llm_classify` turns
(35 calls, 2026-10-03 17:26–18:24, 7 distinct utterances), not 200. The STT
log has transcripts but no call id, state or allow-list, so turns cannot
be rebuilt from it without inventing context. All 58 were replayed, 4
passes, through the callbot's `LLMOp` path and the A6-shaped `llm_step`:

| | pairs | agreement | control p50 / p95 | step p50 / p95 |
|---|---|---|---|---|
| first pass (each recorded turn once) | 58 | 58/58 = 100% | 86.8 / 103.1 ms | 88.9 / 105.9 ms |
| 4 passes | 232 | 232/232 = 100% | 86.6 / 103.8 ms | 88.7 / 105.9 ms |

The step's p95 is 2.1–2.8 ms above control's. `results/shadow_latency_decomposition.txt`
attributes all of it to `logprobs=True`: without it, the step's p95 is the
control's (107.4 vs 107.3 ms over 174 pairs).

## A3 gate (2026-10-04)

A3 needed one core change: **operonx #86** (`99b1634`), `child(current=False)`.
`Model.stream` keeps its child record open across its yields, and the consumer's code
runs between them in the same context: with the default scope the consumer's own
steps nested under the stream (`('main', 'model[0]', 'emit[0]')`), and an abandoned
stream left every later step of the op under a dead record. Tests:
`operonx tests/internal/core/test_child_executions.py::test_child_held_across_yields_is_a_sibling_of_the_consumers_steps`
and `…::test_abandoned_stream_leaves_the_op_current_and_is_marked_cancelled`.

| plan bullet (AGENTS_V2_PLAN §A3) | test |
|---|---|
| loop invariants ported from `test_react.py` | `tests/test_runner.py::TestTurns`, `TestBudget` (graceful last turn, `tool_choice="none"`, tools still sent), `TestBudgetNeverStrandsACall`, `TestFailuresReachTheModel`, `TestTruncatedAnswer` |
| loop invariants ported from `test_session.py` | `tests/test_runner.py::TestFailedTurns` (a failed model call leaves the session unchanged; a retry works), `TestBudgetExhaustionInASession` (a provider-strict model accepts the next run), `TestSessions` (no duplication, system prompt first and once, never stored, prefix byte-stable) |
| each of the seven limits trips | `tests/test_limits.py::test_turns`, `test_tool_calls`, `test_input_tokens_before_the_call` / `_after_the_call`, `test_output_tokens`, `test_total_tokens`, `test_cost_usd` (+ unpriced resource fails loudly), `test_wall_s_cuts_the_turn_and_writes_nothing_for_it`, `test_a_spent_wall_ends_before_the_next_call` |
| child usage counts toward the parent | `tests/test_limits.py::test_child_usage_counts_toward_the_parent` (parent 13 + child 26 = 39 tokens; the parent's 40-token cap stops its next call, which 13 + 10 alone would have allowed) |
| SIGKILL between "in flight persisted" and "tool finished" | `tests/test_crash_recovery.py::test_sigkill_mid_turn_then_resume[sqlite,redis]` |
| cancel mid-turn writes nothing | `tests/test_cancel.py` (×memory, ×SQLite): during the first model call, during a later turn (state and session byte-equal to after the last commit), during an idempotent tool; a non-idempotent tool leaves only its journal (and resumes "outcome unknown"); a cancel during the commit lets the whole turn land |
| event stream complete and ordered; `stream()` == `run()` | `tests/test_events.py` (hypothesis, 150 scripted conversations: good/bad-argument/failing/unknown calls, model errors, turn/tool-call/token caps) |
| compaction triggers on real usage, summary persists | `tests/test_compaction.py::TestTrigger` (800 ≥ 0.75 × 1000 compacts; 100 000 characters counted as 50 tokens do not), `TestPersistence` (summary item in the session, the next run does not summarise again, `prefix_is_stable` across runs, view == run's messages, re-summarised on the second compaction, tool-result clearing), ported planner tests `TestPairingIsPreserved` |
| storage backends | `tests/test_storage.py` (session and store contract × memory, SQLite, Redis) |

**SIGKILL.** A subprocess runs one turn calling `lookup` (idempotent), `charge` (not
idempotent) and `note` (idempotent) concurrently; `lookup` and `charge` block, `note`
finishes. Once the store holds `pending.inflight == ['c_charge', 'c_lookup']` and the
tools' start log shows all three, the test sends SIGKILL (return code −9). Then: the state
is `running`, `turn 0`, `pending.results == ['c_note']`, the session is empty. `Runner.resume`
in the test process re-runs `lookup` (start log `lookup` ×2), does not run `charge`
(start log `charge` ×1) and answers it `Interrupted: 'charge' was running when the run
stopped, and its outcome is unknown. …`, keeps `note`'s journaled result (`note` ×1),
commits user, assistant, 3 tool messages and the final answer. Passes on SQLite and on
Redis (throwaway `redis:7-alpine`).

**Seven limits** (each scripted reply spends 10 prompt + 3 completion tokens):
`turns=2` → `limit/turns` after 2 calls, the last with `tool_choice="none"`;
`tool_calls=2` with 3 calls in one turn → 2 run, the 3rd "not run", next turn last →
`limit/tool_calls` with its answer; `input_tokens=25` → 2 calls (20), stopped before a
third; one 40-token prompt → stopped after the call, its calls answered;
`output_tokens=5` → 6 after the 2nd call; `total_tokens=30` → 26, stopped before a
third; `cost_usd=0.03` → 0.032 after the 2nd; `wall_s=0.2` with a 5 s tool → `limit/wall_s`
in 0.2–0.3 s, nothing written, tool cancelled.

**Overhead** (`scripts/bench_overhead.py` → `results/overhead_a3.{log,json}`): the A0
bench re-pointed at `Runner`: 0 ms scripted model streaming one chunk per text and per
tool call, 10 turns, readonly tool, 200 runs per cell after 20 warm-ups, tracing on (every
run checked: 30 / 48 trace records = 1 op + 10 turn + 10 model + 9 × calls tool). Same
machine, same window, the A1 spike control (`inop+children`) for scale (A1 recorded it at
1.01 / 1.41 ms; the machine reads 9–20% slower today):

| at 5 concurrent | calls/turn | latency/turn p50 / p95 | CPU ms/turn | event-loop lag p99 |
|---|---|---|---|---|
| **`Runner.run` in an op** | 1 | **1.18 / 1.25** | 0.24 | 3.2 ms |
| **`Runner.run` in an op** | 3 | **1.85 / 1.96** | 0.39 | 1.8 ms |
| `Runner.stream` in an op, events drained | 1 | 1.49 / 1.81 | 0.30 | 4.8 ms |
| `Runner.stream` in an op, events drained | 3 | 1.96 / 2.04 | 0.41 | 1.7 ms |
| spike `inop+children` (control) | 1 | 1.10 / 1.18 | 0.22 | 1.7 ms |
| spike `inop+children` (control) | 3 | 1.69 / 1.86 | 0.35 | 1.8 ms |

Gate < 2 ms/turn at 5 concurrent: **passes** (1.18 / 1.85 ms; streamed 1.49 / 1.96 ms,
which is close). The Runner costs ~0.04 ms/turn more than the spike's bare loop for the
work the spike skipped: parsing a provider-shaped stream, validating arguments, limits,
usage. Measured on the way (first cut 2.16 / 3.03 ms): a concurrent batch's tasks
(23 vs 34 µs per 3 calls on bare asyncio; the first call now runs in the dispatching task),
an `AttributeError` per missing pydantic field in usage/stream parsing, pydantic
`__setattr__` on the per-turn state (now a dataclass), events built with no listener.
One yield to the loop per turn is deliberate: without it a model that answers with no I/O
held the loop for the whole run (lag p99 12–77 ms at 1 call).

**Live** (`results/live_a3.txt`): a 3-tool agent on `qwen3.7-plus`, streamed inside an op,
calls all three tools and answers from them (3 turns, ~8 s, ~1.9k input tokens of which 512
cached: the stable prefix pays); a 20-run conversation over `RedisSession` +
`RedisStateStore` on a throwaway server: 40 items, every run `completed`, run 20 recalls
run 1's fact ("Pelican").

**Not done here:** a SQL session/store other than SQLite (Postgres), a `store:` ResourceHub
category (K9), approvals/hooks/as_tool/as_op (A4). `Runner.run` streams the model like
`stream()` does: a plain-request path for `run()` was tried and dropped, because the
property test caught its errors reading differently from the stream's (and it bought no
measurable time).

## A4 gate (2026-10-04)

**The gate (AGENTS_V2_PLAN §4): an approval survives a process restart; a model naming
another agent's tool gets "unknown tool".** Both hold:
`tests/test_approvals.py::test_an_approval_survives_a_process_restart[sqlite,redis]` and
`tests/test_compose.py::TestAgentAsTool::test_isolation_a_tool_another_agent_owns_is_unknown`.

**Approvals are interruptions, with R2's ids.** A call that needs a human does not wait in
the process: the turn is parked in the `RunState` (`pending`: the calls that finished, the
`Interruption`s), the run ends `interrupted`, and `Runner.resume(..., approvals={id:
Approve() | Deny(reason)})` finishes the turn in any process. An interruption's id is
operonx's `invocation_key(run_id, "<agent>.<tool>", ("turn[n]", call_id))`, the rule
`InterruptOp` uses since R2, so a resume elsewhere finds it and a re-park keeps it.
`InterruptOp` itself is not used: it awaits an in-process future, which a restart loses
(track3 §4.5: "InterruptOp remains for in-graph pauses; not used by the runner").

| plan bullet (AGENTS_V2_PLAN §A4) | test |
|---|---|
| an approval survives a restart: persist, new process, `Runner.resume` | `tests/test_approvals.py::test_an_approval_survives_a_process_restart[sqlite,redis]`: a subprocess runs until `refund(900)` parks and exits (result in a file); the test process checks the id is `interruption_id(run, "cashier", "refund", 1, "c_refund")`, the args a human sees are redacted, nothing ran; resumes with `Approve()`; the refund runs once, in the test's pid |
| deny | `TestDecisions::test_deny_refuses_the_call_with_the_reason` (the model reads the refusal and the human's reason) |
| expiry | `…::test_an_expired_approval_is_refused_even_if_approved_later`, `…_with_no_answer_is_refused` (`Agent(approval_ttl=)`) |
| deny ≠ ask | `…::test_deny_is_not_ask_a_policy_refusal_never_reaches_a_human` (an `approval="always"` tool the policy denies: refused, no interruption); `tests/test_hooks.py::TestBeforeTool::test_deny_is_a_refusal_the_model_reads_never_a_question` |
| argument-dependent approval | `…::test_argument_dependent_approval` (100 runs, 900 parks; resume runs 900 and not 100 again), `…::test_a_waiting_call_holds_the_sequential_calls_after_it` |
| (also) partial answers, unknown ids, no store, `durability="exit"`, the stream | `…::test_an_unanswered_interruption_keeps_waiting_with_its_id`, `test_answers_for_ids_the_run_does_not_wait_on_are_refused`, `test_without_a_store_the_call_is_refused`, `test_durability_exit_still_saves_an_interrupted_run`, `test_the_stream_says_what_waits` |
| a child agent's approval surfaces on the parent with a path; resuming completes both | `tests/test_compose.py::TestAgentAsTool::test_a_childs_approval_surfaces_on_the_parent_and_one_resume_finishes_both`: path `("support", "billing")`; the child's run (id `child_run_id(...)`) is `interrupted` in the store; one parent resume runs the refund once, the child and the parent complete, neither model asked twice |
| isolation regression | `…::test_isolation_a_tool_another_agent_owns_is_unknown` (support's model names `refund`, which billing owns: "no tool named 'refund'. Available tools: ask_billing.") and A2's dispatch-level test |
| a hook tripwire ends the run `blocked` | `tests/test_hooks.py::TestTripwire` (before_tool: `blocked`, `Tripwire: …`, session and store unchanged, resume returns it; before_model: no model call; the stream) |
| a `before_tool` replacement is honoured | `TestBeforeTool::test_a_replacement_is_what_the_tool_runs_with` (the tool, `ToolCallStarted` and the model see the replaced args), replacements validated, cannot change the tool, deny > ask merge, cannot loosen the policy |
| MCP: the stdio tests ported | `tests/test_mcp.py`: operonx's 56 (`test_mcp.py` 50 + `test_mcp_values.py` 6) against the same real fixture servers; registry tests became toolset tests (no registry to leak from); every client- and toolset-level test runs on stdio **and** streamable HTTP (94 items) |
| MCP: streamable HTTP against the reference server | `tests/test_mcp_reference.py`: `@modelcontextprotocol/server-everything@2026.8.31` via npx, over streamable HTTP and stdio: tools listed, text, structured values, an image block, annotations gating, an agent driving `get-sum` + `echo`, bad arguments stopped before the wire (10 items) |
| `as_op` events reach `engine.stream(mode="custom")` | `tests/test_compose.py::TestAgentAsOp::test_streamed_events_reach_the_custom_stream` (typed events, `RunStarted` … `RunFinished`), `test_streaming_adds_no_trace_record_per_event`, `test_outputs_bind_downstream`, `test_an_interrupted_run_is_resumed_from_its_state_id` |
| redaction | `tests/test_redact.py`: the 40 ported `Redactor` tests (both directions), plus: exported records scrubbed by default (tool, model input and answer, compaction) while memory and the model are untouched, a SQLite run store holds no secret, `redact=None`, approval payloads redacted / tool args not, `RedactToolOutput` before truncation |

Every new test file fails on `main` (`7bac63d`) at import (`cannot import name 'Approve'`,
`'Redactor'`, `No module named 'operonx_agents.tools.mcp'`).

**MCP protocol revisions.** The Python fixture servers (mcp 2.3) negotiate the stateless
`2026-07-28` revision over both transports (`test_it_speaks_the_stateless_revision_to_a_server_that_does`);
the reference server negotiates the handshake revision `2025-11-25`. On Node 18 the
reference server's HTTP transport needs `--experimental-global-webcrypto` (no global
`crypto`: every request answered `Parse error`); the fixture sets it for Node < 19.

**`as_op` is two ops, not one.** operonx runs a consumer once per frame a producer yields
(measured: a generator yielding three events then `{"output": ...}` ran its `output`
consumer four times, three of them failing on a missing input). So `as_op()` is a result op
whose outputs bind downstream, and `as_op(stream=True)` a transient event stream whose
`event` port an `EmitOp(..., transient=True)` sends to the custom stream: the trace keeps
one record for the stream and none per event.

**Trace redaction happens where the trace is exported.** The first cut scrubbed each
message on the run's loop (once per run, memoised): +0.03–0.05 ms CPU per turn of the
3-tool bench, +0.2–0.35 ms per turn at 5 concurrent, which put the 3-call row over 2 ms.
Track3 §4.4.8 asks for redaction "on by default only for trace export", so operonx got
the hook it lacked: `child()`'s `handle.redact`, applied by every exporter (run stores,
Local view, Langfuse) through `OpExecution.exported()`. The runner records values as they
are and sets its `RunRedaction` on each record; `handle.trace` in memory holds them raw.
The agent op's own record (`AgentOp`, written by core from its inputs and outputs) is not
covered; `@op(exclude=)` hides those.

**operonx #90** (`10fe40a`): `child()`'s `redact` (export-time scrubbing; failing tests
first: in memory vs exported, files/SQLite/Mongo/Postgres/ClickHouse stores, the Local view,
Langfuse) and `OpType` `"agent"`, the type `AgentOp` sets.

**Overhead** (`scripts/bench_overhead.py` → `results/overhead_a4.{log,json}`, tracing on, 30 / 48
trace records per run as in A3). The machine was shared with other agents' test suites, so
main (`7bac63d`, the A3 control) and a4 ran interleaved, four rounds; per turn, 5 concurrent,
p50 ms:

| round | runner 1 call: main / a4 | runner 3 calls: main / a4 | stream 3 calls: main / a4 |
|---|---|---|---|
| 1 | 1.18 / 1.29 | 1.60 / 1.75 | 1.99 / 2.15 |
| 2 | 1.23 / 1.32 | 1.66 / 2.07 (a4 p95 3.9: interference) | 2.05 / 2.12 |
| 3 | 1.31 / 1.32 | 2.04 / 2.05 | 2.47 / 2.09 |
| 4 | 1.32 / 1.34 | 2.11 / 2.18 | 2.17 / 2.17 |

A4 adds 0.01–0.11 ms per turn at 1 call and 0.01–0.15 at 3 (one noisy outlier), CPU
+0.002–0.03 ms per turn: the hooks/interruption plumbing; redaction costs the loop nothing
now. The 1-call row holds < 2 ms in every round (1.29–1.34). The 3-call row tracks main,
and main itself read 1.60–2.11 as the machine's load moved (A3 recorded 1.85): it holds
< 2 ms in the quiet round (1.75) and not in the loaded ones, where the A3 code does not
either.

**Live** (`results/live_a4.txt`): on `qwen3.7-plus`, "Refund 900 on order A1B2C3D4" parks
after 2.8 s (`'refund' asks for approval for these arguments`), state in SQLite; resumed with
`Approve()`: the refund runs once, the model answers "Refunded 900 on order A1B2C3D4.
Reference: R-0001." (2 turns, 846 + 73 tokens). The model also drove the MCP reference
server's `get-sum` over streamable HTTP (1234 + 4321 → "5555").

**Suites:** operonx-agents `-m "not live"` with `REDIS_URL` (throwaway `redis:7-alpine`):
522 passed. Live A4: 2 passed. operonx #90: 3664 passed, 50 skipped (Postgres and ClickHouse
throwaway servers set); callbot refactor: 303 passed, 2 skipped.

**Not done here:** handoffs (track3 lists them for phase 4; AGENTS_V2_PLAN A4 does not, and
no second conversational agent asks for them yet); guardrails as a named layer (they are
hooks); MCP elicitation/sampling/resources (track3 "Later").

## A5 gate (2026-10-04)

**The gate (AGENTS_V2_PLAN §4 / §A5): HTTP and WS end to end with an approval round
trip; a 20-case eval; Workflow-view screenshots (§2b).** All hold.

| plan bullet (AGENTS_V2_PLAN §A5) | test / evidence |
|---|---|
| `agent_service` over HTTP, approval round trip | `tests/test_serve.py::TestHttpJson::test_an_approval_round_trip` (refund 900 parks, nothing ran; `POST /cashier/resume` approves; it runs once, the parked turn is not asked again), `test_deny_tells_the_model_why`, invalid bodies answered `status: invalid` and run nothing (6 shapes), unknown approval ids refused, `session_id` continues a conversation |
| SSE (K8) | `tests/test_serve.py::TestHttpEventStream::test_every_event_then_the_result_and_a_streamed_resume`; core: `operonx tests/internal/app/serve/test_stream_and_resume.py` (frames sent as the run sends them, over a real uvicorn server; a run that sends nothing is still a 500) |
| `/resume` via K8 | core `Service(resume=)` → `POST <path>/resume` (`test_stream_and_resume.py::TestResumeRoute`: mounted, streams too, filed under `<name>.resume`, toml `resume =`, refused on a websocket / with variants); `agent_service` sets it |
| websocket, approval round trip | `tests/test_serve.py::TestWebSocket` (one connection: run, `ApprovalRequired`, resume frame, `RunStarted(resumed)` … `completed`; two requests on one connection run in turn) |
| the service run's trace is agent → turn → model/tool | `tests/test_serve.py::TestTheServiceRun::test_the_trace_is_agent_turn_model_and_tool` |
| **live**: HTTP and WS on qwen3.7-plus | `tests/live/test_live_serve.py` (real uvicorn on a free local port; httpx SSE + `websockets`): `results/live_a5.txt` — HTTP parked in 5.1 s (`RunStarted, TurnStarted, ToolCallStarted, ToolCallFinished, TurnFinished, TurnStarted, ApprovalRequired, RunFinished`), resumed → completed, "Refunded 900 … R-0001" (3 turns, 1663 + 107 tokens); WS the same on one connection (refund 700) |
| trajectory evaluators | `tests/test_evals.py`: `tool_called` (name, args subset, `times`), `tool_not_called`, `no_tool_errors` (exceptions, unknown tool, bad args), `turns_at_most`, `output_valid` (status + pydantic type), `cost_at_most` (priced; unpriced = unknown fails), `agent=` per agent (an `as_tool` sub-agent's steps are its own) |
| `dataset_from_runs` | `tests/test_evals.py::TestDatasetFromRuns`: recorded `agent_service` runs → cases (input from the first turn's record, the calls as the reference trajectory, the answer as `expected`) → an `Eval` of the same service graph passes 2/2; a resumed run is not a case; `agent=` filter. The runner now records the run's input on its first turn (`tests/test_redact.py::…the_first_turn_says_what_the_run_was_asked_scrubbed_on_export`) |
| **a 20-case eval** | `evals/` (20 support cases, a rule model, through `agent_service`'s graph; the trajectory evaluators + core `trajectory.tool_calls`): `pytest evals -p operonx.app.evals.pytest_plugin --operonx-eval-name support --operonx-eval-dir results/eval_a5` → **20/20, gate pass (exit 0)**, `results/eval_a5.txt`, record `results/eval_a5/support/20261004T162511-650606`. **Run locally, not in CI**: this repo has no GitHub repo/CI yet. Control: raising the approval threshold to 1000 fails exactly the two cases it should (18/20, gate failed, exit 1) |
| `operonx init --template agent` on the new API | core `tests/internal/cli/test_init.py::test_the_agent_template_is_built_on_operonx_agents` (declares `operonx-agents`, no `operonx.agents`), and the generated project's own tests, CLIs and ruff (`TestTheGeneratedProject[agent]`, run where operonx-agents is installed: 52/52) |
| a guide page with tested snippets | core `operonx/guide/09-agents.md` (6 snippets: a run, approvals + resume, `agent_service` JSON/SSE/resume, `as_op`, a trajectory eval, a scripted model), run by core `tests/guide` where `operonx_agents` is installed (`<!-- requires: operonx_agents -->`; the stand-in model now calls tools) |
| shims for `operonx.agents` (D3) | core `tests/internal/agents/test_deprecation.py`: one `DeprecationWarning` on import naming operonx-agents and `MIGRATION.md`; everything still works (the 22 `operonx.agents` test files pass); `import operonx` alone does not warn. Callbot (`refactor/operonx-studio`, which does not import it): 303 passed |
| Studio: `graphForRun` on root records | studio `tests/studio/test_runs.py::test_a_run_names_the_ops_it_ran_at_its_root` (`root_ops`, `child` on tree rows), `tests/js/agentsteps.test.mjs` (`rootOps`). The live run's IR graph has 3 ops; 7 op names ran (+ turn, model, order_status, refund): the old score 3/7 < 0.5 drew "isn't drawn here" |
| Studio: the canvas opens an agent op into turn → model/tool | `tests/js/agentsteps.test.mjs` (turns chain, a model call fans out to its tools, ids unique across executions, the Flow tab's graph untouched); screenshots below |
| Studio: child-row icons from `op_type` | tree rows with `child` use the step's type (◎ agent, ↻ turn, ✧ model call, ⚒ tool call), never an IR node of the same name; IR nodes carry `op_type` (`tests/project/test_extract.py::…op_type`) |
| Studio: flat `{id,name,args}` tool calls | already rendered since studio #12 (`io.js` `toolCallOf`, `tests/js/io.test.mjs`); seen in `desktop_tree_model.png` (`order_status()` `{"order_id": "A1B2C3D4"}`). Long call ids now shorten instead of wrapping the message head |

**Screenshots** (`results/shots_a5/`, a live qwen3.7-plus run of the `cashier` service:
"Please refund 300 on order A1B2C3D4." → 3 turns): `desktop_tree.png` / `phone_tree.png`
(agent → turn → model, order_status / refund with values), `*_tree_model.png` (a model call:
system/user/assistant messages, the tool call and its arguments, the tool's answer),
`*_workflow.png` (the Workflow view lands on the opened agent: `cashier · agent · 3 turns` →
`turn[0]` → `model[0]` → `order_status[0]`, `turn[1]` …), `*_workflow_tool.png` (a tool call
picked on the canvas opens the same execution panel as the Tree).

**Overhead** (`results/overhead_a5.{log,json}`; `Model.llm` now checks the hub per call):
interleaved with main, 3 rounds, runner p50 at 5 concurrent — 1 call: main 1.30–1.34 /
a5 1.27–1.30 ms; 3 calls: main 2.03–2.07 / a5 2.02–2.09 ms. No change; the 3-call row is over
2 ms on this machine today for main as well (A4 recorded the same drift).

**Core (operonx #91, `2c5feb9`; studio #17, `d080bc8`):** K8 SSE framing and `Service(resume=)`; `Session.stream`;
a door's resume graph in `graph_refs`; the `operonx.agents` deprecation and MIGRATION.md;
`init --template agent`; guide page 09. **Also here:** `Model` re-resolves its backend when
the ResourceHub changes (a module-level agent tested under two hubs used the first one's
model: `tests/test_model.py::TestTheHubItReads`); `operonx_agents.testing` (moved from
`tests/fakes.py`).

**Suites:** operonx-agents `-m "not live"` with `REDIS_URL` (throwaway `redis:7-alpine`): 562
passed; live A5: 2 passed. operonx feat/a5: 3607 passed, 128 skipped; in a venv with
operonx-agents installed, `tests/guide` 11/11 and `test_init.py` 52/52. Studio: 657 passed,
4 skipped; node 88/88. Callbot refactor: 303 passed, 2 skipped.

**Not done here:** operonx-agents on PyPI (the template declares
`operonx-agents>=0.1.0.dev0`, which `uv sync` cannot fetch until it is published); a
websocket "resume" route (it resumes on its connection); collapsing the per-event egress rows
a streamed run leaves under the agent op in the Tree view.
