# Track 1: dogfooding operonx 1.14.0 on 8 small products

**Date:** 2026-10-04 · operonx `a21082d` (v1.14.0, main) · operonx-studio `7de6d60`
**Workspace:** `/home/thanglq/operonx-dogfood` (its own `git init`, no commits). Every product depends on
`/home/thanglq/Operon` as an editable install. Neither source tree was modified.
**Model:** real `gpt-4o-mini` + `text-embedding-3-small` via `OPENAI_API_KEY` from `Operon/.env`, copied
into each project's `.env` (mode 600) as `LLM_API_KEY` and never printed. Total spend was a few cents.
Edge cases ran against a scripted fake OpenAI server, `common/fakellm.py`, which I had to write myself (F25).
**Studio:** a separate instance on port 8791 (`HOME` and `OPERONX_STUDIO_STATE_DIR` under `.home/`,
`OPERONX_STUDIO_AUTH=off`), installed into its own `.studio-venv`. I queried it over its HTTP API and stopped it
at the end. Ports 8766 and 8792 belong to other processes and were not touched.

Every claim below comes from a script I ran, and the script's path is given, or from a `file:line` I read.
Severity levels:
- **P0:** a silent wrong result, a hang or a leak in the core.
- **P1:** real friction or a debuggability gap that costs real time.
- **P2:** polish.

Each finding is also tagged as one of: bug, missing feature, API awkwardness, docs gap, debuggability.

---

## (a) Per-product log

### P1. LLM prompt chain with structured output: `p1_chain/`
**Built:** scaffolded with `operonx init p1_chain --template chat`. The `triage` feature is
`src/triage/{ops,graph}.py`. It extracts `category/urgency/summary` with an `LLMOp` using `fields=` (XML, an
allow-list validator with `@other`, `max_retries=1`), drafts a reply, then reviews it with an `LLMOp` using
`fields=["score: int","approved: bool"]` and `parser="json"`. The output is assembled into one result.
- **Happy path:** `run_triage.py` worked on the first try in 7 s, with sensible typed fields. This is a good
  first experience.
- **Edge cases** (`edge_triage.py` against the fake model, plus `repros/parse_probe.py`):
  - **A.** `<urgency>high</urgency>` into `urgency: int` gave `"high"` (a str) and `error=None`. A review of
    `{"score":"nine","approved":"maybe"}` gave `score="nine"` and `approved=True`. No error was raised, so no
    retry fired. The cause is `parsing.py:382-386` (int failure returns the value unchanged) and
    `parsing.py:377` (`return bool(value)`), so `"maybe"`, `"nah"` and `"2"` all become True. A `: list` field
    gives the string `'a'` for one `<tags>` element and a list for two (parse_probe `xml_one_tag_list` vs
    `xml_two_tag_list`). **F05**
  - **B.** The JSON reply `Sure!\n```json {...}```` fails with
    `Parse error (json): Expecting value: line 1 column 1 (char 0)`. A fenced block followed by trailing text
    fails with `Extra data`. XML containing `Q&A` fails with
    `Parse error (xml): not well-formed (invalid token)`, and XML is the **default** parser.
    `_strip_fence` (`parsing.py:198-203`) only handles a fence at the very start. There is no native
    `response_format` JSON-schema or pydantic path (no grep hits in `llm.py`, `parsing.py` or `openai.py`). **F18**
  - **C.** When extraction is missing fields after its retry, every field becomes None. The next templated
    `LLMOp` then fails with `PromptError: [PROMPT] Missing template variable(s) ... missing_vars: ['urgency',
    'category','summary']`, and the rest of the chain is skipped. `$errors` names only `engine.dr`. The real
    cause, the parse failure in `ex`, sits in `ex.error` and is never surfaced because `assemble` never ran.
    About 10 minutes to understand. **F19**
  - **D.** A provider 400 propagates as a full traceback string in `$errors["engine.ex"]`, starting with two
    operonx-internal frames (`base.py:1230`, `:1025`). **F27**
  - Literal JSON braces in a prompt (`repros/prompt_braces.py`) are not caught when the graph is built. Every
    call then fails at run time with `PromptError ... Error: '"score"'`, and the message has no hint to use
    `{{ }}`. **F20**
  - Each LLM call logs a `WARNING ... Slow op engine.ex: 2169.6ms` line, because the threshold is hard-coded at
    100 ms (`base.py:1447`). **F26**
  - `model_used` returns `'assistant'` (the resource key), not the model name. The docstring at `llm.py:186`
    says "Actual model that served the request" (code at `llm.py:1140`, `:1244`). **F29**
- **Served** (product 5) through `operonx serve`, see below.

### P2. Tool-using, multi-turn agent: `p2_agent/`
**Built:** `operonx init --template agent`, plus `src/shop/tools.py` with three `@tool`s: `search_products`,
`get_price`, and `place_order` (destructive=True). `run_shop.py` drives an `AgentSession` with `on_approval`
across 3 user turns.
- **Happy path:** 11 s on real gpt-4o-mini. Approval fired once with a readable payload. The history and tool
  messages were correct. This is the best part of the product.
