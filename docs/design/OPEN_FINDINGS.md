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
