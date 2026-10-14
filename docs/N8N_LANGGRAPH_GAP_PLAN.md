# What n8n and LangGraph do best, and what OperonX should take from them

**Status:** 2026-10-14. Research and plan. L1 is implemented with this plan; the rest waits for a go.

**Scope:** the operonx library (`/home/thanglq/Operon`) and operonx-studio (`/home/thanglq/operonx-studio`).

## How this was done

Every claim about OperonX below was checked in the code, not assumed:
- the library's own guide (`operonx/guide/01`–`09`);
- `core/policy.py`, `core/ops/_cache.py`, `durable/`, `checkpoint/` and `core/engine.py` (`resume`);
- the studio's app, access table and screens;
- three real projects: the callbot, meeting-prep and meeting-prep-brd.

n8n and LangGraph are compared on their strongest ideas, not their whole surface.

## The verdict in one paragraph

The operonx library already has most of what LangGraph is known for:
- retries, timeouts, error edges and fail-fast;
- one concurrency cap per run, and op caching;
- dynamic parallel fan-out (`.parallel(max=N)`);
- streaming in several modes, subgraphs, and cells with reducers;
- conversation threads, idempotency keys and live traces;
- **durable runs**: a journal, resume after a crash, approvals that park a run for days, and the same three durability modes LangGraph has.

**The gap is not in the engine. It is that none of the durability reaches the tools people use.** No project, Job, Service or studio screen runs with a journal, so in practice:
- a failed run is a dead record;
- an approval cannot be answered from the studio;
- nothing can be rewound.

The second gap is two LangGraph debugging ideas built on that journal: **retry from the failure** and **fork from a step** (its "time travel"). n8n's best ideas for the studio (pinned input, run one node, run history, alerts) are already covered or were added this week.

## What already exists (not recommended again)