- **Edge cases** (`edge_shop.py`, fake model):
  - A tool that raises, an unknown tool and bad argument names are all answered back to the model with good
    messages. For example: `Error: no tool named 'delete_db'. Available tools: ...`. Good.
  - `place_order(qty="two")`, where the schema says integer, reaches the Python function. The model sees
    `TypeError: '<' not supported between instances of 'int' and 'str'`. Arguments are never validated
    against the declared schema (no `schema` or `validate` in `agents/graphs/dispatch.py`). `@tool` requires a
    hand-written JSON schema even when the type hints already say it (`tool.py:70-90`). **F17**
  - A destructive tool with no `on_approval` hangs silently: it waited until my session timeout (15 s) and then
    raised a `TimeoutError` with an empty message. The default `approval_timeout` is **300 s**
    (`react.py:179`, `dispatch.py:341`). Nothing detects that no one is listening. In an HTTP-served agent that
    is a 5-minute hang per request. **F16**
  - Budget exhaustion is handled well: `stopped_early=True`, with a clear error string and the unrun call
    answered as "Not run".
- **Served:** `POST /ask` on port 8807 answered in 3.8 s. The trace in studio is discussed under F28.

### P3. Small RAG over local markdown: `p3_rag/`
**Built:** `operonx init`, then `operonx[faiss]`. `kb/*.md` holds 5 files. `src/rag/ops.py` chunks by heading
and does an upsert by hand. `src/rag/graph.py` defines `ingest` and
`ask` (EmbeddingOp → VectorSearchOp(faiss) → DocFetchOp(memory) → LLMOp).
- **Worked:** about 10 minutes of work. Answers came back with correct citations, and an out-of-scope question
  was refused. 12 s including indexing.
- **What's missing**, all confirmed by reading the code:
  - No ingest or upsert op. I had to call `ResourceHub.instance().get("vector_store:kb").upsert(...)` inside an
    `@op`. The hub key `"vector_store:kb"` differs from the op argument `resource="kb"`.
  - No document loader or chunker.
  - No BM25, keyword or hybrid retriever.
  - The memory doc store and the in-memory FAISS index die with the process, so a served RAG must re-ingest at
    startup.
- **Silent failure** (`edge_rag.py`): searching an index that was never ingested returns no hits and no
  warning, and the LLM answers "context does not cover it". With the dimension set wrong (`dim: 1024`), ingest
  fails into `$errors`, but every later query still answers without error. **F21**
- `$errors` was keyed `'out.up'`: the root graph was named `out`, after the variable that held the *result*
  (`out = await Operon(...).run()`). **F11**

### P4. Fan-out data pipeline with reducers: `p4_pipeline/`
**Built:** `src/orders/`. `each_order` (a generator) feeds `enrich` with `.parallel(max=8)`, which writes a
`delta` into `PARENT.declare(totals={}, reducers={"totals": merge_counts})`, and `report` reads
`row.collect()`. There are also two Jobs in `app/main.py` (`on_error="record"`, `key="id"`).
- **P0 bug, about 15 minutes to root-cause:** the first run crashed with `ReducerError: reducer on cell idx=1
  raised AttributeError: 'list' object has no attribute 'items'. old={'small': 81, 'revenue': 24800, 'big':
  119}, new=[{...200 deltas...}]`. The per-item reduction had already been correct. Then `.collect()` on
  `en["row"]` **stored all of `en`'s outputs again** at the collect context, so the reducer was fed again.
  - With a list reducer (`operator.add`) the result is silently wrong: `repros/repro_collect_reducer.py` prints
    `['item0','item1','item2',['item0'],['item1'],['item2']]` against
    `['item0','item1','item2']` without the collect.
  - Root cause: `_flush_collect` builds `merged` from every key of every buffered result
    (`task_scheduler.py:918-921`) and calls `g._ops[src].store_result(state, merged, collect_ctx)` (`:937`),
    which fires the push-ref into the cell. **F01**
  - Workaround: a pass-through `tally` op so that the reducer's writer is not the collected op.
- With the workaround: 200 items in 0.82 s, `max_inflight=8` as asked, and totals correct.
- **Reading the reduced value is undocumented.** `out["totals"]` holds the per-item op outputs (a list of 200
  deltas), not the cell. The cell needs `out["$state"].get(engine.name, "totals")`, which I found by reading
  `state.py:520`. A Job sink (`out/analytics.jsonl`) gets the unreduced list too. **F23**
- **Two failing items appear as one** (`probe_errors.py`): orders 100 and 200 both raise, but `$errors` holds
  only `{'engine.en': '...order 100'}`. By design it keeps the first failure (`state.py:366-380`), with no
  count and no item context. **F10**
- **Jobs work well.** `operonx run enrich_orders` gave `ok=18 failed=2` with each failed key listed.
  `--resume` re-ran only the 2 failures (`skipped=18`). Run records are under `out/jobs/`.
  - Note: `on_error="record"` still exits 0 with status `ok` even though items failed.
  - Run ids are UTC (`20261003T190734`) while log lines are local time (`02:07:34`). **F34**
- **Trace workflow name for every job run is `params`.** **F11**

### P5. HTTP API through `operonx serve` from `operonx.toml`: `p1_chain/app/main.py`, `p8_stream/`
**Built:** `chat` and `triage` services (`triage_http` = ingress → `triage` subgraph → egress) on port 8805.
- **Happy path:** `POST /triage` returned 200 with the full result in 3.5 s. Four concurrent `/chat` calls
  finished in 1.0 s total (true concurrency). `?trace_id=my-trace-123` was honoured.
