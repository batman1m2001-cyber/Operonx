# R4 — the platform: threads, a durable run queue, leases

Status: plan (2026-10-05). Roadmap: `docs/roadmap/ROADMAP.md` "R4 (W4): platform";
gap analysis `docs/roadmap/track2_langgraph_gap.md` rows 25–26 and "Phase 4".

## 1. What is missing today (audit)

| Gap | Where | Today |
|---|---|---|
| A webhook event dies with the process | `app/serve/triggers.py` `WebhookTransport.accept` | the `202` is sent once the event is an in-memory `HttpSession`; a restart before the run ends loses it with no trace of it anywhere |
| N replicas fire a schedule N times | `ScheduleTransport.sessions` | an in-process clock; no lease, no leader anywhere under `operonx/app` |
| No run queue across replicas | — | each replica runs what it accepted; nothing hands work to another worker |
| `thread_id` is a label | `engine.start(session_id=…)` → `RunHeader.thread_id` | nothing is carried from one run of a thread to the next |
| A second message on a busy thread | serve doors | both runs go at once; no policy |
| A stream that drops loses the rest | `_stream_reply` (`app/serve/app.py`) | the run goes on (a disconnect never cancels), but its frames are gone for the client |
| Who learns a webhook run ended | — | the sender got a `202` and a run id, and nothing after |

What R4 must respect: services read through an `ingress` door, so their runs are **not
resumable** (R3 D11). A durable event is therefore re-run from its payload — at least once —
not resumed from a journal. Runs that are resumable (no door: jobs, `engine.start`) keep R3.

## 2. Decisions

| # | Question | Decision | Why |
|---|---|---|---|
| D1 | Where queued work lives | `operonx.app.queue`: protocol `RunQueue` (`put`, `claim`, `renew`, `finish`, `get`, `items`, `fire_once`, `request_stop`); `SqliteQueue` (one host, many processes: WAL + `BEGIN IMMEDIATE`) and `PostgresQueue` (`SELECT … FOR UPDATE SKIP LOCKED`). One contract suite runs both | the roadmap's table; SQLite so a single box needs no server, Postgres for replicas |
| D2 | A row | `id, service, thread_id, payload, meta, status (queued·running·done·failed·stopped·discarded), attempts, max_attempts, available_at, lease_until, worker, stop (None·interrupt·rollback), error, created_at, updated_at`. Payload and meta are JSON — what came over HTTP is JSON already | inspectable with `psql`/`sqlite3`; no pickle across trust boundaries |
| D3 | Delivery | at least once. A claim takes a lease (`lease_s`, default 30); the worker renews it every `lease_s/3` while the run goes on; a lease that lapses (the worker died) makes the row claimable again, `attempts + 1`; past `max_attempts` (default 3) the row is `failed` with the last error. The run id is the row id on every attempt, so its trace and `run_context().idempotency_key` repeat — what an external call deduplicates on | a crash must not lose an event; exactly-once needs the side effect's own key, which R2 already gives |
| D4 | One thread at a time | `claim` skips a row whose `thread_id` has a `running` row — across replicas, in the same statement | the queue is where replicas meet; an in-process lock would not see the other replica |
| D5 | Webhook | `Service(kind="webhook", queue="runs.db")` (or `queue={url="postgresql://…"}` in `operonx.toml`): `accept` writes the row, **then** answers `202 {run_id}`. Each replica's transport claims rows and runs them as sessions. Without `queue=` nothing changes | `test_webhook_event_survives_restart` |
| D6 | Schedule | with `queue=`, ticks are aligned to the wall clock (`every`: multiples of the period since the epoch; `at`: the day's slot) and a tick runs only on the replica whose `fire_once(name, slot)` inserted the slot's row (unique key). Skip-while-running stays per replica | aligned slots are the same on every replica, so one row per slot means one run per tick |
| D7 | Threads | `Operon(g, journal=…, carry=["messages"])`: a run started with `thread_id=T` seeds each carried *declared* cell from T's last saved values; a run of T that ends `ok` saves them (journal table `threads`). A cell not declared is refused at build | `test_thread_carries_cells_between_runs`; declared cells are the graph's state by name already |
| D8 | Second message on a thread | `Service(multitask="enqueue" (default with queue) · "reject" · "interrupt" · "rollback")`; thread from `?thread_id=` or header `x-operonx-thread`. `reject`: `409` while T has a queued/running row. `interrupt`: the running row gets `stop="interrupt"` — its worker, on its next renew, drains the run (cancels when it has no journal) and marks it `stopped`, keeping what it did; the new row then runs. `rollback`: the same with `discarded` | LangGraph's `MultitaskStrategy`; the stop travels through the row, so it reaches a run on another replica |
| D9 | Stream reconnect | each streamed frame gets a sequence number (SSE `id:`); the process keeps a run's frames (bounded, 15 min after it ends). `GET <path>?run_id=R&after_seq=N` (or `Last-Event-ID`) replays frames after N, then follows live. Process-local: a reconnect must reach the same replica (sticky) — documented | the run outlives the connection already; only its frames were lost |
| D10 | Completion callback | `Service(callback_hosts=["hooks.example.com"])` lets a request carry `?callback=https://…`; when the run ends the worker POSTs `{run_id, status, output, errors}` (3 tries, backoff). The URL is stored on the row, so the worker that finishes the run calls it, whichever replica accepted it. A host not on the list is refused with `400` | callers that got a `202` learn the end; the allowlist stops a sender from aiming the server at internal URLs |

## 3. Phases

| Phase | Ships | Gate |
|---|---|---|
| **R4a** | D1–D7: queue (SQLite + Postgres), durable webhook, schedule lease, threads `carry` | contract suite on both queues; `test_webhook_event_survives_restart` (subprocess killed mid-run, a second process runs the event); `test_schedule_fires_once_across_two_workers`; `test_thread_carries_cells_between_runs` (two processes); without `queue=`/`carry=` the serve and callbot suites unchanged |
| **R4b** | D8–D10: multitask policies, stream reconnect, completion callbacks | `test_second_message_policy_*` (four), `test_stream_reconnect_after_seq`, `test_callback_fires_after_restart` |

## 4. Not in R4

- Moving `Job` runs onto the queue (jobs already resume by key; a later step).
- A queue UI in Studio (R4 exposes `items()`; Studio reads it later).
- `history(run_id)` / `fork(...)` — R5.
