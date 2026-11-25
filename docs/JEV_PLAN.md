# JEV_PLAN — a `decision` resource and `DecisionOp` for typed classifiers (TypeSafe Jev)

Status: plan, 2026-11-23. One branch (`feat/jev`), one PR.

## Why

Jev, TypeSafe's "System One" model, does not write text. It takes one **state** and several named, typed
**questions**. It answers all of them in parallel, in one request, with calibrated probabilities. It costs
$0.042 per million input tokens, and output is free. A request takes about 0.65 s.

Today operonx can only reach it as a chat model, and Jev is not one. Its endpoint is
`POST …/v1/systemone`, not `/chat/completions`. The only route is an `@op` that calls httpx by hand,
which loses the retry, the price and the run-row token counts that `llm:` gets.

What matters most about Jev is the **probabilities**, which let a flow say "act only when sure".
The `jev` package on PyPI (0.3.0) maps answers onto a pydantic model. Its README admits that this
throws the probabilities away. This design keeps them.

## The wire format

All of this was checked on 2026-11-23 against the sources below.

Sources:
- The `typesafe-sdk` models, generated from `https://api.typesafe.ai/openapi.json`.
- docs.typesafe.ai: `api/system-one`, `api/errors`, `models`, `confidence`, `primitives/*`, `model-jaggedness`.
- Live probes on OpenRouter, which cost $0.00002 in total.

### The request

```json
{"model": "jev-1.14.0",
 "state": "text, or any JSON object or array",
 "questions": {
   "tone":   {"type": "choice", "instructions": "The customer's tone.",
              "criteria": {"calm": null, "angry": "raises voice, insults", "confused": null}},
   "refund": {"type": "noul",   "instructions": "The customer asks for a refund."},
   "urgency":{"type": "score",  "instructions": "How urgent is it?",
              "criteria": ["not urgent", "today", "right now"]}},
 "assets": {"<placeholder>": {"type": "image", "format": "png", "data_base64": "…"}}}
```

| Type | What it answers | Fields |
|---|---|---|
| `noul` | P(statement holds) | `instructions`, optionally `criteria: {true, false}`. It needs one of the two. |
| `choice` | one label out of N | `criteria: {label: description or null}` must not be empty. Clients cap it at 255. |
| `score` | an ordered level | `criteria: [level0, level1, …]`, at least one level. Clients cap it at 256. |
| `box` | an image region | Image only. Needs Jev 1.14 and the header `TypeSafe-Danger-Zone-Images: true`. |

`instructions` and each criteria value can be a string, a JSON object or a JSON array.

### The response

```json
{"model": "typesafe/jev-1.14-20261014",
 "answers": {
   "tone":    {"type": "choice", "choice": "angry", "confidence": 0.52,
               "probabilities": {"calm": 0.18, "angry": 0.68, "confused": 0.14}},
   "refund":  {"type": "noul", "noul": 0.91},
   "urgency": {"type": "score", "score": 1.4, "confidence": 0.3,
               "legend": {"0": "not urgent", "…": "…"}, "probabilities": {"0": 0.2, "1": 0.2, "2": 0.6}}},
 "usage": {"input_tokens": 364, "output_tokens": 0, "cost": 0.0000153}}
```

`usage.cost` is only sent by OpenRouter. A `noul` answer has no confidence field.
A `box` answer is `{box: {top, bottom, left, right}, confidence}`, with fractions of the image.

### Routes and models

Both routes accept the same body. A base URL ending in `/v1` plus `/systemone` serves both.

| Route | URL | Model ids |
|---|---|---|
| TypeSafe direct | `https://api.typesafe.ai/v1/systemone` | `jev-1.14.0`, `jev-1.13.0`, or the aliases `jev-latest` and `jev-preview` |
| OpenRouter | `https://openrouter.ai/api/v1/systemone` | `typesafe/jev-1.14`, `typesafe/jev-1.13`. There are **no aliases**: `typesafe/jev-latest` gives 400. |

**Pin the version.** In a probe, the same Vietnamese message came back calm 0.49/0.51 from 1.14 and
angry 0.68 from 1.13. Two calls to 1.14 also differed by about 0.01.

### Limits and errors

- Each request is limited to 64k tokens, covering the state and all questions.
- Each question is limited to 32k tokens, covering the state and that one question.
- The header `TypeSafe-Danger-Zone-Long-Context: true` raises both limits to 262k.
- Through OpenRouter the 64k limit is not applied (probed 2026-11-23). A 173,314-token state was
  answered without the header, at the same price as with it. About 350k tokens were refused with the
  header and without it (`max_tokens_exceeded`), so 262k is the limit on that route. The header
  matters on the direct TypeSafe route.
- A body is limited to 32 MiB and 64 levels of nesting, and only finite numbers are allowed.