- **P0 bug, about 10 minutes:** a body of `{oops` or `"just a string"` sent to `/triage` returned
  **`200 null`**. The same input on the flat `/chat` graph returns 500. The reason is that an op failing
  **inside a subgraph does not stop the op after the subgraph**: that op runs with None.
  `repros/subgraph_failure_leaks.py` shows:
  ```
  z: None | errors: ['out.b']                          # flat: the downstream op is skipped
  z: after ran with y=None | errors: ['out.s.b']       # nested: the downstream op runs with None
  ```
  Root cause: the `GraphOp.run` batch branch always yields `_outputs` (`graph_op.py:775-780`). Only the
  streaming branch skips all-None items (`:782-785`). The serve layer then sees one egress reply, so the
  "no output → 500" guard (`serve/app.py:320-335`) never fires. In the trace, `engine.out` is `ok` with
  `sent=True` right after `engine.t.n` failed. **F02**
- Malformed JSON is passed into the graph as a raw string (`serve/app.py:314-318`) instead of getting a 400.
  When the guard does fire it returns `500 {"error":"the graph produced no output"}` with **no trace or
  request id** in the body or headers (`curl -D -`), so a client cannot find its trace. **F09**, **F27**
- `operonx serve` has no `--port`, `--host` or `--reload` flags. Its help still says "this
  `[[serve]]` name". It binds to `0.0.0.0` by default and has no `/health` endpoint (404). When the port is
  taken it logs "Application startup complete" and only then `[Errno 98]`. **F31**

### P6. Branching and looping: `p6_flow/`
**Built:** a `router` with three-way `if_().if_().else_()` routing to a `sqrt_loop` subgraph (Newton's method
with a back-edge), a `collatz_loop` subgraph, or `reject`. The arms merge into `respond`.
- **Worked** once two problems below were fixed: sqrt(2) in 4 steps, Collatz(27) in 111 steps, unknown kinds
  rejected. Ref-vs-Ref conditions are correct now (`repros/ref_vs_ref.py`, so S7 really is fixed).
- **The parameter name `start` is reserved.** It raised two WARNINGs listing **21 reserved op keywords**,
  including `id`, `name`, `description`, `start`, `stream`, `inputs`, `outputs`, `sources` and `targets`.
  Those are ordinary domain names. **F24**
- **Seeding a loop cell from an input is undocumented, about 10 minutes.** `repros/loop_seed.py` shows three
  attempts:
  - `PARENT.declare(n=seed)` (a Ref) is accepted when the graph is built and fails at run time with
    `step() missing 1 required positional argument: 'n'`. `_edges.py:58-118` has no Ref check.
  - Declaring the parameter's own name (`PARENT.declare(n=0)` with a graph parameter `n`) works. That trick is
    not in the guide.
  - Writing back without declaring (`c_no_declare`) spins to the **1000-iteration cap with no log line and no
    `$errors` entry**: the output is `[3, 10, 10, 10, ...]`. The cap is hard-coded
    (`cycle_rewrite.py:407`, `task_scheduler.py:1044`) and has no per-loop setting. **F22**

### P7. Failure, retry, timeout, cancellation and resume: `p7_failures/`
- **No op-level retry or timeout.** `@op(retries=3)`, `@op(retry=3)` and `@op(timeout=1)` all raise
  `TypeError: op() got an unexpected keyword argument ...` (`failures.py`). The only retries are at the Job
  level (`on_error="retry:N"`), the LLM transport level, and per-tool timeouts. **F13**
- **P0: a timeout does not stop the run.** `repros/timeout_leak.py` puts
  `asyncio.wait_for(engine.run(...), 0.3)` around a graph where a 1 s op is followed by a side-effect op. The
  caller gets `TimeoutError`, and 1.7 s later the slow op and the side-effect op **have both run**
  (`slow finished: 1 | after ran: 1`). `Operon.run` (`engine.py:780-793`) never cancels the handle when it is
  itself cancelled. **F04**
- **P0: `handle.cancel()` hangs every waiter.** After `handle.cancel()`, `await handle.result()` never returns:
  it was still waiting at a 3 s guard (`failures.py` case 4). `_pump` catches only `Exception`
  (`engine.py:140`). The `CancelledError` that `cancel()` injects (`engine.py:371-374`) leaves `_done=False`
  and never notifies `_cond`. **F03**
- **Job-level retry and timeout work** (`job_retry.py`): a flaky item succeeded on attempt 3, and a slow item
  timed out 4 times and ended as `timeout`. Retries happen immediately with no backoff (all within 1 s), and the
  timed-out item has `trace_id: None`, so the hung run cannot be inspected. **F33**
- **Human-in-the-loop resume works in the same process** (`hitl.py`), but only after some digging.
  `handle.interrupts` returned `[]` even while the `InterruptOp` was waiting: it lists the *scheduler's*
  `Interrupt` frames (`engine.py:229-243`), a different concept with the same name. The `interrupt_id` comes
  from `operonx.checkpoint.bind_interrupt_bus(handle.state, sink=...)`, which the guide never mentions; it says
  only "resume with `handle.state.resume_interrupt(interrupt_id, value)`". **F15**
- **No durable resume.** The checkpointer is observe-only (`checkpoint/base.py` docstring) and the only
  implementation is `InMemoryCheckpointer`. A paused or crashed run cannot be resumed after a restart. Only Job
  item resume (by key) persists. **F14**

### P8. Streaming: `p8_stream/`
**Built:** an `LLMOp(stream=True)` feeding a per-delta op; `engine.stream(mode="updates")`; and an
`Application` with `http("/story")` and `websocket("/ws")` services on port 8806.
- `engine.stream` yielded the first delta at 1.39 s and 50 deltas in all. Good.
- **The final frame repeats the whole answer** (`stream_tail.py`): `"".join(pieces[:-1]) == pieces[-1]` is
  `True`. The last `content` is the full accumulated text (`llm.py:1134-1140`, `"final": True,
  "content": acc["response"]`), so every per-delta consumer (a websocket relay, TTS) sends everything twice
  unless it filters on `final`. The guide says only "yields `content` deltas". **F07**
