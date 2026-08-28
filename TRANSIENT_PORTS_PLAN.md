# Transient ports — streaming runs must stop retaining every item

Status: **designed, measured, not built.** 2026-08-28.

## The finding

A run never frees per-item state. `MemoryState._cells[idx]` is a dict keyed
by context; every dispatched item mints a context and writes an entry, and
**nothing anywhere in the package removes one**. The scheduler pops
`tasks_by_ctx`, `ready`, `seq_origins`, `seq_queues` and `collect_bufs` —
all scheduling bookkeeping — and leaves the state cells alone.

Request-response graphs never notice: the run is short, then everything
drops. A long-lived streaming run is the opposite case, and it grows without
bound.

Measured — a generator yielding 32 KB numpy blobs into one downstream op,
one run, unthrottled:

```
items    RSS delta    per item
  100       3.2 MB      32 KB
  400       9.4 MB      24 KB
 1600      36.9 MB      23 KB
 6400     147.8 MB      23 KB
```

Dead linear. Every payload is held for the life of the run.

## Why it surfaced

The callbot asked whether its audio path could be plain edges instead of
`operonx.io.Channel`:

```python
recv = receive_audio(websocket=SCRATCH["websocket"])
vad  = vad_stream(audio=recv["audio"])
asr  = stt_flow(seg=vad["utterances"])
```

Every number previously quoted against that shape came from a benchmark
that ran a whole `ENGINE.start().collect()` per chunk — a graph run per
item, not a yield inside one run. The real primitive is far cheaper than
anyone thought:

```
                    loop lag max      per-item        RSS growth
 1 CCU   edge          2.35 ms       0.300 ms          +0.0 MB
         channel       1.77 ms       0.070 ms          +0.0 MB
 5 CCU   edge          2.87 ms       0.810 ms          +5.0 MB
         channel       2.13 ms       0.117 ms          +0.0 MB
10 CCU   edge          5.77 ms       1.027 ms          +6.2 MB
         channel       2.36 ms       0.151 ms          +0.0 MB
12 CCU   edge         11.43 ms       1.218 ms          +2.6 MB
         channel       2.27 ms       0.134 ms          +0.0 MB

BURST, producer unthrottled
  edge     20,000 items   2.97 s   +43.0 MB   delivered 20000
  channel  20,000 items   ——  QueueFull at its bound  ——
```

Latency was never the blocker. **Retention is.** And the burst row is the
whole argument in one line: the edge swallowed 20,000 items and 43 MB
silently, while the channel hit its bound and said so. operonx guards
*concurrency* (`_sem`, and sequential-by-default dispatch); it does not
guard *volume*.

## The design

`@op(transient=True)` — the op's per-item output vars are stored, delivered,
then evicted when the consuming context finishes.

```python
@op(bound="io", transient=True)
async def receive_audio(websocket=None, call_id=""):
    async for msg in websocket.iter_text():
        yield {"audio": (chunk, cmc_time)}
```

Not literally "never stored": a consumer reads its input by pulling from the
source cell, so the value must exist at dispatch. The semantics are **store,
deliver, evict** — and the scheduler already knows the instant, because it is
where `tasks_by_ctx[ctx]` is popped.

### Why opt-in, and why on `@op`

A general context GC would have to *prove* nobody else will read a value —
`.collect()` buffers, `push_refs` into shared cells, interrupt replay. Each
is a way to be silently wrong. A per-port flag makes the declaration the
proof, and every existing graph is bit-for-bit unchanged.

On the decorator rather than at the wiring site because transience is
intrinsic to what the op does, not to where it is deployed. `receive_audio`
streams telco packets on every deployment, forever. Contrast a queue *bound*
(4000, 64), which is a per-environment tuning number and belongs in
`resources.yaml` — that distinction is why `@op(outputs={"audio": Queue(64)})`
was rejected and `@op(transient=True)` was not.

### Transience propagates along the pull

Each payload is held **twice per hop**, because reading an input caches it in
the reader's own cell (`state.py:478`, `cell[ctx_key] = result  # Cache`):

```
(recv, "audio", ctx)   store_result — the producer's output
(vad,  "audio", ctx)   cached pull  — the consumer's input
```