| Status | Meaning | Here |
|---|---|---|
| 400 | Breaks a request rule. A body containing `max_tokens_exceeded` means it is over the context (probed). | Raise. When it is over the context and has more than one question, **split the questions** (below). |
| 401 / 403 | The key is wrong. | Raise with "check api_key". Never retry. |
| 413 / 415 / 422 | The body is too big, badly encoded, or invalid. | Raise with the server's message (the 422 detail names the field). |
| 429 / 529 | Rate limited or overloaded. | Retry. `TRANSIENT` already covers 429 and 5xx, and `Retry-After` is honoured. |

Error bodies come in two shapes:
- **TypeSafe:** `{"detail": …}`.
- **OpenRouter:** wraps the provider's error as `{"error": {"message": "HTTP 422: {\"detail\": …}", "code": 422}}`. Probed for 400 and 422.

The message is read from either shape.

### Known weaknesses

From TypeSafe's `model-jaggedness` page:
- It reads literally.
- It cannot do maths, counting or date comparison.
- It is weak with indirection.
- A large irrelevant state hurts.
- A choice is biased toward its first option.

The guide says to use it to route, triage and gate, and not to generate. These belong in the docs, not
in code.

## The design

### 1. Resource category `decision`

The category is named after the protocol, a typed decision, not after one model. Another System One
server, such as a local one, fits it unchanged.

```yaml
# resources.yaml
decision:jev:
  api_type: openrouter              # typesafe | openrouter | fake
  api_key: ${OPENROUTER_API_KEY}
  model: typesafe/jev-1.14          # required: answers differ between versions
  # base_url: https://openrouter.ai/api/v1   (the default for the api_type)
  # timeout: 30
  # long_context: false             # sends TypeSafe-Danger-Zone-Long-Context
  # cost_per_input_token: 4.2e-8    # TypeSafe direct does not report a cost; OpenRouter does
  # retry: {max_attempts: 3, initial: 1, max_interval: 30}
  # headers: {X-Title: my-app}
```

`DecisionConfig(YamlModel)` has these fields:
- `_category = "decision"`
- `api_type: DecisionType`
- `api_key`
- `base_url`, which defaults to the api_type's URL
- `model`, which is required
- `timeout = 30.0`
- `long_context = False`
- `cost_per_input_token = None`
- `retry`, with `check_resource_retry` run as the config loads
- `headers`
- `split_on_overflow = True`
- `script`, for `fake`

These defaults were chosen deliberately:
- **Model:** there is no default. A default would go stale, and the version changes the answers.
- **Price:** there is no hardcoded price. OpenRouter reports `usage.cost`. For TypeSafe direct, the
  config states the price, as `llm:` does. An unpriced call is `cost_usd: None`, never 0. This is
  `LLMOp._cost_usd`'s rule.
- **Retry:** 3 attempts with a 1 s initial wait. An `llm:` defaults to 1 attempt. TypeSafe's own SDK
  retries 429/529 by default, and their docs say to.
- **Timeout:** 30 s. The SDK uses 10 s, but a long-context request can take longer.

### 2. The backend

The package is `operonx/providers/decisions/`, laid out like `rerankers/`:

| File | Contents |
|---|---|
| `config.py` | `DecisionType`, `DecisionConfig` |
| `base.py` | `BaseDecider`, plus the shared helpers: answer normalising, `verdict`, the error-message reader |
| `system_one.py` | `SystemOneDecider`, for typesafe and openrouter, over httpx (the `providers` extra); `RunDecider`, `api_type: run`, a self-hosted endpoint's envelope (1.48) |
| `fake.py` | `FakeDecider`: scripted answers, no network, for tests and templates. Like `llm` `api_type: fake`. |
| `factory.py` | `create_decision`, with lazy imports and a missing-extra hint |

`registry/decision_plugin.py` registers `DecisionConfig` → `create_decision`.

The backend's call:

```python
result = await decider.decide(state, questions, assets=None)
# Decision(answers={...}, usage={"prompt_tokens": 364, "completion_tokens": 0, "total_tokens": 364},
#          cost_usd=1.53e-05, model="typesafe/jev-1.14-20261014")
```

**Checked before sending.** `questions` must be a non-empty dict of dicts, each with a string `type`.
Anything deeper is the server's job, and its 422 names the field. Unknown types pass through, because
the SDK is forward-compatible.

**Headers:**
- `Authorization: Bearer <key>`
- the long-context header when `long_context` is set
- the images header when `assets` are given
- the configured `headers`

**Retries.** The call runs under `retry_call(resource_retry(cfg, attempts=3, initial=1.0,
max_interval=30.0))`. A failed HTTP status raises `DecisionError` carrying `status` and `response`,
so `TRANSIENT` and `Retry-After` work unchanged.

**The answer's shape is checked, not just the status.** The `answers` keys must equal the `questions`
keys. A wrong `base_url` can answer 200 with an HTML page.

