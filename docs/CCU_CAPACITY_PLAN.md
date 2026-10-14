# CCU capacity: see the backlog, then lift the ceiling

Status: **done** 2026-10-14 (phases 1–4; phase 5 not needed). Results at the end. Driven by the callbot (educa-reminder-agent,
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
