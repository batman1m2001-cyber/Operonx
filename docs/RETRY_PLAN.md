# One retry model for operonx: a project default, specific overrides

**Status:** proposal, 2026-10-14. Nothing implemented. Every "today" below was read in the code.

## What exists today: eleven retry mechanisms

| # | Layer | Where | Knobs | Counts | Default | Which errors | In the trace? |
|---|---|---|---|---|---|---|---|
| 1 | an op | `core/policy.py` `Retry`, `@op(retry=)` or per use `op(..., retry=)` | `max_attempts, initial, backoff, max_interval, jitter, on` | **total** attempts | **off** (opt-in per op) | `TRANSIENT`: timeouts, connection errors, 429, 5xx | yes: `attempt`, a retried node |
| 2 | LLM call (transport) | resource config, `LLMOp._call_with_retry` | `max_retries, retry_base_delay, retry_min_delay, retry_max_delay, retry_on_empty` | **extra** attempts | **0, off** | its own list (rate limit, 5xx, empty answer) | **no**, a log line only |
| 3 | embeddings call | `providers/embeddings/config.py` | `max_retries, retry_base_delay, retry_max_delay` | extra | **2** | its own | no |
| 4 | Triton call | `providers/triton/client.py` | `retries, retry_base_delay, retry_max_delay` | extra | **2** | its own gRPC code list | no |
| 5 | Gemini token refresh | `providers/llms/gemini.py` | none (hard-coded) | 3 | 1 s, 2 s, 4 s | any | no |
| 6 | a job item | `Job(retry=Retry(...))` | the op's `Retry` | total | off | `ITEM_FAILED`, `ITEM_TIMEOUT` | per item line |
| 7 | the run queue | `app/queue.py` row | `max_attempts`, `available_at` | total | **3** | a failed run | the attempt in meta |
| 8 | webhook callbacks | `app/serve/triggers.py` | `CALLBACK_BACKOFF = (0.5, 1, 2)` | 4 | fixed | any | no |
| 9 | trace writer | `telemetry/writer.py` | `max_retries, retry_backoff=(0.5, 30)` | extra | 3 | any | n/a |
| 10 | re-ask the model (semantic) | `LLMOp(max_retries=)`, agents `output_retries`, judges `max_retries` | one count | extra | 0 / 1 / 1 | a parse or validation failure | partly |
| 11 | after the run | `resume(retry_failed=True)` (#153) | a flag | n/a | off | the run's first failed op | a new attempt of the run |

The layering rule is sound and stays: an inner layer that gave up marks the error (`mark_retried`), so an op's `Retry` does not retry it again. (3 transport tries under 4 op attempts would otherwise be 16 calls.)

## The problems, with evidence

**P1. A backoff holds a concurrency slot while it sleeps.** The op's retry loop sleeps inside `op.run` (`core/ops/base.py`, `await asyncio.sleep(delay)`). The scheduler only calls that after `_sem.acquire()` and the run's shared limiter (`task_scheduler.py`, `_pump`).

So an op waiting out a 30 s backoff keeps one of the run's N slots. During a provider outage every slot fills with sleepers, and healthy ops wait behind them. This is the user's point about the task queue.

**P2. No default for a whole project or run.** `Operon(...)` takes `errors=` and `max_concurrency=`, but no `retry=`. A project that wants "retry transient failures 3 times everywhere" must annotate every op, and an op it forgets fails on the first 429.

**P3. Three ways to count.**
- `Retry(max_attempts=3)` is 3 calls.
- An LLM resource's `max_retries: 3` is 4 calls.
- Triton's `retries=2` is 3 calls.

The same number means different things one file apart.

**P4. Five shapes for the same backoff:**
- `(initial, backoff, max_interval, jitter)`;
- `(retry_base_delay, retry_min_delay, retry_max_delay)`;
- `(retry_base_delay, retry_max_delay)`;
- a `(0.5, 30)` tuple;
- a fixed list.

Gemini's is hard-coded.

**P5. Defaults that disagree.** An LLM call's transport retry is off by default (`max_retries: 0`). Embeddings and Triton retry twice. So the call most likely to hit a 429, the LLM, is the one that does not retry.

**P6. "Which errors are worth another try" is written three times:**
- `TRANSIENT` (op);
- the LLM transport's own list;
- Triton's gRPC code list.

A project cannot say "our CRM's `Throttled` is transient" once, for all of them.

**P7. Transport retries are invisible.** They go to the log only. The studio cannot show that a slow LLM step was really three calls behind two 429s.

## The constraint: no cost to latency or concurrent calls

The user's rule, and the gate for every step: **retry must not slow a call that does not fail, and must not lower how many calls run at once.**

- **Zero cost when nothing fails.** An op with a retry policy pays one `is None` test (or one flag test) on its success path, as `journal=` does today. No extra task, no extra await, no allocation.
- **Do not move ops off the inline path.** Today an op with `_policy` is put in `_task_ops` (`task_scheduler.py`), so it gets an asyncio task even when it is a plain `def` that would run inline. A project-wide default (R2) would therefore turn **every** op into a task op. That is a real per-op cost on the callbot's audio path, 1 ms-scale ops at 5+ CCU, and must not happen. R2 is designed so that:
  - the policy is only *looked at* when an attempt fails;
  - a plain `def` op keeps running inline;
  - only a **failed** attempt that will be retried is handed to a task for its backoff, so the main loop is never blocked on a sleep;
  - `_task_ops` stays as it is for ops with an explicit `timeout=`, which needs a task to cancel.
- **The backoff must not hold capacity:** that is R1 (release the slot while waiting).
- **A door graph's live path (the callbot) is never retried by a default.** A stream item cannot be replayed to the caller. The project default applies to ops outside a door's per-item path unless an op opts in. Today `Retry` already refuses to retry a generator that has yielded.
- **The gate, measured, not assumed:** before and after each step,
  - the 5-CCU callbot load test (`load5.py` against a local `operonx-serve` on 19922, never 9922/9924/9926): greeting p50/p95 and per-turn latency within noise of the baseline;
  - the scheduler microbenchmarks: per-op dispatch overhead unchanged.

  A step that moves either is not merged.

## The proposal

### One model, three scopes

`Retry` is the one policy type, and every layer reads it. A retry is declared at the widest scope that is true. The narrowest one wins:

```
per use   op(x=..., retry=Retry(...))        ← wins
op        @op(retry=Retry(...))   /  @op(retry=False)
engine    Operon(g, retry=Retry(...))
project   operonx.toml  [retry]                ← the default
```

- **Optional, as asked:** the project default is off unless `[retry]` is in `operonx.toml`. Any op opts out with `retry=False`, for example a payment that must never be sent twice without its idempotency key.
- **Safe by default when turned on:** the default only retries `TRANSIENT` errors. A bug (a `ValueError`, an HTTP 400) fails the same way every time and is never retried, so turning the default on for every op is harmless for pure code.
- **Example `operonx.toml`:**

  ```toml
  [retry]
  max_attempts = 3          # attempts in all, the first included
  initial = 0.5             # seconds before the second attempt
  max_interval = 30
  transient = ["crm.errors:Throttled"]   # added to TRANSIENT, for every layer
  ```

### R1. A backoff gives its slot back. *Essential · small · library*
- While an op waits between attempts it releases its concurrency slot (and the run's shared limiter), and takes them again before the next attempt. A producer waiting on a full edge already does exactly this in `_pump`.
- Only the wait changes. Attempts, trace records and `run_context().attempt` stay as they are.
- *Considered and not proposed:* moving the retry out of the op into the scheduler's queue, as a delayed re-dispatch. It frees the slot the same way, but it is a large refactor of the main loop for no extra gain. Surviving a crash during a backoff is already covered by the journal: a resume reruns the op.

### R2. A project and engine default, with per-op opt-out. *Essential · small to medium · library*
- `[retry]` in `operonx.toml` and `Operon(g, retry=...)`; `@op(retry=False)` opts out; precedence as above.
- Jobs and Services built from the application pick up the project default.
- Ops that already run their own semantic re-asks keep them: `LLMOp(max_retries=)` and agents' `output_retries`, layer 10, are a different thing (a new prompt, not the same call again).

### R3. One vocabulary everywhere. *Important · medium · library*
- Transport retries (LLM, embeddings, Triton, Gemini's token), the run queue, webhook callbacks and the trace writer all take a `Retry` (`max_attempts` counts every attempt).
- In `resources.yaml`:

  ```yaml
  retry: {max_attempts: 3, initial: 1, max_interval: 60}
  ```

  The old keys (`max_retries`, `retry_base_delay`, …) keep working as aliases, converted (`max_retries: 2` becomes `max_attempts: 3`).
- **One default for every transport:** 3 attempts on transient errors. The LLM moves from off to on.
  - This is the one behaviour change. It fixes P5, and `mark_retried` keeps it from multiplying with an op's retry.

### R4. One list of transient errors. *Important · small · library*
- `TRANSIENT` becomes the single classifier:
  - it adds the LLM transport's cases (empty answer, provider rate limit) and Triton's gRPC codes;
  - it also includes what a project lists under `[retry] transient`.
- Every layer asks it.

### R5. Retries in the trace and the studio. *Nice to have · small to medium · library and studio*
- A transport retry adds `{attempt, error, waited}` to its op's trace node.
- The studio's step panel says "3 calls, 2 retried (429)".
- The Settings screen shows the project's `[retry]` (read only).

### Left as they are
- **Semantic re-asks** (layer 10): they re-prompt, they do not repeat a call. Renaming them would only churn.
- **`Job(retry=)`** (layer 6): it already takes `Retry`; only its default joins R2.
- **`resume(retry_failed=True)`** (layer 11): it retries after the run, for a person after a fix. It is not a policy.

## Order

| # | Item | Impact | Effort | Behaviour change |
|---|---|---|---|---|
| 1 | R1 backoff releases its slot | high under outages | small | none, only waits |
| 2 | R2 project/engine default + `retry=False` | high | small to medium | none until `[retry]` is set |
| 3 | R4 one transient classifier | medium | small | none |
| 4 | R3 one vocabulary + transport default on | medium | medium | **the LLM retries transient errors by default** |
| 5 | R5 retries in trace and studio | medium | small to medium | none |

Each step ships with tests in `tests/internal/policy` and `tests/internal/durable`, and a guide update (`guide/06-failures.md`). R3's alias reading is tested against every resource type.
