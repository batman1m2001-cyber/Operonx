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
`operonx-agents probe`; phase A3 (this state) the `Runner`, `RunState`,
`UsageLimits`, the event stream, sessions, state stores and compaction.

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
```

operonx is an editable path dependency on `../Operon`. That relative path
is why this repo is not worked on from git worktrees. The checkout at
`../Operon` must contain `child(current=False)` (operonx main `99b1634`, #86, or later); to test
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
