# 06 — State, Side-Channels & Interrupts

Tier 1: pure compute, no API keys.

```bash
uv sync
uv run python main.py
```

Three mechanisms a long-lived workflow needs that a request/response
pipeline never does.

## SCRATCH — run-scoped mutable state

Ops write it imperatively; it lives outside the dataflow, so a reader needs
no edge from the writer:

```python
@op
def take_order(item: str, qty: int):
    SCRATCH["qty"] = qty
    return {"line": f"{qty} x {item}"}
```

Downstream, wire it as a normal input. At build time `SCRATCH["log"]`
resolves to a `ScratchRef`; at run time, to the live value:

```python
done = audit(log=SCRATCH["log"], total=priced["total"])
```

Seed it per run with `run(scratch={...})`. Writing `SCRATCH[...]` outside an
active run raises — it is a programming error, not a silent no-op.

## EmitOp — a side channel, not a graph output

Progress and telemetry go out on a named channel without becoming outputs,
so they never distort the topology. Fire-and-forget: with no subscriber the
payload is dropped.

```python
say = EmitOp(payload=a["note"], channel="progress")
a >> say >> END  # no outputs, so it must still reach an exit
```

Consume with `mode="custom"`, optionally filtered:

```python
async for evt in engine.stream({"n": 5}, mode="custom", channels=["progress"]):
    print(evt.payload)
```

That `>> END` matters: an `EmitOp` declares no outputs, so without it the
graph reports the node as unable to reach an exit.

## InterruptOp — suspension as a visible node

Human-in-the-loop approval is a node in the DAG, not a hidden callback, so
the suspension point is visible in the graph and its answer is a normal ref:

```python
approve = InterruptOp(payload=draft["plan"], timeout=5)
final = settle(plan=draft["plan"], response=approve["response"])
```

The caller answers from outside via the run handle's state:

```python
handle = engine.start(inputs={"amount": 12.5})
handle.state.resume_interrupt(interrupt_id, True)
```

`timeout` is wall-clock seconds; falsey means wait forever. The op also
reports `timed_out` and `interrupt_id` alongside `response`.
