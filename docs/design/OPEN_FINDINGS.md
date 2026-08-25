# Open findings — 12 Aug 2026

Four adversarial reviews ran against this branch. They produced **31
findings**; every one below was **reproduced with a runnable script**, not
inferred. Nine are fixed in this release. **The rest are open**, and this
page is the handoff: what breaks, where, what a caller sees, and the exact
repro.

Repro scripts live in [`repros/`](repros/) next to this file. Run them from
the repository root with `uv run python docs/design/repros/<name>.py`.

Read [Failure modes](../architecture/failure-modes.md) first if you have
not — the pattern in almost every entry here is the same: **a plausible
value instead of an error.**

---

## Fixed in 1.3.0

Listed so you do not re-investigate them. Full detail in the
[CHANGELOG](https://github.com/batman1m2001-cyber/operonx/blob/main/CHANGELOG.md);
regression tests in `tests/internal/core/ops/graph/test_scheduler_cancel_gaps.py`.

| | What was wrong | Repro |
|---|---|---|
| `Interrupt.SELF` at the root context was still `ALL` | `r2_self_flat.py` |
| Inline (`bound="sync"`) ops were never swept — the **default** op kind | `r1_inline.py` |
| The emitter's own queued EOF was dropped, stranding a sequential fan-out | `r4_seq_self.py` |
| A `BaseException` in `_pump` deadlocked the scheduler | `r7b.py` |
| A reused `Interrupt` object was mutated in place | `r9_shared_interrupt.py` |
| `convert_type` coerced a list object instead of its elements | — |
| MCP `list_tools` read only the first page | `paged_server.py` |
| MCP tool names were not sanitised (a dotted name broke every tool) | `odd_server.py` |
| A structured-only MCP result read as empty | `struct_server.py` |

---

## Open · core

### C1 — `Media` is unwrapped only on an op's first-ever invocation
`operonx/core/ops/base.py:513-517` returns from the cached-index fast path
before `_unwrap_media_in_place` (`:542`) runs. The cache is keyed by
`state.schema` identity, which is shared across every run of an engine
*and* every item of a fan-out.

```
per-item input type: ['bytes', 'Media', 'Media']
```

The op body receives a different **type** depending on invocation order.
Silent. Repro: `r6_media.py`.

### C2 — `stream(mode="updates"|"custom")` swallows a fatal run error
`operonx/core/engine.py:863-887` and `:771-789`. The drainer task's
exception is never retrieved; the ticker reads `drainer.done()` as
"finished", flushes and returns cleanly. `mode="frames"` propagates
correctly, so the same graph raises one way and succeeds the other.

```
run()             -> ObserveBudgetExceeded
stream(updates)   -> ended cleanly, yielded the update that tripped it
```

Repro: `r8b.py`.

### C3 — `__interrupt__` leaks into the `run()` / `collect()` / `result()` payload
`task_scheduler.py:377` puts the synthetic record on the same queue as real
frames; `engine.py:252-260` and `:292-298` merge every frame's items into
the output dict without filtering the tag. A caller comparing
`set(result)` against its declared outputs, or serialising the result, gets
a surprise non-JSON-serialisable key. `handle.interrupts` filters it;
nothing else does.

### C4 — the classic loop re-dispatch path is dead code with a latent bug
`task_scheduler.py:154` and `:173`. `_loop_config` is only ever set
alongside `_loop_mode="synthetic"` (`cycle_rewrite.py:402`), which that
branch excludes — so `root_interrupted = root_interrupted or
_iter_interrupted` is unreachable. If it were live it would be wrong: one
interrupted iteration would suppress the whole loop's result. Delete or
fix; do not leave it as a trap.

---

## Open · agent layer

### A1 — budget exhaustion strands an unanswered `tool_call` in the stored history
`agents/graphs/react.py:270-285`. `normalize_messages` is upstream of the
branch, so on the exhausted turn the assistant message — `tool_calls` and
all — is written to `PARENT["messages"]`, and then `decide` routes to
`END` without dispatching.

The caller is told the turn succeeded: `AgentSession.send` only checks
`messages[-1]["role"] == "assistant"`, and a pure tool-call turn has empty
content. The **next** `send()` posts a history with an unmatched
`tool_call` and the provider rejects it — one turn after the cause.
`unmatched_tool_calls` exists and is called only from tests.

### A2 — `AgentSession.send` rolls back the history only on the success path
`agents/session.py:100-156`. The user turn is appended at `:101`; the
rollback that prevents consecutive user turns is at `:139-149`, *after*
`await asyncio.wait_for(...)` at `:117`. A timeout — a stalled provider, a
gated tool with no `on_approval` waiting out 300s — propagates with the
user turn still in the history. The caller retries, appends a second user
turn, and the provider rejects the shape.

### A3 — tool **arguments** are never redacted in the approval payload
`agents/graphs/dispatch.py:184-189` builds `approval_payload` in
`parse_call`, which takes no redactor; the redactor reaches only `execute`
(`:367-377`). So `http_post(headers={"Authorization": "Bearer sk-…"})`
ships verbatim to the human's approval prompt, the interrupt bus, the
tracer and the checkpointer — while the identical string in a tool
*result* is scrubbed. Same shape as the already-fixed "redactor skipped on
the exception path".

### A4 — `apply_cache_control` marks messages no provider reads
`agents/ops/prompt_ops.py:145-164` writes `cache_control` as a top-level
message key. The Anthropic backend rebuilds each message as
`{"role", "content"}` and discards it (`providers/llms/anthropic.py:55-79`);
OpenAI-compatible backends pass it through as an unknown property, which
strict gateways reject. `marked: 1` is returned either way. The only
symptom is the bill.

### A5 — a sub-agent is told about tools it will always be refused
`agents/graphs/subagent.py:141-157`. `_child_policy` restricts *dispatch*,
but the child reuses the parent's `call_model`, whose `tools=` list was
baked into the closure by `make_llm_caller`. The child therefore sees
tools it cannot call, calls them, and burns its own turn budget on policy
refusals. Related: an inherited `"ask"` verdict is unanswerable in a child
— nothing binds an interrupt bus to the child's state, so it waits out
`approval_timeout` and reports a timeout.

### A6 — `finish_reason` and `truncated` are declared and never read
`agents/ops/model_ops.py:68-69`; `react.py:263-268` reads only `done`,
`tool_calls`, `assistant_message`. A response cut at
`finish_reason="length"` ends the loop as a clean finish. The module
docstring claims a caller can tell the two apart; there is no wiring by
which they can.

### A7 — `_exchanges` leaves `pending_ids` stale across a flush
`agents/ops/compact_ops.py:100-131`. A tool message arriving while a stale
non-empty `pending_ids` is held is appended to an empty `current` and
emitted as a standalone group, separable from its assistant by
`groups[-keep_recent:]`. Compaction can then keep a tool result whose call
is gone. Requires a history that already contains an unanswered
`tool_call` — which A1 manufactures.

### A8 — the token budget ignores the `tools=` payload
`agents/ops/compact_ops.py:58-74`. A 20-tool registry is easily 2–4k
tokens re-sent every request, so an agent compacts against a budget it is
already over. Constant error, not proportional.

---

## Open · providers and harness

### P1 — `grep` answers a different question depending on whether `rg` is installed
`packages/operonx-code/operonx_code/tools/search.py:145-193`. Three silent
divergences: `rg` honours `.gitignore` and skips dotfiles while the Python
fallback does not; `glob=` is path-relative under `rg` and a **basename**
match in the fallback (`fnmatch(file.name, glob)`), so any glob containing
`/` matches nothing; and with a single-file `path=`, `rg` omits the
filename so the model reads a line number as a path.

This is the same bug class as the already-fixed `_match`, still live in
`_python_grep`.

### P2 — one long output line kills the bash tool and poisons the *next* command
`packages/operonx-code/operonx_code/shell.py:177-196`.
`StreamReader.readline()` raises `ValueError` past a 64 KiB line and clears
the buffer; nothing catches it, and the shell is not replaced — so the
next command's output carries the previous one's leaked marker. Any
minified JS, base64 blob or single-line JSON triggers it.

### P3 — `batch_mode=True` silently discards the whole structured-output layer
`providers/ops/llm.py:490-499` returns before the `_extract_fields` check,
so `fields`, `parser`, `validators`, `max_retries` **and** `fallback` are
no-ops. The declared output Params resolve to their defaults: every field
`None`, `error` `None` — indistinguishable from a clean parse.

### P4 — an XML root tag equal to a requested field returns a stringified subtree
`providers/parsing.py:210-230`. `_resolve_field` only descends into the
lone root when the top-level walk misses. When the root tag *is* a
requested name, the walk succeeds and hands back the child dict, which a
`: str` hint turns into a Python repr — with `error: None`.

```python
parse_and_extract("<action><type>greet</type></action>", "xml", F("action: str"))
# {'action': "{'type': 'greet'}", 'error': None}
```

### P5 — two dotted paths ending in the same leaf overwrite each other
`providers/parsing.py:106-111` sets `output_key = chain_path[-1]`.
`fields=["user.id: str", "order.id: str"]` yields one key, last writer
wins, `error: None`. There is no way to alias an output key.

### P6 — `?` optional and `validators` do not compose
`providers/parsing.py:290-302`. `apply_validators` treats `None` as
invalid unconditionally, so an absent optional field either hard-errors
(no `@default`) or is silently filled (with one) — and `:355` then strikes
it off `missing`, so nothing downstream can tell "absent" from "took the
default". Related: `v.lstrip("@")` cannot represent a legitimate value
beginning with `@`.

### P7 — a mid-stream fallback makes joined deltas disagree with the final frame
`providers/ops/llm.py:637-671`. If the primary fails after emitting
deltas, those frames have already reached the consumer; the fallback
allocates a fresh accumulator and replays the whole answer. Joining
`final=False` frames gives `primary_partial + fallback_full`; the
`final=True` frame gives `fallback_full`. The two strategies the docs call
equivalent now differ, and streaming consumers take the delta path.

---

## Open · MCP and heartbeat

### M1 — closing an `MCPClient` from a different task cancels an unrelated task
`agents/mcp.py:173-199`. Both `stdio_client` and `ClientSession` enter
anyio cancel scopes, which must be exited in the entering task. `close()`
raises `RuntimeError: Attempted to exit cancel scope in a different task`
— and `:198` swallows it. Worse, a client that is never closed finalises
on a GC task and fires `tg.cancel_scope.cancel()` from foreign context,
cancelling whatever happens to be running:

```
UNRELATED TASK: *** CANCELLED by something else ***
```

Realistic trigger: connect in a FastAPI lifespan, close in a shutdown
handler. Fix direction: pin ownership to a dedicated task, and stop
blanket-swallowing in `close()`. Repro: `repro_mcp.py`.

### M2 — a failed registration leaves dead proxies advertised to the model
`agents/mcp.py:353-388` registers tool-by-tool with no rollback;
`connect_mcp` closes the client on failure but leaves whatever already
registered. Those proxies remain in `get_tool_definitions()` and raise
"not connected" on every call. There is no `unregister`; only
`clear_registry()`, which deletes local tools too.

### M3 — `max_beats` still overshoots by one under `overlap="queue"`
`agents/heartbeat.py:223-227` vs `:235-238`. Both the ticker and the beat
chain increment `_started`; the chain can reach `max_beats` while the
ticker sleeps, and the ticker then increments past it before breaking.
Measured: `max_beats=2` → 3 beats. Same class as the skip-mode overshoot
already fixed; the queue arm was missed. Repro: `repro_hb.py`.

### M4 — `Heartbeat.stop()` swallows `CancelledError` and clears `_task` early
`agents/heartbeat.py:156`, `:167`. An outer deadline is absorbed —
`wait_for(hb.stop(timeout=30), timeout=0.2)` returns normally instead of
raising. `running` reads False for the whole grace window while a turn is
in flight, so `start()` is accepted and two loops share state.

### M5 — a `BaseException` from `on_error` tears down the event loop
`agents/heartbeat.py:265` guards `send`/`on_result` with `BaseException`;
the inner guard at `:273` around `on_error` catches only `Exception`.
Asymmetric, and any exception escaping `_beat_chain` is never retrieved
because `_beat_task` is overwritten on the next dispatch.

---

## Confirmed sound

Recorded so the next review does not spend time here. Each was probed, not
assumed.

- **Calling an MCP session from a different asyncio task is safe.** anyio
  memory object streams are task-agnostic; only scope entry/exit is
  task-bound. The hazard is lifecycle only (M1).
- **`close()` during an in-flight call** returns promptly; the call raises;
  no orphaned child process.
- **Optional MCP arguments are not sent as JSON `null`** — the synthetic
  `__signature__` is metadata; `proxy(**kwargs)` carries exactly what was
  passed.
- **`_pending` needs no lock**; `_drain` awaiting only the last beat task
  is correct; `jitter=1.0` does not hot-loop.
- **Workspace containment and the read-before-edit ledger**: `realpath`
  before the check means a symlink alias and its target share one ledger
  key, so the ledger does not alias. `write`'s `mkdir(parents=True)` runs
  on an already-resolved path.
- **`_clamp` does not split a multi-byte character** — it slices `str`.
- **`messages=` is never mutated on the semantic-retry path**, and the
  retry hint does append correctly in `messages=` mode.
- **`root_interrupted` does not leak** across concurrent sibling
  invocations of one `GraphOp` — it is a `_run_once` local.
- **The `updates` ticker does not lose steps** — `store_result` writes and
  advances the step in one uninterrupted slice.
- **Policy resolution, redaction ordering, empty-vs-`None` `tool_calls`,
  `add_messages` id handling, `memory_ops` gathering, skills parsing, and
  turn counting vs `max_turns`** were each checked and found correct.

---

## Sweep of 15 Aug 2026 — four readers over core

Found while building Operon Scope (`tools/scope/`). Every claim below was
re-verified against the source by hand; agent output was not taken at
face value. Numbered `S*` to keep them distinct from the earlier set.

### S1 — an op exception is silently swallowed by `run()`; three handlers, two dead

An `@op` raising `ValueError` makes `engine.run()` return `{}` and
`result()` return `{}`. The traceback is logged, written to the op's
`error` cell (`base.py:1068`) and recorded as an `OpExecution` trace node
— but never raised. Downstream ops never dispatch, because no `Frame` is
emitted, so the branch stops silently rather than failing.

Three nested handlers exist for this; only the first ever runs:

| Handler | Fate |
|---|---|
| `base.py:1021` `except Exception:` | catches, logs, **never re-raises** |
| `func_op.py:443-456` `CodeError` wrapper | **dead** — exception already consumed by the frame it delegates to. Its docstring claims it "re-raises any exception as a `CodeError`" |
| `task_scheduler.py:384-387` `_pump`'s `except Exception` | **dead** on this path, along with its `state[op,"error"]` write |

`docs/architecture/execution-flow.md:201` states an op raising surfaces
as an `OpError` subclass. It does not. Either the doc or the code is
wrong; the present behaviour — a bare `{}` indistinguishable from a
legitimately empty result — is the shape this codebase keeps producing.

Only `BaseException` (`ObserveBudgetExceeded`, `InterruptTargetError`)
reaches the caller, via `fatal` → `engine.py:595`.

### S2 — `loop_iters` is dead

`task_scheduler.py:302-305`. Declared and documented, never written or
read. `_on_eof` derives the iteration index by parsing the ctx tail
(`:594-601`) instead.

### S3 — `_stamped` is called twice on the same value

`task_scheduler.py:427-437` and `:438-447` are a verbatim duplicated
block, both `except InterruptTargetError` handlers included. Idempotent
in effect (after the first stamp `ctx_to_cancel` is concrete), a
copy-paste error in fact.

### S4 — three per-run dicts have flat keys but nested comments

`seq_queues` (`:277`), `seq_active` (`:282`), `collect_bufs` (`:292`) are
documented as `[gen_ctx][dst_op]`. Every real write uses a flat
`(src, dst)` edge key — e.g. `collect_bufs.setdefault((src, dst), [])`
at `:523`. **Consequence: sequential gating is per-edge globally, not per
generator context** — two different item contexts serialize against each
other on the same edge. Either the comments or the design is wrong.

### S5 — `fatal` from the final inline drain can be dropped

`fatal` is checked at `:819` (after the first drain) and `:826` (after
`queue.get()`). The drain at the end of the loop body (`:836`) has no
check after it, so if it appends to `fatal` while `inflight` is already
0, `while inflight > 0` exits and the error is lost.

### S6 — `get_inputs` resolves differently on an op's first-ever call

`self._input_cache` is set at the *end* of the slow path
(`base.py:537`), so the first invocation takes the slow path and every
later one takes the fast path. They disagree twice:

| | first call (slow) | later calls (fast) |
|---|---|---|
| missing-context fallthrough | `cell.default_value`, **no hierarchy walk** (`state.py:441`) | `cell[context_id]`, **walks ancestors** (`base.py:508`) |
| `_unwrap_media_in_place` | runs (`base.py:542`) | skipped |

The second row is finding **C1**. The first row is its unrecorded twin.
Code-verified; a triggering repro (unshared cell, no exact-ctx value, no
pull ref, ancestor value present) has not yet been constructed.

### S7 — Ref-vs-Ref in `if_()` always takes the first case

Confirmed and worse than the existing note. `_wrap`'s comparison cases
capture the right-hand side literally (`ref.py:264-275`); only
`and_/or_/rand_/ror_` resolve a `Ref` operand (`:283-286`). So
`Ref == Ref` produces a **truthy Ref**, and `BranchOp._evaluate_conditions`
does `if result:` (`branch_op.py:140`) — the first case always wins,
silently, with nothing for the `except` at `:149` to catch.

Compounding: `get_all_vars` (`ref.py:333-352`) never recurses into
comparison args, so the RHS Ref's variable is never declared as a
BranchOp input and would resolve to `None` even if it were consulted.

Workaround unchanged: compare against literals and combine with `&`/`|`.

### S8 — `Ref._resolve` keys by var name alone

`ref.py:330` does `ctx.get(self.var)`, not `(source_op, var)`. Two Refs
from different source ops sharing a variable name collide in that flat
dict. `BranchOp._parse_cases` dedupes the same way (`branch_op.py:90`).
Latent; not demonstrated end to end.

### S9 — `hasattr()` on a `Ref` is a side effect, not a question

`Ref.__getattr__` (`ref.py:373-376`) is overloaded to build a new `Ref`
carrying a `getattr` transform. So `hasattr(some_ref, "anything")` is
always `True`, and *constructs an object* as a side effect.

Any generic introspection — a debugger rendering locals, a serializer
probing for fields, a logger sniffing attributes — silently fabricates
Refs. Found the hard way: Operon Scope's value summariser probed unknown
objects with `hasattr` to detect scheduler events, which turned a 0.17s
capture into one that never terminated, because each probe created Refs
whose construction the profiler then recorded, recursively.

Not a crash, and arguably intended for the DSL. But it means `Ref` is
hostile to every tool that inspects objects generically, and nothing
says so. A `__getattr__` that raised `AttributeError` for dunder and
underscore-prefixed names would keep the DSL and stop the bleeding.

Workaround for tooling: never probe; match on `type(v).__name__` against
a known list, and read fields with `object.__getattribute__`.

---

## Sweep of 18 Aug 2026 — extractor build-out

Found while wiring `operonx-project`'s extractor against the tutorial.

### S10 — `operonx[openai]` is broken against the current OpenAI SDK

`operonx/providers/llms/base.py:8` does a module-scope `import httpx`, but
the `openai` extra declares only `openai>=1.0` and has always relied on
httpx arriving transitively through the SDK.

**openai 3.x moved to `httpx2`:**

```
$ python -c "import importlib.metadata as m; print(m.requires('openai'))"
openai 3.2.0 -> ['httpx2<3,>=2.7.0', ...]
```

So a fresh `pip install operonx[openai]` resolves openai 3.x, never installs
`httpx`, and **`import operonx.providers` raises `ModuleNotFoundError`**.
This is a shipped-package failure, not a test artefact — six of the eight
provider tutorial examples reproduce it once their lockfiles are refreshed.
Older locks that pinned openai 2.x mask it.

`keycloak.py` imports `httpx` the same way and is exposed identically.

Measured in clean venvs — **four of five extras tested were broken**, so
this was never an `openai`-only problem:

```
operonx[openai]   -> ModuleNotFoundError: httpx
operonx[faiss]    -> ModuleNotFoundError: httpx
operonx[gemini]   -> OK
operonx[bedrock]  -> ModuleNotFoundError: httpx
operonx[pgvector] -> ModuleNotFoundError: httpx
```

**Fixed (metadata only).** `httpx>=0.24` added to every extra that reaches
`operonx.providers`, honouring the invariant already stated above the extras
block — *"each extra is self-contained... installing one extra cannot rely on
another being present"* — and matching what `anthropic` always did.
`operonx[openai]`, `[gemini]` and `[bedrock]` now import cleanly.

### S10b — the lazy-backend design was defeated by its own base class · FIXED

Fixing httpx exposed the next eager import. Retrieval-only extras still fail:

```
operonx[faiss] / [pgvector] / [qdrant] / [onnx] / [postgres]
  -> ModuleNotFoundError: No module named 'openai'
```

`operonx/providers/llms/__init__.py` documents the right design — *"Backend
classes are lazy-loaded via module-level `__getattr__` so this package can be
imported with only core dependencies"* — and `embeddings/__init__.py` applies
the same pattern deliberately. But `llms/base.py` is imported **eagerly** by
that very module, and its lines 8–10 pull both SDKs:

```python
import httpx                                     # used at runtime (line 94-100)
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
```

The two `openai` imports are **types used only in annotations** — all 14
uses are signature annotations; the rest are docstrings. Three checks
cleared the change: nothing in `operonx/` calls `get_type_hints`, `BaseLLM`
is a plain ABC rather than a pydantic model, and nothing re-imports those
names from `base.py`.

**Fixed.** They now load under `if TYPE_CHECKING:`. The accompanying
`from __future__ import annotations` is load-bearing rather than cosmetic:
`base.py` did not have it, so its signatures were evaluated at definition
time and the deferred imports would otherwise raise `NameError`.

Verified in clean venvs — five extras now import `operonx.providers` with
the OpenAI SDK entirely absent:

```
operonx[faiss] [pgvector] [qdrant] [onnx] [postgres]  -> OK  (no openai SDK)
operonx[openai] [gemini] [bedrock]                    -> OK  (openai present)
```

Behaviour is unchanged with the SDK present: 1687 unit tests and 246 live
integration tests pass, matching baseline exactly.

The alternative — declaring `openai` in the retrieval extras — was rejected:
it would make `pip install operonx[faiss]` pull an LLM SDK to do vector
search, cementing the wrong architecture.

*Caveat for later:* `from __future__ import annotations` applies file-wide.
A pydantic model or runtime hint resolution added to `base.py` in future
would need `get_type_hints()` with the right namespace rather than working
by accident.

### S12 — install hints pointed at extras that do not exist · FIXED

`pyproject.toml`'s own install-tier comment listed
`pip install operonx[providers]`, and `llms/__init__.py:22-23` still tells
users to install it when a lazy backend is missing:

```python
"OpenAISDKModel": ("operonx.providers.llms.openai", "providers"),
```

**Root cause.** The extra was not merely undefined — it was *deleted* by
commit 1f830c7 (Apr 2026, "examples: standalone per-example projects"),
which kept both the install-tier comment and every in-code reference:

```
-providers = [
```

pip does not fail on an unknown extra; it warns and installs the base
package. So the advice our own `ImportError` gave appeared to work, and then
the same `ImportError` came back.

**Scope was nine call sites, not two** — `llms/__init__.py`,
`llms/factory.py` ×2, `embeddings/factory.py` ×2, `rerankers/factory.py` ×3,
`auth/factory.py`. Two tutorial examples (`ex07`, `ex12`) pinned it, which
is why they had no numpy despite importing it directly.

**Fixed** by restoring the deleted extra verbatim, plus the `httpx` note
from S10:

```toml
providers = [
    "openai>=1.0",        # canonical ChatCompletion types
    "aiohttp>=3.8",       # vLLM/TEI/Pinecone HTTP clients
    "numpy>=2.2.6",       # vLLM embedding output type
    "httpx>=0.24",        # Anthropic direct API client + operonx.providers import
]
```

`ex07` and `ex12` are back on `operonx[providers]` — the pin they always
wanted.

### S12b — install hints for backends that were never implemented

`DocStoreType.MONGO` and `.REDIS` are declared and have factory branches,
but `doc_stores/mongo.py` and `redis.py` **do not exist**. Configuring one
produced:

```
ImportError: MongoDocStore requires additional packages.
  Install with: pip install operonx[mongo]      <- extra never existed either
  Original error: No module named '...doc_stores.mongo'
```

Wrong twice: it blamed a missing install for something no install provides.
Now raises `NotImplementedError` naming the backends that do exist. The
`DocStoreType` members and the dangling `_LAZY_BACKENDS` entries are left
alone — removing public enum members is an API decision.

### Regression guard

`tests/internal/cli/test_extras.py` scans every install hint in `operonx/`
— string literals (comments excluded via `tokenize`), `_missing_extra_message`
call sites, and `_LAZY_BACKENDS` tuples — and asserts each names a declared
extra. 27 hints checked; verified to fail on an injected bad name.

These paths only execute when a user hits a missing optional dependency,
which is exactly why two of them rotted unnoticed for four months. Companion
to `test_entry_points.py`, which guards the same class of bug for
`[project.scripts]` after an identical migration-era regression.

### S11 — `ex16_rag_pipeline` could never have run

```python
q_emb = EmbeddingOp.of(resource="openai", texts=[question])   # question is a Ref
```

Operonx wires one param to one state cell holding one ref, and rejects a Ref
nested inside a list with a clear `TypeError`. The example's own
`main.py` calls `rag(question=PARENT["question"])`, so construction fails
before any resource is touched — the graph cannot be built at all.

`ex07` shows the intended idiom: pass the Ref directly (`texts=query`).
Fixed by matching it. The lesson is that nothing exercised these examples —
worth a CI job that at minimum *constructs* every declared graph, which
`operonx-extract` now does for free.