**Answers are normalised by adding, never by removing.** Every answer keeps the server's fields:
- A `noul` answer gains `confidence = |2p − 1|`.
- A `choice` answer without `confidence` gains `(pmax − 1/n)/(1 − 1/n)`, which is TypeSafe's definition.

**Context overflow.** When a request is over the context (400 with `max_tokens_exceeded`) and holds
more than one question, the questions are split in two halves. The halves are sent concurrently and
merged, with usage and cost summed. A single question over the limit raises: the state is too long,
and splitting cannot help.

**Usage** uses the LLM names `prompt_tokens`, `completion_tokens` and `total_tokens`, so the run row's
token columns count it unchanged (`telemetry/runs/model.py::_usage`).

### 3. `DecisionOp`

The op is needed: without it a flow cannot ask Jev without an ad-hoc `@op`. Its type is `"decision"`.
It is listed in `OpType` and in `_SLOW_EXEMPT`, because its time is the provider's.

```python
from operonx import PARENT
from operonx.providers.ops import DecisionOp

triage = DecisionOp.of(
    resource="jev",                       # decision:jev in resources.yaml
    state=PARENT["message"],              # str, or a dict: structured state is first-class
    questions={
        "tone":   {"type": "choice", "instructions": "The customer's tone.",
                   "criteria": {"calm": None, "angry": None, "confused": None}},
        "refund": {"type": "noul", "instructions": "The customer asks for a refund."},
    },
)
# triage["answers"]["refund"] -> {"type": "noul", "noul": 0.91, "confidence": 0.82}
```

| Ports | |
|---|---|
| inputs | `state` (required), `questions` (dict, required), `assets` (dict, optional), and `resource` when the resource is given at run time, as with `RerankOp` |
| outputs | `answers`, `usage`, `cost_usd`, `model_used` |

- **The questions are the wire format itself.** There is no second vocabulary, so TypeSafe's docs and
  examples apply as written. A static dict at `.of(...)` is an ordinary input default, and a `Ref`
  works too.
- **`cost_usd` is an output.** Any op whose outputs carry `cost_usd` and `usage` is counted, priced and
  token-summed on the run row (`model.py` line 392). The op only has to emit both.
- **Errors.** An HTTP status, or a body that is not an answer, raises `DecisionError` with the
  resource, the question names, `status` and `response`. A connection error or a timeout is raised raw
  as httpx raises it, so it stays transient to an op's `retry=`. The op wraps nothing, as `LLMOp` does.
- **Routing** stays in an `@op` that computes the bool. A Ref-vs-Ref comparison inside `if_()` is wrong
  without any warning (see the guide).

`verdict(answer, at=0.7)` applies TypeSafe's threshold advice:

```python
from operonx.providers.decisions import verdict

verdict({"type": "noul", "noul": 0.91})                     # True
verdict({"type": "noul", "noul": 0.55})                     # None: the unsure band is its own outcome
verdict({"type": "choice", "choice": "angry", "probabilities": {"angry": 0.68}})  # None at 0.7
```

- For a `noul`: True when p ≥ at, False when p ≤ 1 − at, and None in between.
- For a `choice`: the label when its probability is at least `at`, otherwise None.
- Other types raise: a score's "sure" depends on what the scale means.

### Not in this plan

Each item is left out on purpose and can be added later on its own:

- **A pydantic-model mapping** (`bool` → noul, `Literal` → choice), as the `jev` package does. It is
  sugar over the dict, and the dict keeps one vocabulary.
- **Prompt objects as `instructions`.** Today they are data. A rendered prompt can be passed as a string.
- **An answer cache.** operonx caches at the op level. Jev answers vary by about 0.01 between calls, so
  a cache changes behaviour and should be opt-in.
- **gzip/zstd request compression.** Only bodies of 32 MiB or more need it.
- **A studio card for the op type.** The studio has no per-type card for `rerank` either; ops draw generically.

## Phases

| # | What | Done when |
|---|---|---|
| J1 | config, plugin, factory, `SystemOneDecider`, `FakeDecider`, `DecisionError`, normalising, `verdict` | Unit tests over `httpx.MockTransport` pin the URL, headers, body, both error shapes, the retry on 429/529 with `Retry-After`, the overflow split, the shape check and the cost (reported, priced, None). |
| J2 | `DecisionOp` and `OpType`/`_SLOW_EXEMPT` | Tests run the op in a graph on `fake`, cover a static `questions` default and a run-time resource, and check that the run row counts tokens and cost. |
| J3 | Docs: the guide's op table, a `resources.yaml` example, and the weaknesses | `operonx guide` shows it. |
| J4 | One live call on OpenRouter, run by hand, not in CI | A two-question request returns probabilities and a cost. Whether OpenRouter forwards the long-context header is checked with a >64k-token state, which costs about $0.01. |

Cost: J4 is under $0.02 in total.