- **The websocket door does not decode JSON.** The same doors graph that works over HTTP got the raw text frame
  `'{"topic": "owls"}'` as a str. The op raised `AttributeError: 'str' object has no attribute 'get'`, and the
  **client received nothing**: no error frame, and the connection stayed open until my 5 s timeout. The cause:
  `asgi.py:149-155` feeds `message["text"]` raw, while `_send` JSON-encodes dicts (`asgi.py:121-128`) and the
  HTTP door JSON-decodes bodies (`serve/app.py:314-316`). After a manual `json.loads`, the websocket streamed 37
  deltas live (first at 1.13 s). **F08**
- HTTP has no SSE. It answers with the JSON array of every egress item after the run ends; that matches the
  guide, but it is a gap for chat UIs. There is no `event-stream` anywhere in `operonx/app`.

### Studio, used against these runs
Setup: `/api/open` on 6 projects, then `/api/p/{pid}/runs`, `/trace/{run}`, `/tree`, `/timeline`, `/flow`
and `/op/{op}`.
- **What works:**
  - Runs are listed with `status` (the failed `/triage` run that answered 200 is correctly `error`) and
    `?status=error` filters to 5 runs.
  - Job runs are grouped by `job_run`.
  - Op detail shows inputs, outputs and the parsed structured fields.
  - The tree shows subgraph containers.
- **Script runs are invisible.** Products 1–4, 6 and 7 driven from scripts left no trace at all, because
  `Operon()` does not trace by default. With `Operon(..., trace="local")`, run from *inside* a project
  directory that has an `operonx.toml`, the run went to `/tmp/operonx_traces/adhoc/engine/...`, a shared
  temp directory, not to the project's `.operonx/runs`. That is because `resolve_root` only knows the project
  when an `Application` has set it (`telemetry/consumers/local.py:86-104`, fallback `:83`). About 10 minutes to
  find. **F12**
- **Every name is wrong:** `workflow` is `engine` for every service run, `params` for every job run, and
  `out` for scripts. The cause is the source-line fallback in `core/utils/auto_name.py:155-172`, which scans up
  to 6 lines *above* the call. At `jobs/job.py:177-180` it finds `params = {...}`. **F11**
- **Debugging pain:**
  - Error fields are full tracebacks that begin with operonx frames.
  - `meta.json` has no status field; the error count is only in `view.txt`.
  - Op detail shows the **unrendered** prompt template (`"Ticket: {text}"` plus the variables), not the
    messages actually sent.
  - The agent run (3 model calls, 3 tool calls) is **101 tree rows and 70 records**. About 19 plumbing ops
    repeat every turn (`counter`, `router`, `closed`, `route_1`, `planned`, `compacted`, `assembled`, `cached`,
    `recalled`, `matched`, `choice`, `adapted`, `asked`, `ended`, …).
  - Contexts read like `main.[0].engine.agent.__loop_0__#1.[0]`.
  - The first model call sorts *last* in the tree.

  **F27**, **F28**

---

## (b) Friction catalog, deduplicated