Marking only the producer would evict one of two. So: **if a cell's
`pull_ref` points at a transient source, that cell's cache is transient
too.** Derivable at compile time from `schema._pull_refs`, so one flag on the
producer stops the whole chain from retaining, and nothing appears at the
wiring site.

## Open questions — settle before coding

1. **Do the two cells alias one buffer?** 23–32 KB retained per 32 KB blob
   suggests both cells reference the same numpy array rather than copying,
   in which case freeing one frees nothing. This is inferred from the RSS
   slope, **not measured**. It does not change the design — propagation
   covers both cases — but it changes what a partial fix would be worth, so
   verify it first with a refcount probe.
2. **Tracing must not re-retain.** If the trace serializes op outputs, a
   transient port's payload gets copied into the trace and the eviction buys
   nothing. Store a summary — type, size, count — never the value.
3. **`.collect()` is incompatible** by definition: it buffers until EOF.
   Compile-time error naming the offending hop, not a runtime surprise.
4. **1-1 only in v1.** More than one consumer of a transient port needs a
   refcount before eviction. Do not build that yet; make a second binding a
   compile error.
5. **Interrupt.** A swept context's transient values are gone. Confirm no
   path in `_sweep_ctx` re-reads an op's input after cancellation.

## Sequence

1. **Probe the aliasing question** (open question 1). A refcount check on the
   two cells; decides nothing structural but prices the change honestly.
2. **`transient: bool = False` on the `@op` decorator**, threaded into the
   op's schema entry. No behaviour yet — just carried.
3. **Propagation pass at compile time**: walk `schema._pull_refs`; any cell
   pulling from a transient port is itself transient. Add the `.collect()`
   and multi-consumer compile errors here.
4. **Eviction at the existing hook** — where `tasks_by_ctx[ctx]` is popped,
   drop transient cells for that ctx.
5. **Trace summary** for transient ports.
6. **Re-measure**: the probe should go flat instead of 23 KB/item; rerun the
   edge benchmark at 12 CCU to see whether the 11.43 ms lag and 1.218 ms
   dispatch come down too. Both are plausibly GC pressure from exactly this
   retention, but that is a hypothesis, not a finding.

## Gates

- The full existing suite. This touches the state layer; nothing may move.
- A new test: a long streaming run holds flat RSS across 10k items.
- A new test: `.collect()` on a transient chain fails at compile.
- Callbot `tests/test_vad_parity.py` identical, and both e2e calls passing,
  before any callbot change is considered.

## Outcome, 2026-08-28

Built, and the callbot's audio path is edges end to end as a result. Three
things this document had wrong or did not know:

**The callbot was never in the danger zone this plan claimed.** The
"450 MB at 5 CCU" that motivated it came from multiplying two invented
numbers — a ten-minute call, when the real p50 across 87 logged calls is
35 s, and 3 KB/item extrapolated from 32 KB blobs when a telco packet is
320 bytes. Real baseline is ~4.2 MB a call. The leak is real and worth
fixing on its own terms; it was not the blocker for the thing it was
justified by.

**Eviction did not fire for `bound="sync"` consumers.** The hook lived in
`_pump`, which inline ops never reach. Every measurement that showed the
feature working used an `async def` consumer. Found by writing the tests
this plan listed as gates and then skipped — 509 / 5009 / 50009 entries
for 50 / 500 / 5000 items.

**The intermittent ~50 ms loop spike is GC.** Measured at 5 CCU, four runs
each: with GC on, maxima of 3.04 / 46.66 / 4.10 / 10.80 ms; with
`gc.freeze()` + `gc.disable()`, 3.92 / 5.58 / 3.62 / 4.31 ms — the spike
disappears entirely. It is per-item context churn, not anything inherent
to edge dispatch, and it is mitigable at the application level.

## What it unlocks

If this lands, high-rate edges become viable and the callbot's two Channels
can become ordinary graph edges — `recv["audio"] -> vad -> asr` — with real
edges, per-item spans and sweepable contexts, instead of an async seam the
graph cannot see. That is the actual goal; transient ports are the
prerequisite.

Not a certainty: the burst case shows a channel gives **backpressure** as
well as bounded memory (`QueueFull` is a signal; an unbounded `seq_queues`
deque is not). Transient ports fix retention, not backpressure. Whether the
callbot still wants a bound on the audio path is a separate decision, to be
made after step 6.
