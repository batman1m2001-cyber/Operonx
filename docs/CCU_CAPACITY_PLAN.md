# CCU capacity: see the backlog, then lift the ceiling

Status: part 1 **done** 2026-10-14 (phases 1–4; phase 5 not needed), results
below. Part 2 (a latency dashboard, then 50–100 calls on the fewest workers)
**in progress**: see the end. Driven by the callbot (educa-reminder-agent,
`refactor/operonx-studio`), but every generic part lands in operonx.

## Why

A real-audio load test of the callbot on operonx 1.23.1 (20 ms packets in
real time, recorded speech, turn-taking, 4 workers as in production):

| Calls | VAD hears the caller stop, p50 / p95 | Bot replies after the caller stops, p50 |
|---|---|---|
| 10 | 0.37 / 0.41 s | 0.97 s |
| 20 | 0.8–5.8 / 11–12 s | 1.3–5.5 s |
| 30 | 8.1 / 25 s | 5.6 s (many replies are silence reprompts) |
| 40 | 17.6 / 35.9 s | 5.4 s, 13 turns unanswered |

STT (p95 ~240 ms), the LLM (`agent_turn` ~170 ms) and TTS (first audio
< 100 ms) stayed fast at every level. The time is lost before VAD:

1. **A worker holds about 5–6 calls.** The VAD thread and the event loop
   share one GIL. Under load one `VadState.push` costs ~3× what it costs
   alone (p95 14 ms against 4.6 ms; reproduced offline with a second
   GIL-busy thread). At 6 calls the VAD thread is 83–94 % busy and handles
   ~210 of the 300 packets a second; the rest wait on the
   `audio_in → detect_speech` edge, which has no bound. A shorter
   `sys.setswitchinterval` does not help.
2. **Calls spread unevenly over the workers.** Uvicorn's workers share one
   listening socket. 20 calls landed 6 / 5 / 9 / 0; with 8 workers,
   1 / 9 / 2 / 2 / 5 / 1. The busiest worker collapses first.
3. **The loop pays for every packet's whole state.**
   `State.release_context` visits every cell of the call's state (~476) to
   drop one transient packet; it was the largest single function in the
   profile.

**None of this was visible in ClickHouse or the studio.** It took
py-spy, a monkeypatched server and an offline reproduction. Phase 1 fixes
that first, so every later phase is measured in the traces.

## Target

40 calls on one host, production shape (the worker count may change; it
is config):

- VAD lag (packet sent → VAD processing it) p95 ≤ 0.6 s at every level up
  to 40;
- reply after the caller stops p50 ≤ 1.2 s;
- no silence reprompt caused by lag;
- at 5 calls, nothing worse than today (greeting p95 364 ms, reply ~1 s).

## Hard gates (every phase)

- **No cost on the success path** for an operonx change: the pinned A/B
  (8-op chain, 400 runs at once, `taskset`, against main) matches main.
- **The op structure stays.** The callbot graph keeps its ops, edges and
  names; studio views and traces look the same.
- Each phase re-runs the real-call test (`ccu_real.py`, 10/20/30/40) and
  states its numbers from ClickHouse, not from a profiler.

## Phase 1: see it in the traces

Everything the investigation needed outside the traces becomes trace data.

- **T1 (operonx): edge backlog.** For each sequential edge
  (`seq_queues[(src, dst)]`), the run records its high-water mark and the
  longest any item waited in it, as `metadata["backlog"] =
  {"audio_in>detect_speech": {"max": 612, "max_wait_ms": 11800}}`. Only
  edges that ever held more than one item are written. Kept on the deque
  append/pop that already happen; no new task, no timer.
- **T2 (operonx): which worker ran it.** `metadata["worker"] = {"pid",
  "active_runs"}` (runs of that engine in flight when it started), for a
  served run. Two integers at start.
- **T3 (operonx): event-loop lag over the run.** `metadata["loop_lag_ms"]
  = {"max", "p95"}`. One beacon per process (a 50 ms heartbeat, started
  with the first served run) keeps a ring of the last minutes; a run reads
  its window when it ends. The callbot's own `[loop-beacon]` log stays.
- **T4 (callbot): VAD lag.** Each `detect_speech` frame carries `lag_ms`
  (now minus the telco timestamp of the packet that closed the segment),
  and the call's end record carries the worst `lag_ms` of the call. Read
  in the trace: "the bot heard this 11.8 s late".
- **Studio:** the run header shows backlog, worker and loop lag when
  present (they are run metadata already); Monitor gets nothing new yet.
- **Gate:** the A/B matches main. A 20-call run shows, in ClickHouse and
  on the run page, the backlog on `audio_in>detect_speech`, the uneven
  workers and the VAD lag, without a profiler.

## Phase 2: cheaper VAD maths (callbot)

`_preprocess` runs two `filtfilt`s a chunk; half of `filtfilt` is
`lfilter_zi`, which solves the same small linear system for the same
constant filters every time. Compute the initial conditions once per
filter at import and filter with them; check what else repeats per chunk
(`rfftfreq`, the band masks of spectral gating) and hoist it.