| ID | Sev | Kind | Finding | Evidence (what ran / output) | Root cause |
|---|---|---|---|---|---|
| F01 | P0 | bug | `.collect()` on one output stores **all** of the op's outputs again at the collect context; a reducer cell fed by a sibling output gets every item twice (wrong total with a list reducer, crash with a dict reducer) | `repros/repro_collect_reducer.py` → `['item0','item1','item2',['item0'],['item1'],['item2']]`; p4 `ReducerError` | `core/ops/graph/task_scheduler.py:918-921, 937` |
| F02 | P0 | bug | An op failing inside a subgraph does not stop the ops after it (they run with None); the flat case skips them. Over HTTP the result is `200 null` | `repros/subgraph_failure_leaks.py`; `curl /triage -d '{oops'` → `null [200]` | `core/ops/graph/graph_op.py:775-780` (batch branch always yields) |
| F03 | P0 | bug | `handle.cancel()` leaves `result()`, `collect()` and `get()` waiting forever | `p7_failures/failures.py` case 4 → "HUNG after cancel (3.0s)" | `core/engine.py:140` catches only `Exception`; `cancel()` at `:371-374` never sets `_done` |
| F04 | P0 | bug | `asyncio.wait_for(engine.run())` timing out does not cancel the graph; downstream side effects still run | `repros/timeout_leak.py` → `slow finished: 1 \| after ran: 1` | `core/engine.py:780-793` (no cancel-on-CancelledError) |
| F05 | P0 | bug | Structured-field coercion is silently wrong: an int that fails to parse stays a str, `bool("maybe")` is True, and `: list` is a str for one element | `repros/parse_probe.py`; p1 edge A | `providers/parsing.py:377, 382-394` |
| F06 | P0 | bug/API | A misspelled output key (`m["totl"]`) is accepted; the consumer silently gets its default. (Input-name typos are caught at build time, nicely.) | `repros/typo_key.py` → `typo_output -> total=0 []` | `core/ops/base.py:479-481` (no check); `graph/validation.py` has no output-key check |
| F07 | P1 | bug/docs | `LLMOp(stream=True)`: the final frame's `content` is the full text, so delta consumers send it twice | `p8_stream/stream_tail.py` → `True` | `providers/ops/llm.py:1134-1140` |
| F08 | P1 | bug | Websocket text frames reach the graph undecoded (HTTP decodes JSON; ws sends dicts as JSON), so one doors graph breaks on ws and the client gets silence | p8 ws run: `AttributeError: 'str'...`, client got 0 messages | `app/serve/asgi.py:149-155` vs `:121-128`, `serve/app.py:314-316` |
| F09 | P1 | bug | A malformed JSON body runs the graph with a raw string instead of returning 400; failure responses carry no trace id | `curl -D -` on `/chat -d '{oops'` | `app/serve/app.py:314-318, 320-335` |
| F10 | P1 | debuggability | `$errors` keeps the first failure per op only: no count, no item context | `p4_pipeline/probe_errors.py` (2 failures → 1 entry) | `core/states/state.py:366-380` |
| F11 | P1 | debuggability | Trace and workflow names come from the wrong variable: services `engine`, jobs `params`, scripts `out` | studio `/runs` output; `$errors` key `out.up` | `core/utils/auto_name.py:155-172`; `app/jobs/job.py:177-180`; `app/serve/app.py:98` |
| F12 | P1 | debuggability | Script runs are not traced by default; `trace="local"` inside a project writes to `/tmp/operonx_traces/adhoc`, which studio does not read | `p6_flow/traced.py`; the meta.json was found under `/tmp/operonx_traces/adhoc/engine/` | `telemetry/consumers/local.py:83, 86-104` |
| F13 | P0 | missing | No op-level `retry` or `timeout` (`TypeError` on `@op(retry=, timeout=)`); the Job-level `retry:N` has no backoff | `p7_failures/failures.py` case 1; `job_retry.py` | `core/ops/base.py` run path (~`:1229`) |
| F14 | P1 | missing | No durable checkpoint or resume of a run; the checkpointer is observe-only and in-memory only | `checkpoint/base.py` docstring; only `checkpoint/memory.py` | `checkpoint/` |
| F15 | P1 | API/docs | HITL: `handle.interrupts` ≠ InterruptOp interrupts; the id needs `operonx.checkpoint.bind_interrupt_bus`, which the guide does not mention | `p7_failures/hitl.py` (first version: `IndexError`) | `core/engine.py:229-243`; guide `01-ops.md` "Flow ops" |
| F16 | P1 | bug/API | A destructive tool with no approval listener waits 300 s silently, then raises an empty `TimeoutError` | `p2_agent/edge_shop.py` `no_approval RAISED 15.0s TimeoutError` | `agents/graphs/react.py:179`, `dispatch.py:341, 375` |
| F17 | P1 | missing | Tool arguments are not validated against the declared schema; the schema must be hand-written even with type hints | `edge_shop.py` `wrong_type` → `'<' not supported ...` | `agents/graphs/dispatch.py` (no schema check); `agents/tool.py:70-90` |
| F18 | P1 | missing | Parsing is brittle: JSON with preamble or trailing text fails, XML (the default) fails on `&`, no native JSON-schema or pydantic mode | `repros/parse_probe.py` (`json_preamble`, `json_fence_tail`, `xml_amp`) | `providers/parsing.py:198-208, 234-241` |
| F19 | P1 | debuggability | A failed structured step makes the next templated step raise `PromptError`, so the root cause is hidden. None-valued template variables count as missing | p1 edge C | `providers/ops/llm.py:1745-1759`; no failure propagation from `error` |
| F20 | P1 | API | Literal braces in a prompt fail on every call, not at build time, and the message has no `{{ }}` hint | `repros/prompt_braces.py` | `providers/ops/llm.py:1752-1759` |
| F21 | P1 | missing | RAG kit gaps: no upsert/ingest op, chunker or BM25/hybrid; memory stores are not persistent; hub key ≠ resource name; searching an empty index is silent | `p3_rag/run_rag.py`, `edge_rag.py` | `providers/ops/` (no upsert op), `vector_stores/faiss.py` |
| F22 | P1 | docs/bug | Seeding a loop cell from an input is undocumented; a Ref in `declare()` fails only at run time; the 1000-iteration cap is silent and has no setting | `repros/loop_seed.py` | `core/ops/_edges.py:58-118`; `graph/cycle_rewrite.py:407`; `task_scheduler.py:1044` |
| F23 | P2 | docs/API | `out[<cell>]` holds per-item op outputs, not the reduced cell; the cell needs `out["$state"].get(engine.name, key)`; Job sinks never get the reduced value | `p4_pipeline/run_pipeline.py`, `out/analytics.jsonl` | `core/engine.py` result assembly; guide `03-control-flow.md:349` |
| F24 | P1 | API | 21 reserved op keywords (`id`, `name`, `description`, `start`, `stream`, `inputs`, …) collide with domain parameter names | p6 `collatz_loop(start)` warnings | op constructor kwargs in `core/ops/base.py` / `_params.py` |
| F25 | P1 | missing/DX | No fake or scripted LLM provider (each template hand-rolls an HTTP server in conftest); `operonx init` in a uv project creates a nested project that resolves **PyPI** operonx, not the local checkout | `p1_chain/uv.lock` (`source = registry`) before `uv add --editable` | `cli/init.py`, `cli/templates/*/tests/conftest.py`, `providers/llms/factory.py` |
| F26 | P1 | noise | A "Slow op" WARNING fires for every op over 100 ms (every LLM call); logs go to **stdout**; the level comes from the generic `LOG_LEVEL` | every run; `2>/dev/null` still printed logs | `core/ops/base.py:1447`; `core/loggings/handlers/console.py:163, 193`; `config.py:79` |
| F27 | P1 | debuggability | Error strings are full tracebacks starting with operonx frames; `meta.json` has no status; HTTP replies have no trace id; studio shows the unrendered prompt | studio `/trace`, `/op/ex`; `curl -D -` | `core/ops/base.py:1311-1316`; `serve/app.py:320-337`; `telemetry/consumers/local.py` |
| F28 | P1 | debuggability | The agent trace is mostly plumbing (70 records for 3 model calls + 3 tool calls); contexts are unreadable; turns are out of order | studio `/trace/{run}/tree` on p2 | `agents/graphs/react.py`, `dispatch.py` (no internal-op marker) |
| F29 | P2 | bug/docs | `model_used` is the resource key, not the model | p3 output `'model_used': 'assistant'` | `providers/ops/llm.py:186` vs `:1140, :1244` |
| F30 | P2 | dead code | `operonx pack` (the dropped Rust runtime) is still a top-level command and dumps a traceback on any looping graph | `operonx pack flow.graph::router` → `NotImplementedError` | `cli/pack.py`; `graph/graph_op.py:857` |
| F31 | P2 | DX | `serve` has no `--port`, `--host` or `--reload`; help text says `[[serve]]`; binds 0.0.0.0 by default; no `/health` | `operonx serve --help`; curls | `cli/serve.py`; `app/declare.py:86` |
| F32 | P2 | docs | Stale `Operon` docstring (`with GraphOp(name=...)`, `LLMOp(inputs={"prompt":...})`, `result["response"]`); 3 import paths for the same ops; the template imports `LOGGER` from `operonx.core.loggings` | `core/engine.py` class docstring (~`:376-395`); `examples/python/ex16_rag_pipeline/main.py` vs guide | docs |
| F33 | P2 | missing | Job `retry:N` retries immediately; a timed-out item has `trace_id: None` | `p7_failures/job_retry.py` | `app/jobs/runner.py` |
| F34 | P2 | DX | Job run ids are in UTC while the console logs local time | `operonx run` output | `app/jobs/` |