| Idea | n8n / LangGraph | OperonX today |
|---|---|---|
| Retry and timeout per node | both | `retry=Retry(...)`, `timeout=` (guide 06) |
| Route a failure | n8n error output, LangGraph | `op.on_error(handler)`, `errors="raise"` |
| Concurrency limits | both | `max_concurrency=`, `.parallel(max=N, max_pending=P)` |
| Dynamic fan-out (LangGraph `Send`) | LangGraph | `.parallel()` over a stream |
| Node caching | LangGraph | `@op(cache=...)`, keyed by graph, op code and inputs |
| Streaming modes | LangGraph | several modes at once, `tasks` (guide 08) |
| Subgraphs | both | `@graph` in `@graph`, `invoke()` |
| Human in the loop | both | `InterruptOp`; parks with a journal; `resume(answers=)` |
| Durable execution and modes | LangGraph | `journal=`, `durability="async"/"sync"/"exit"` |
| Threads and short-term memory | LangGraph | threads: cells kept between runs |
| Long-term memory / store | LangGraph | operonx-agents sessions, operonx-kb |
| Triggers: webhook, schedule | n8n | Services (doors), `Job(schedule=)` |
| Sub-workflows | n8n | `invoke`, Steps |
| Rate limits | n8n | `rate_limit` |
| Pinned test data, run one node | n8n | studio Inspect: input picker and **Run this op** (#85) |
| Execution history, search | both | studio Runs: filters, tree, timeline, workflow replay |
| State per step | LangGraph Studio | studio step panel, state-as-change |
| Failure alerts ("error workflow") | n8n | studio Alerts: thresholds, webhooks |
| Evals, datasets | LangSmith | `operonx eval`, judges, review queues, online evals |

## Recommendations

### Library

**L1. Retry a failed run from the failed op.** *Essential · small · library.* **Implemented with this plan.**
- **Why:** this is n8n's "retry execution" and the first thing anyone wants after a fix. Today a failed op is journalled as a finished step, so `resume` restores it as finished and nothing reruns. A run that failed on a bug cannot be finished, even after the bug is fixed.
- **What:** `resume(run_id, retry_failed=True)` keeps every step before the first failure, drops that step and everything journalled after it, and runs from there.
  - What ran before the failure is not run again: a charge is not charged twice.
  - Work that ran after the failure runs again, as a resume after a crash does, with the same idempotency key.
  - After a code fix, add `allow_graph_change=True`, as for any resume across a change.

**L2. Durable runs inside Jobs and Services.** *Essential · medium · library (then studio).* The architectural item.
- **Why:** durability exists only for code that builds an `Operon` with `journal=` by hand. Jobs (one run per item) and Services (one run per request) never pass one. So:
  - a job item that dies mid-way starts over;
  - a served approval flow has nowhere to park.
- **What:** one switch in `operonx.toml`, `[durable] journal = "sqlite:.operonx/journal.db"`, which Jobs and Services pick up:
  - a job item resumes where it stopped, and a failed item can be retried from its failed op (L1);
  - a request-response Service with an `InterruptOp` parks and answers later.
  - A **door** graph (a live stream, such as the callbot) stays as it is: its items come from a live connection that no journal holds. `resume` already refuses one.
- **Design (2026-10-14, audited before building):**
  - **The switch.** `operonx.toml`:

    ```toml
    [durable]
    journal = "sqlite:.operonx/journal.db"   # relative to the project
    durability = "async"                     # optional: async | sync | exit
    ```

    Parsed into `Manifest.durable`. A typo fails at load. Absent, nothing changes.
  - **Who gets it:** doorless jobs and doorless request-reply services. A graph with a door (`ingress`), such as a websocket call or a job fed through a session, never does: `resume` refuses doors, and its items come from a live connection. An `Operon` handed to a job as is keeps its own `journal=`.
  - **Job items:**
    - Each item's run id is `<job run id>:<item key>`, so a job that processes the same key every night never replays yesterday's run.
    - `Job(retry=)`: attempt 2 of a failed item is `engine.resume(run_id, retry_failed=True)` (L1), so what ended before the failure (a charge) is not done again.
    - `operonx run NAME --resume`: an item the previous run left mid-way, or failed, resumes from the journal under the previous run's id. A finished item is skipped, as today. An item with no journal run starts fresh.
  - **Services:**
    - Each request's run is journalled under its trace id.
    - A run that parks on an `InterruptOp` replies with `{"run_id", "$interrupted": [...]}` instead of an answer.
    - `POST <path>/resume` with `{"run_id", "answers": {id: value}}` continues it (`engine.resume(answers=)`) and replies as the first request would have. operonx adds this route unless the service declares its own `resume=` graph (operonx-agents' approvals keep theirs).
  - **Cost:** the journal writes each step (`durability="async"`: a background writer). Measured on a request-reply service before it ships, and stated in the guide.
  - **Studio:** S1 (inbox) and S2 (retry button) use this same journal through the project's bridge.
- **Done (2026-10-14).** Built as designed. Measured (8-op chain, one run at a time): no journal 1.4 ms p50; SQLite `async` 3.85 ms p50 and 8.6 ms p95; `exit` 3.7 ms; `sync` 7.3 ms. At 400 runs at once, throughput drops about 10×.
  - Profiled: the journal starts a writer thread per run (`recorder.py`), and SQLite has one writer, so commits serialize.
  - **L2b, done (2026-10-14):** one shared writer per journal, batching the steps and ends of all its runs into one transaction (`_SharedWriter`). A batch nobody waits on (`async` steps) lingers up to 2 ms, so a run's steps and end go in one commit (1.6 commits per run, against 2.1 with a thread per run and 4.1 without the linger).
    - Measured, pinned A/B against main: at 400 runs at once, 10.2–10.9 ms → 3.6–4.0 ms per run (no journal: 1.2 ms). One run at a time: `async` 3.9 → 3.8 ms p50, `exit` 3.6 → 3.2 ms, `sync` 7.5 → 7.2 ms p50 and 13.8 → 8.8 ms p95. Without a journal: unchanged.

**L3. A shared journal: Postgres.** *Essential for more than one machine · medium · library.*
- **Why:** `SqliteJournal` is one file on one machine. LangGraph ships a Postgres checkpointer for this reason: a run parked on one worker is answered on another. The journal is a `Protocol`, and the codec is already JSON, which is what a Postgres table needs.
- **What:** `PostgresJournal(dsn)` with the same tests as `SqliteJournal`. Do it after L2, when a real deployment needs it.

**L4. Fork a run from a step ("time travel").** *Important · medium · library.*
- **Why:** LangGraph's most-loved debugging idea is to rewind to a step, change what it produced, and let the rest run. For agents this answers "would it have done better if the tool had said X?" without replaying the whole conversation. The studio's Run this op covers one op; fork covers what follows it.
- **What:** `fork(run_id, at=(op, ctx), outputs={...}) -> new run id`, which works like this:
  - it copies the journal's steps up to that op's end into a new run, with the op's outputs replaced;
  - it resumes the new run;
  - the original run is never changed.
- **Built (2026-10-14):** `Operon.fork(run_id, at=, outputs=, ctx=, occurrence=, allow_graph_change=)` returns the new run's handle. `at` must answer once (not a generator or a graph). The op's journalled end is rewritten: its event, its own output cells, and the copies it wrote further (a write in that step holding an output's old value, unless an unchanged output holds the same value). A resume with no kept trace records its replayed steps, so a fork's trace (and a killed run's resume) is the whole run. The bridge's `{"op": "fork"}` forks with the owner's engine as the code is now, filed as a playground run (`toy=fork`).

**L5. Durable timers: wait until a time.** *Nice to have · medium · library.*
- **Why:** n8n's Wait node handles "follow up in two days". With L2, `InterruptOp` can already wait for a person; a timer would let a run wait for a time without a person or a scheduler.
- **What:** later. Until then, a Job with `schedule=` that resumes parked runs covers it.

### Studio

**S1. Waiting approvals: answer a parked run.** *Essential once L2 lands · medium · studio.*
- **Why:** both n8n (Wait and Form nodes) and LangGraph Studio let a person answer an interrupt from the UI. With L2, a parked run is just a status no screen shows.
- **What:**
  - Runs shows **interrupted** runs, with a filter for them;
  - the run page shows the question (`payload`) with **Approve** / **Reject** or a JSON answer;
  - answering calls `resume(answers=)` through the project's bridge.
- **Found while designing (probe, 2026-10-14):**
  - A parked run's trace says `status: ok`. Nothing in any store tells it apart from a finished run.
  - A resume records its trace under the same id (the run id), holding only what ran after the resume. The local store replaces the run's folder with it, and ClickHouse replaces the node rows by `(trace_id, seq)`. Answering a run deletes the record of everything before the question. Probe: parked trace `[p, s, ask]`, after the answer `[ask, a]`.
  - The resume cannot take a new trace id. The run id is the trace id, and interrupt ids and `run_context().idempotency_key` are derived from it. A new id would give an op that charges a card a new idempotency key.
- **Design:**
  - **S1a. A stopped run's trace says so.** The trace of a parked run has status `interrupted` (`drained` for a drain) and `metadata["interrupted"] = [{id, op, payload}]`. An error still wins.
  - **S1b. A resumed run's trace is the whole run.** When a journalled run stops (parked, drained or failed), the engine saves its trace nodes in the journal. This happens only on the stop path, so a run that ends pays nothing. A resume loads them into its trace before it runs: wall times are kept, the trace starts when the run first started, and a loaded node that did not end ok gets the suffix `@stopped` (its op runs again under the plain id). Every store then gets one complete trace and replaces the old one, so local, ClickHouse and Langfuse need no change. A failed run's retry (S2) and a job item retried by resume get the same.
  - **S1c. The bridge answers.** `{"op": "parked"}` lists the journal's interrupted runs: run id, owner (the service or job whose compiled graph has the run's fingerprint), inputs, times, and questions (`[{id, op, payload}]` from the PARKED steps). `{"op": "answer", "run_id", "answers"}` resumes the run with the owner's engine and its real trace consumers, filed as the original run was. It replies with the new status, plus the questions if the run parked again.
  - **S1d. Studio.** The Runs list gets a **Waiting** filter: the trace's status (S1a), so listing starts no bridge, and an answered run leaves it because its trace is written again (S1b; the run stores read a rewritten run again). The bridge checks the journal when the answer is sent, so a run answered elsewhere is refused (409), not run twice. The run page of a waiting run shows each question with its payload: **Approve** sends `"approve"`, **Deny** sends `"deny"` (operonx-agents' words), and **Answer** sends any JSON. The page then reloads the run, which now holds the whole story.
- **Gates:**
  - No cost on a run that ends: the A/B benchmark (8 ops, 400 runs at once, with and without a journal) matches main.
  - The stop path is measured and stated in the guide.
  - Tests: a parked trace's status and questions; after an answer, the trace holds every node once, in order, with the parked `ask@stopped`; `retry_failed` gets the same; the bridge's `parked` and `answer` against a service and a job.

**S2. Retry from failure.** *Essential · small · studio (needs L1, L2).*
- **What:** a failed, journalled run's page gets **Retry from the failure**, which calls `resume(retry_failed=True, allow_graph_change=True)`. The new attempt lands in the same run, so it reads as one story.
- **Built (2026-10-14):** the bridge's `{"op": "retry", "run_id", "service" | "job"}` resumes with the owner's engine as the code is now (found by owner, not fingerprint: the fix changed the graph). A journalled run's trace has `metadata["durable"]`, so the button shows only where it can work, and the retry's trace is the whole run (S1b). Found while building it: `retry_failed` left the failed steps in the journal, so a second retry or `--resume` ran the op again. `Journal.truncate` now drops them as the retry starts.

**S3. Fork from a step.** *Important · medium · studio (needs L4).*
- **What:** on a run's step panel, **Change this output and continue**: an editor with the recorded output, then L4's fork. The new run opens beside the original, and Compare runs (already built) shows the difference.

**S4. Pause before an op, in the Playground.** *Nice to have · small to medium.*
- **Why:** LangGraph Studio's `interrupt_before` breakpoints.
- **What:** later. Run this op and fork cover most of the need.

**S5. What changed in the code between two runs.** *Nice to have · small.*
- **Why:** n8n keeps workflow versions. OperonX is code-first and a run already records its commit.
- **What:** Compare runs could show `git diff` between the two runs' commits.

## Order

| # | Item | Where | Impact | Effort | Status |
|---|---|---|---|---|---|
| 1 | L1 retry from the failed op | library | high | small | **done with this plan** |
| 2 | L2 journal in Jobs and Services | library | high, architectural | medium | **done** (#161; L2b shared writer #162) |
| 3 | S1 answer parked runs | studio | high once L2 is in | medium | **done** (#163, #164, studio #87) |
| 4 | S2 retry-from-failure button | studio | high | small | **done** (#165, studio #88) |
| 5 | L4 fork from a step | library | medium-high | medium | **done** (#166) |
| 6 | S3 fork in the studio | studio | medium-high | medium | **done** (studio #89) |
| 7 | L3 Postgres journal | library | high at scale | medium | when needed |
| 8 | S5 code diff between runs | studio | low-medium | small | |
| 9 | L5 durable timers, S4 breakpoints | both | low | medium | not now |

## Considered and left out, on purpose

- **n8n's integration catalogue (400+ nodes), its expression language, and drag-and-drop editing.** OperonX is code-first; an op is a Python function, and the assistant writes new ones.
- **n8n's credential vault.** Resources plus `${VAR}` from `.env` (guide 05) already do this, and the studio shows what is missing.
- **LangGraph's Functional API.** `@op` / `@graph` already are the functional form.
- **The LangGraph Platform** (hosted assistants and cron API). Services, Jobs and `operonx-serve` cover it on the user's own machines.
- **A second checkpoint system.** `operonx.checkpoint` (observation) and `operonx.durable` (the journal) already split the work: L1–L4 build on the journal alone.
