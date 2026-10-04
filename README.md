# operonx-agents

Typed LLM steps and agents on the [operonx](../Operon) workflow engine.
Design: `Operon/docs/roadmap/track3_agents.md` §4; plan and phases:
`Operon/docs/AGENTS_V2_PLAN.md`.

Two front doors over one model layer:

- **`llm_step`**: a typed, deadline-bounded model call as a graph op. The
  graph owns control flow (the callbot's front door).
- **`Agent` + `Runner`**: a model-driven tool loop in one op, with child
  executions in the trace (phase A3).

Phase A2 (this state) ships tools, the model layer, `llm_step` and
`operonx-agents probe`.

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
```

operonx is an editable path dependency on `../Operon`. That relative path
is why this repo is not worked on from git worktrees. The checkout at
`../Operon` must contain K7 (operonx main `2279cea` or later); to test
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