**What was good**, so it is not lost:
- The chain, the agent and the jobs all worked on the first try with a real model.
- Tool failure messages to the model are excellent.
- An input-name typo is caught at build time with a clear message.
- Job resume, `retry:N`, `item_timeout` and `record` work, and the run records are clean.
- `.parallel(max=N)` honours its cap.
- Branch merging and back-edge loops are solid, and Ref-vs-Ref conditions are fixed.
- In studio, the status filter and per-op inputs and outputs are genuinely useful.

**Time lost** (approximate, from this session):

| Findings | Time lost |
|---|---|
| F01 | ~15 min |
| F02 | ~10 min |
| F22 | ~10 min |
| F12 | ~10 min |
| F15 | ~8 min |
| F08 | ~5 min |
| F19 | ~10 min |
| F03 / F04 | ~10 min |
| Everything else | under 5 min each |

---

## (c) Prioritized OperonX refactoring plan

Each item lists the code to change and a test that fails today and passes after the fix. The repro scripts in
`docs/roadmap/evidence/repros/` should be turned into these tests.

### P0: silent wrong results, hangs and leaks

1. **One failure-propagation rule for subgraphs** (F02, and part of F09)
   - **Change:** in `core/ops/graph/graph_op.py:775-780`, when the child scheduler recorded an op error and the
     subgraph's declared outputs are all None, do not yield. This mirrors the streaming branch at `:782-785`,
     so successors are skipped exactly as in the flat case. Also record a `"<graph>.<sub>"` entry in `$errors`
     that points at the child error.
   - **Test:** `tests/internal/core/ops/graph/test_subgraph_failure_stops_successors.py`: the nested graph from
     `subgraph_failure_leaks.py` asserts that `"z" not in out`. A serve test (Starlette `TestClient`) of a doors
     graph whose subgraph op raises asserts status 500, not `200 null`.

2. **Cancellation that ends the run and wakes every waiter** (F03, F04)
   - **Change:** in `core/engine.py`:
     - `_pump` (`:108-145`): catch `BaseException`, set `_done=True` and `_error=CancelledError()`, and call
       `_resolve_all_waiters`.
     - `cancel()` (`:371-374`): mark the handle done and notify `_cond`.
     - `Operon.run` (`:780-793`) and `stream()`: wrap the await in `try/except BaseException`, call
       `handle.cancel()`, then re-raise.
   - **Tests** in `tests/internal/core/engine/test_cancel_wakes_waiters.py`:
     - `start()` → `cancel()` → `await asyncio.wait_for(handle.result(), 1)` raises `CancelledError` and does not
       time out.
     - `test_run_timeout_cancels_graph`: `timeout_leak.py` asserts `after == 0` two seconds after the
       `TimeoutError`.

3. **`.collect()` must not store sibling outputs again** (F01)
   - **Change:** in `_flush_collect` (`core/ops/graph/task_scheduler.py:906-937`), build `merged` only from
     `_collected_vars(g, src, dst)`. Store it so it does not re-fire push-refs into `PARENT` cells: either a
     consumer-scoped write, or `store_result(..., push=False)`.
   - **Test:** `tests/internal/core/ops/graph/test_collect_reducer_no_double_write.py`:
     `repro_collect_reducer.py` asserts the cell equals `['item0','item1','item2']` for the graphs with and
     without the collect. Add a dict-reducer variant that must not raise `ReducerError`.

