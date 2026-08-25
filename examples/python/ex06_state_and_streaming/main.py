"""06 State, Side-Channels & Interrupts — SCRATCH, EmitOp, InterruptOp.

The three mechanisms a long-lived workflow needs and a pure request/response
pipeline never does:

* **SCRATCH** — per-run mutable state, outside the dataflow. Ops write it
  imperatively; downstream ops read it by wiring ``SCRATCH["key"]`` as an
  input at construction time. Seeded per run via ``run(scratch={...})``.
* **EmitOp** — a side channel. Progress, telemetry, partial UI updates go
  out on a named channel without becoming graph outputs, so they never
  distort the topology. Fire-and-forget: no subscriber, no cost.
* **InterruptOp** — a visible node that suspends until the caller answers.
  Human-in-the-loop approval is a node in the DAG, not a hidden callback.

Tier 1 — pure compute, no API keys. Run from this directory:

    uv sync
    uv run python main.py
"""

from __future__ import annotations

import asyncio

from operonx.core import END, PARENT, SCRATCH, START, EmitOp, InterruptOp, Operon, graph, op

# ── SCRATCH: state that outlives a single op ────────────────────────────


@op
def take_order(item: str, qty: int):
    """Write run-scoped state, then hand the dataflow onward.

    SCRATCH writes are imperative and invisible to the DAG — which is the
    point: `audit` below does not need an edge from here to see them.
    """
    SCRATCH["item"] = item
    SCRATCH["qty"] = qty
    SCRATCH["log"] = [*(SCRATCH["log"] or []), f"took {qty}x{item}"]
    return {"line": f"{qty} x {item}"}


@op
def price_it(line: str, unit_price: float):
    total = round((SCRATCH["qty"] or 0) * unit_price, 2)
    SCRATCH["log"] = [*(SCRATCH["log"] or []), f"priced at {total}"]
    return {"total": total}


@op
def audit(log: list, total: float):
    """Read SCRATCH through a wired input, not a global read.

    `log=SCRATCH["log"]` in the graph body resolves to a ScratchRef at
    build time and to the live value at run time.
    """
    return {"receipt": f"{total} | " + " -> ".join(log or [])}


@graph
def scratch_flow(item, qty, unit_price):
    order = take_order(item=item, qty=qty)
    priced = price_it(line=order["line"], unit_price=unit_price)
    done = audit(log=SCRATCH["log"], total=priced["total"])
    done["receipt"] >> PARENT["receipt"]
    START >> order >> priced >> done >> END


# ── EmitOp: a side channel that is not a graph output ───────────────────


@op
def step_one(n: int):
    return {"value": n * 2, "note": f"doubled {n}"}


@op
def step_two(value: int):
    return {"value": value + 1, "note": f"incremented to {value + 1}"}


@graph
def progress_flow(n):
    a = step_one(n=n)
    say_a = EmitOp(payload=a["note"], channel="progress")
    b = step_two(value=a["value"])
    say_b = EmitOp(payload=b["note"], channel="progress")

    b["value"] >> PARENT["value"]
    START >> a >> b >> END
    # An EmitOp has no outputs, so it must still reach an exit or the
    # graph reports it as unreachable.
    a >> say_a >> END
    b >> say_b >> END


# ── InterruptOp: suspend inside the DAG until a human answers ───────────


@op
def draft_refund(amount: float):
    return {"plan": f"refund {amount:.2f}"}


@op
def settle(plan: str, response: bool):
    return {"outcome": f"{'EXECUTED' if response else 'REJECTED'}: {plan}"}


@graph
def approval_flow(amount):
    draft = draft_refund(amount=amount)
    approve = InterruptOp(payload=draft["plan"], timeout=5)
    final = settle(plan=draft["plan"], response=approve["response"])
    final["outcome"] >> PARENT["outcome"]
    START >> draft >> approve >> final >> END


# ── Runners ─────────────────────────────────────────────────────────────


async def run_scratch() -> None:
    result = await Operon(scratch_flow(item=PARENT["item"], qty=PARENT["qty"],
                                       unit_price=PARENT["unit_price"])).run(
        inputs={"item": "widget", "qty": 3, "unit_price": 4.5},
        scratch={"log": ["order opened"]},        # seed run-scoped state
    )
    print(f"[scratch]  {result['receipt']}")


async def run_progress() -> None:
    """mode='custom' yields only what EmitOp sent, filtered by channel."""
    engine = Operon(progress_flow(n=PARENT["n"]))
    seen = []
    async for evt in engine.stream({"n": 5}, mode="custom", channels=["progress"]):
        seen.append(evt.payload)
    print(f"[progress] {len(seen)} event(s): {seen}")


async def run_approval() -> None:
    """Answer the interrupt from outside the graph, then let it finish."""
    engine = Operon(approval_flow(amount=PARENT["amount"]))
    handle = engine.start(inputs={"amount": 12.5})

    async def approve_when_asked():
        state = handle.state
        for _ in range(200):                       # the node registers, then waits
            if state._interrupt_responses:
                break
            await asyncio.sleep(0.01)
        for iid in list(state._interrupt_responses):
            state.resume_interrupt(iid, True)

    approver = asyncio.create_task(approve_when_asked())
    frames = [data async for _op, _ctx, data in handle]
    await approver
    outcome = next((f["outcome"] for f in frames if "outcome" in f), None)
    print(f"[approval] {outcome}")


async def main() -> None:
    await run_scratch()
    await run_progress()
    await run_approval()


if __name__ == "__main__":
    asyncio.run(main())