- **Gate:** every speech segment is identical (same samples, same
  boundaries) on all of `tests/call/audio` and the parity oracle passes;
  offline push cost measured before and after, alone and with a busy
  thread.

## Phase 3: release only what a packet holds (operonx)

`release_context(ctx)` drops a finished transient context's entries from
every non-shared cell. Compute at compile which cells a transient
context can hold (the producer's payload, its consumers' inputs, outputs
and metadata) and visit only those.

- **Gate:** the transient-release tests and memory tests pass (nothing
  left behind after a long stream); the per-packet loop cost is measured
  before and after; A/B matches main.

## Phase 4: spread calls evenly over the workers (operonx serve)

The workers of a pooled listener share an accept socket, and nothing
balances them. Each worker reports its open connections in a small
shared array (`multiprocessing` shared memory, created by the pooled
listener's parent); a worker holding more than the least-loaded worker
plus one stops reading its listening socket until that is no longer
true. Accept-time only: no connection is moved or refused.

- **Gate:** 20 and 40 calls over 4 and 8 workers land within ±1 of even
  (read from T2); a single-worker listener is unchanged; the A/B matches
  main.

## Phase 5 (only if the target is still missed): VAD in its own process

Decided after phases 2–4 are measured. The graph does not change:
`detect_speech` stays the same op with the same inputs and yields. Only
the `vad_pool` resource changes: `audio_worker: mode: thread | process`
(thread stays the default). In process mode each server worker starts
one child holding every call's `VadState` by `call_id`; the op awaits
`vad_pool.push(call_id, packet, ts)` instead of `run_in_executor`, and
gets the segments and `last_speech_ms` back. The call's end op frees its
state; a dead child is restarted and the error is traced. No "process
op" is added to operonx.

- **Gate:** at 5 calls the reply time is not worse (the packet crossing
  costs); one worker holds clearly more than today's 5–6 calls.

## Order and status

| # | Phase | Where | Status |
|---|---|---|---|
| 1 | T1–T4 traces | operonx + callbot | **done**: operonx 1.24.0 (#173), callbot f12c632, studio #93 |
| 2 | VAD maths | callbot | **done**: c54dfb6 |
| 3 | release_context | operonx | **done**: 1.24.1 (#175) |
| 4 | even spread | operonx serve | **done**: 1.25.0 (#176) |
| 5 | VAD process | callbot resource | **not needed**: the target is met by 1–4 and 8 workers |

Traces first: every fix after them is judged in ClickHouse and the
studio. Then the small, contained wins, measured one at a time; the
process split last, because it is the largest change and may not be
needed.

## Reproduce

Scratchpad tools from the 2026-10-14 session (move into the callbot's
`bench/` in phase 1): `ccu_real.py` (the caller), `ccu_run.sh CCU WORKERS
TAG [VAD_THREADS]` (one fresh local server on 19922/19932, never a 992x
port), `serve_instr.py` (the temporary timing that phase 1 replaces).

## Results (2026-10-14)

Real-audio calls (`ccu_real.py`, 20 ms packets, recorded speech, turn-taking),
read from ClickHouse, not from a profiler:

| Calls / workers | Spread | Reply after the caller stops, p50 / p95 | Worst VAD lag of any call |
|---|---|---|---|
| 20 / 4, before | 10/6/5/0 or 8/7/3/2 | 4.35 / 6.7 s | 8.3 s |
| 20 / 4, after | 5/5/5/5 | **0.88 / 1.09 s** | **0.30 s** |
| 40 / 4, after | 10 each | 4.8 / 6.3 s | 10.6 s (10 calls is past one worker) |
| 40 / 8, after | 5 each | **0.85 / 1.05 s** | **0.34 s** |

- **Per phase.** Phase 2 cut a VAD push from 0.9 to 0.64 ms (2.86 to 2.09 ms
  beside a busy loop), every segment bit-identical; one worker then held 6
  calls on time instead of about 5. Phase 3 cut the cells a packet's release
  visits from 472 to 18 (VAD lag at 8 calls on one worker: 1.24 to 0.93 s at
  the median second); coverage checked over 15,271 releases, none missed.
  Phase 4 made the spread exact; the kernel alone gave 9/2/1/0, 11/1/0/0.
- **What one worker holds:** about 6–7 calls on time. 40 calls need 8 workers
  (`WS_WORKERS=8`), well within this host's 24 cores.
- **Not changed:** greeting p95 ~340 ms and 5-call replies ~1 s, as before;
  the 8-op chain and stream A/B matched main at every phase.

# Part 2: latency at a glance, then 50–100 calls on the fewest workers

Started 2026-10-14. Part 1 left one worker holding about 6–7 calls, so
100 calls would take ~16 workers. Part 2 makes every latency problem
readable on a dashboard first, then lifts what one worker holds.

## What part 1 still does not show

Each call explains itself now (backlog, worker, loop lag, VAD lag), but:

1. **The latency a caller feels is not traced.** "Caller stops → bot's
   first audio" was measured by the test client, not by the server.
2. **Nothing is charted over time.** Monitor shows a call's length (how
   long people talked) and op time; VAD lag, loop lag and calls per worker
   were read with hand-written ClickHouse queries.
3. **A worker's CPU is not recorded**, so "out of CPU" and "slow for
   another reason" look the same.

## Target

Same latency as part 1, at 50, 75 and 100 real-audio calls:

- VAD lag p95 ≤ 0.6 s; reply after the caller stops p50 ≤ 1.2 s;
- no silence reprompt caused by lag; 5-call numbers not worse;
- **on as few workers as possible**: accepted at ≥ 12.5 calls a worker
  (100 on 8), aimed at 25 (100 on 4).

## Hard gates (every phase, as in part 1)

- No cost on the success path for an operonx change (pinned A/B).
- **The op structure stays**: the callbot graph keeps its ops, edges and
  names. A faster implementation goes *inside* an op or a resource, never
  around the graph. No "process op" or "native op" kind in operonx.
- Numbers come from the traces (ClickHouse, the Monitor page); a profiler
  is only used to find *where* CPU goes once the traces say a worker is
  out of it.

## Phase 6: the latency dashboard

- **O1 (operonx): `measure(name, ms)`.** Called from an op body; the run
  keeps the samples and writes `metadata["latency_ms"] = {name: {"n",
  "p50", "p95", "max"}}` when it ends. No tracing installed → a no-op. One
  list append per sample.
- **O2 (operonx): worker CPU.** The loop beacon (phase 1, T3) also reads
  the process's CPU time on each tick; a run writes `metadata["cpu_pct"] =
  {"avg", "max"}` (one core = 100; `max` over 1 s windows). One
  `time.process_time()` per 50 ms tick, nothing per run.
- **O3 (callbot): reply latency and VAD lag as measures.** `play`
  measures `reply` (the first packet of a reply minus the end of the
  caller's speech that triggered it); `detect_speech` measures `vad_lag`
  per utterance. The existing `lag_ms` fields stay.
- **O4 (studio): Monitor → "Latency".** From run summaries' metadata, no
  trace opened: every measure's p95 over time (tiles + chart), loop-lag
  p95 and worker CPU over time, the busiest worker's calls, and the
  slowed calls (the run page's "Slowed by" rule) linked to their run
  pages. A service without measures shows only loop lag / CPU / workers.
- **Gate:** the A/B matches main; a 20-call run, then an overloaded one,
  read on the Monitor page alone tell "fine" from "this worker is out of
  CPU, replies late" — screenshots desktop + phone.

## Phase 7: the per-packet budget

Before any fix: the load-test client must not be the limit, then one
worker is driven past saturation and its CPU is split by where it goes.

- `ccu_real.py` spreads calls over several client processes and reports
  its own send drift (a late client looks like a late server).
- One worker, 8 → 20 calls: the dashboard shows where it saturates; a
  profile of that worker splits a packet's CPU into: uvicorn/websocket
  receive, `receive_audio`, operonx dispatch + trace, `VadState.push`
  (and its GIL waits), `play`'s sends, heartbeat.
- **Output:** a table in this doc, µs per packet per part, which orders
  phases 8 and 9.

## Phase 8: VAD in Rust (callbot)

`VadState.push` is ~0.64 ms of numpy/scipy per packet alone and ~2.1 ms
beside a busy loop, all of it holding the GIL. A PyO3 extension
(`callbot_vad`, built with maturin, in the callbot repo) does the same
DSP — resample 8k→16k, bandpass, spectral gating, AGC, the segment state
machine — with the GIL released (`allow_threads`); Silero stays an ONNX
session (onnxruntime already runs without the GIL). The op, its inputs,
its yields and the `audio_worker:` resource stay; `VadState` keeps its
methods and gains a Rust core. A pure-Python fallback stays for a host
without the wheel.

- **Gate:** on all of `tests/call/audio` the same segments (same
  boundaries; samples within float tolerance; same transcripts from STT)
  and the parity oracle passes; push cost alone and under load measured;
  the reply time at 5 calls not worse.

## Phase 9: the loop's per-packet cost (operonx, ordered by phase 7)

Whatever phase 7 shows the loop spends per packet outside VAD (dispatch,
trace records of ~1800 `detect_speech` invocations a call, websocket
framing). Fixes land in operonx where they are generic, measured with the
pinned A/B and the stream bench.

## Phase 10: fewest workers

50, 75, 100 calls; the smallest `WS_WORKERS` that meets the target at
each, read on the Monitor page. The deployed default changes only if the
owner wants it.

## Part 2 order and status

| # | Phase | Where | Status |
|---|---|---|---|
| 6 | latency dashboard | operonx + callbot + studio | in progress |
| 7 | per-packet budget | callbot bench | |
| 8 | VAD in Rust | callbot | |
| 9 | loop per packet | operonx | decided by 7 |
| 10 | fewest workers | callbot config | |