4. **Structured output that fails loudly** (F05, F18)
   - **Change** in `providers/parsing.py`:
     - In `convert_type` (`:348-394`), an int, float or bool that fails to coerce returns a field error, so
       `max_retries` fires. Bool accepts only `true/false/yes/no/1/0`. A `list` hint wraps a lone scalar.
     - `_strip_fence`/`parse_json` (`:198-208`) should take the first fenced block anywhere, or else the first
       balanced `{...}`/`[...]`.
     - `parse_xml` (`:211-241`) should retry once with bare `&` escaped.
   - **Also:** add `LLMOp(fields=..., parser="json_schema")`, which sends `response_format={"type":
     "json_schema", ...}` built from `fields`.
   - **Test:** `tests/internal/providers/test_parsing_strict.py`, using every `parse_probe.py` case:
     `urgency: int` from `"high"` sets `error`; `ok: bool` from `"nah"` is False or an error; `tags: list` from
     one element gives `['a']`; `json_preamble`, `json_fence_tail` and `xml_amp` parse.

5. **Build-time output-key validation** (F06)
   - **Change:** in `core/ops/graph/validation.py` `validate_graph`, check every `Ref(op, key)` against
     `op.outputs` when the op's outputs are statically known (dict-literal keys, not `return_keys=`/dynamic).
     Raise `ValueError("'totl' is not an output of make(); outputs: {'total'} — did you mean 'total'?")`.
   - **Test:** `tests/internal/core/ops/graph/test_unknown_output_key.py`: `Operon(typo_output, ...)` from
     `typo_key.py` raises at build time.

6. **Op-level `retry`, `backoff` and `timeout`** (F13, F33)
   - **Change:** add `retry: int`, `retry_on: tuple[type[Exception]]`, `backoff: float` and `timeout: float` to
     `@op`/`BaseOp` (`core/ops/base.py` run path around `:1229`, reserved keywords in `_params.py`). On a timeout,
     record a `TimeoutError` in `$errors` and skip the successors. Reuse the same backoff for Job `retry:N`
     (`app/jobs/runner.py`).
   - **Tests:** `tests/internal/core/ops/test_op_retry_timeout.py`:
     - a flaky op (fails twice) with `retry=3` returns `y`, and the attempts are recorded in the trace;
     - `timeout=0.2` on a 5 s op gives `$errors["...slow"]` containing `TimeoutError` within 0.4 s, and `after`
       does not run;
     - a generator op with `timeout=` applies it per yield.

### P1: debuggability and the rough edges every user hits

7. **Structured `$errors` and less traceback noise** (F10, F19, F27)
   - **Change:**
     - `state.record_op_error` (`core/states/state.py:366-380`) keeps `{type, message, count, first_ctx}`
       per op. The full traceback stays in the trace.
     - The `base.py:1311-1316` formatter drops operonx frames.
     - When a structured `LLMOp` returns `error`, add it to `$errors` as well.
     - The serve layer (`app/serve/app.py`) adds an `x-operonx-trace-id` header to every reply and a
       `trace_id` field to the 500 body.
     - `meta.json` gets `status` and `errors`.
   - **Tests:** `probe_errors.py` gives `count == 2`; a `TestClient` reply has the header; the p1 edge-C
     scenario shows `ex`'s parse error in `$errors`.

8. **Correct run names** (F11)
   - **Change:**
     - `Job` (`app/jobs/job.py:174-180`) and the serve layer (`app/serve/app.py:98`) pass the graph's own name
       explicitly.
     - `auto_name` (`core/utils/auto_name.py:155-172`) drops the 6-line-up source scan, or limits it to the AST
       of the current statement.
     - When no assignment is found, fall back to the graph function's `__name__`.
   - **Test:** the job trace `meta.json` has `workflow_name == "enrich_one"`; `out = await
     Operon(g).run()` gives the name `g`; the service trace name is the graph name.

9. **Scripts trace into the project** (F12)
   - **Change:** `resolve_root` (`telemetry/consumers/local.py:86-104`) searches upward from the current
     directory for `operonx.toml` when no project root is set. Optionally, `Operon()` defaults to
     `trace="local"` when an `operonx.toml` is found.
   - **Test:** in a temporary project directory with an `operonx.toml`, `Operon(g, trace="local").run()`
     writes under `./.operonx/runs`, and the studio `/runs` endpoint lists it.

10. **A clear streaming contract** (F07)
    - **Change:** the final frame of `LLMOp(stream=True)` (`providers/ops/llm.py:1134-1140`) moves the full text
      to `full_content` (or sends an empty `content` with `final=True`). Update guide `01-ops.md`.
    - **Test:** `stream_tail.py` against the fake streaming server: `"".join(pieces)` equals the reply exactly
      once.

11. **Same decoding on every door** (F08, F09)
    - **Change:** `websocket(..., codec="json")` defaults to decoding text frames (`app/serve/asgi.py:149-155`,
      `app/declare.py:86`). The HTTP endpoint answers `400 {"error": "body is not JSON"}` before minting a run
      (`app/serve/app.py:314-318`). Have the websocket send an error frame when a run fails with no egress.
    - **Tests:** a `TestClient` websocket `send_json({"topic":"x"})` reaches the graph as a dict; `POST '{oops'`
      returns 400 and starts no run.

12. **Agent safety defaults** (F16, F17)
    - **Change:**
      - `dispatch.py:341-377`: if no interrupt subscriber is bound to the state, deny a gated call immediately
        with "no approval handler bound".
      - Validate arguments against `_tool_meta["schema"]` (types, required fields, enum) before `execute`, and
        answer the model with `argument 'qty': expected integer`.
      - `@tool` (`agents/tool.py:70-90`) infers `schema` from type hints and the docstring when it is omitted.
    - **Test:** `edge_shop.py` `no_approval` finishes in under 1 s with a denial tool message; `wrong_type`
      gives the schema error text; `@tool` without `schema=` builds `{"qty": {"type": "integer"}}`.

13. **Loop ergonomics** (F22)
    - **Change:**
      - `PARENT.declare` (`core/ops/_edges.py:58-118`) raises `TypeError` on a Ref value, with "declare the
        parameter's own name to seed it from the input". Document that pattern in guide `03-control-flow.md`.
      - Make `max_iterations` a `@graph` setting (`cycle_rewrite.py:407`).
      - Hitting the cap adds `$errors["<graph>.__loop__"] = "loop cap N reached"` and a WARNING
        (`task_scheduler.py:1044`).
    - **Test:** in `loop_seed.py`, case a raises at build time and case c has the cap entry in `$errors`.

14. **Free domain names from the reserved keywords** (F24)
    - **Change:** move the op-constructor settings (`name`, `id`, `description`, `inputs`, `outputs`,
      `start`, `stream`, …) behind a namespace, either `op.configure(...)` or a `_`-prefixed form, so that
      `get_user(id=...)` is just an input. Deprecate in 1.x and remove in 2.0.
    - **Test:** `@graph def g(start)` and `@op def f(id: str)` build with no warning and bind as inputs.

15. **A testing kit** (F25)
    - **Change:**
      - Add an `api_type: fake` LLM provider in `providers/llms/factory.py`, scripted with text, tool calls,
        HTTP status codes, delays and streaming. In effect, `common/fakellm.py` should ship.
      - Templates use it instead of their HTTP-server conftest (`cli/templates/*/tests/conftest.py`).
      - `operonx init` detects an enclosing uv project and offers `--editable PATH` or a workspace member.
    - **Test:** a generated `chat` template project passes `pytest` with no socket server; `init --editable
      ../Operon` produces a lockfile whose `source` is the path.

16. **Quieter, conventional logging** (F26)
    - **Change:** make the slow-op threshold configurable (`OPERONX_SLOW_OP_MS`), default it to DEBUG, and exempt
      `llm`/`io` ops (`core/ops/base.py:1447`). Log handlers write to stderr
      (`core/loggings/handlers/console.py:163, 193`). Use `OPERONX_LOG_LEVEL` and keep `LOG_LEVEL` as a fallback.
    - **Test:** a 300 ms async op produces no WARNING at the default level; `capsys` shows stdout empty after a
      failing run.

17. **An agent view in traces** (F28)
    - **Change:** mark the agent and dispatch plumbing ops `internal=True`, a new `OpExecution` field set in
      `agents/graphs/react.py` and `dispatch.py`. The studio tree collapses internal ops by default into
      `turn N → model (rendered messages) → tool calls`. Order rows by `wall_start`.
    - **Test:** a trace of the p2 run with internals collapsed has 6 primary records (3 model + 3 tool), in
      chronological order.

18. **RAG kit minimum** (F21)
    - **Change:** add `UpsertOp.of(resource=..., ids=, vectors=, metadata=)` and `DocPutOp` in
      `providers/ops/`, a `chunk_markdown`/`chunk_text` helper, and an in-memory BM25 retriever op. `VectorSearchOp`
      logs a WARNING (and sets `empty_index=True`) when the index has 0 vectors. Accept the bare `resource`
      name in `ResourceHub.get` when there is no ambiguity.
    - **Test:** `p3_rag` rewritten without any `ResourceHub` call; a search on an empty index sets a flag or
      `$errors` warning.

19. **Durable pause and resume** (F14, F15)
    - **Change:**
      - Add a file or sqlite checkpointer in `checkpoint/` that persists InterruptOp waits and cell writes, plus
        `Operon.resume(run_id, value)`.
      - Rename `handle.interrupts` to `handle.control_interrupts`, and add `handle.pending_interrupts` for
        InterruptOp. The guide must show how to obtain an `interrupt_id`.
    - **Test:** pause `hitl.py`, kill the process, resume in a new process with `value="yes"`, and get
      `result == "refunded"`; `handle.pending_interrupts` is non-empty while the op waits.

### P2: polish
- **F29:** `model_used` returns `completion.model` (`llm.py:1244`), with the resource key in a separate
  field.
- **F30:** delete `cli/pack.py` and the Rust serialize path (`graph_op.py:857`), since operonx-rs is dropped.
- **F31:** add `--port`, `--host` and `--reload` to `operonx serve` and fix the `[[serve]]` help text. Default
  the host to 127.0.0.1 in development, and add a `/healthz` endpoint.
- **F32:** rewrite the `Operon` class docstring (`core/engine.py`), settle on one import path per symbol, and
  make the templates import `LOGGER` from `operonx`.
- **F23:** add a documented `out["$cells"]` (or `handle.cell(name)`) for the final reduced values, and let a Job
  sink choose it.
- **F33** and **F34:** add backoff to Job retries, give a timed-out item its own trace id, and use one timezone in
  CLI output.

**Suggested order:** items 1–3 first (these are correctness bugs, and each has a ready repro), then 5 and 4,
which are the silent-value bugs. After that, 6 and 7–9, which are together the "can I debug it" work. Items
10–19
are independent and can run in parallel.
