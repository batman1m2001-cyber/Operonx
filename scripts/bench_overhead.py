"""A3 gate: per-turn framework overhead of ``Runner``, tracing on.

The A0 spike's ``bench_overhead`` (operonx-agents-spike) re-pointed at
``Runner``: the same shape — a scripted 0 ms model, ``--turns`` turns per
run, the last one answering and the others asking for ``calls`` readonly
tool calls — and the same method: 20 warm-up runs, then ``--runs`` runs with
``concurrency`` in flight at once, each timed from start to result, while a
probe task sleeping 1 ms in a loop records how late it wakes (the event-loop
lag a callbot's audio pump would see).

Variants:
  engine     control: an Operon run of a one-op graph (fixed per-run cost)
  direct     Runner.run called with no engine around it: no trace records
  runner     Runner.run inside an @op of an Operon run: every turn, model
             call and tool call a child execution in the trace (the gate)
  stream     the same through Runner.stream, every event drained: the model
             streams (one chunk per text and per tool call)

Every run is checked: its turn count, its tool calls and, for ``runner``, its
trace — 1 op record plus per turn a ``turn`` and a ``model`` record and one
record per tool call. A variant that did different work fails the benchmark.

    PYTHONPATH=<operonx main> uv run python scripts/bench_overhead.py --out results/overhead_a3.json
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operonx import END, START, Operon, graph, op  # noqa: E402
from operonx.core.registry.resource_hub import ResourceHub  # noqa: E402

from operonx_agents import Agent, Model, Runner, UsageLimits, tool  # noqa: E402
from tests.fakes import FakeHub, chunks_of, completion  # noqa: E402

USER = "look these up"


@tool(readonly=True)
async def lookup(a: int) -> dict:
    """Look up a number."""
    return {"a": a, "found": True}


class Scripted:
    """A 0 ms, stateless model: the turn is the number of assistant
    messages already sent, so one backend serves every concurrent run."""

    def __init__(self, turns: int, calls: int) -> None:
        self.turns, self.calls = turns, calls
        self.config = type("Config", (), {"structured_output": "prompted", "model": "bench"})()
        self.completions = [self._reply(t) for t in range(turns)]
        self.replies = [chunks_of(c, pieces=1) for c in self.completions]

    def _reply(self, turn: int):
        if turn >= self.turns - 1:
            return completion("final")
        calls = [{"id": f"c{turn}_{j}", "name": "lookup", "args": {"a": turn * 10 + j}}
                 for j in range(self.calls)]  # fmt: skip
        return completion(f"turn {turn}", tool_calls=calls, finish_reason="tool_calls")

    async def stream(self, messages, **params):
        turn = sum(1 for m in messages if m.get("role") == "assistant")
        for piece in self.replies[turn]:
            yield piece

    async def generate(self, messages, **params):
        return self.completions[sum(1 for m in messages if m.get("role") == "assistant")]


def make_agent(turns: int) -> Agent:
    return Agent(
        name="bench",
        model=Model("bench"),
        tools=[lookup],
        limits=UsageLimits(turns=turns + 1),
    )


@op
def noop(question: str) -> dict:
    return {"answer": question}


@graph
def engine_floor(question=None):
    n = noop(question=question)
    START >> n >> END


def runner(variant: str, agent: Agent) -> Callable[[], Any]:
    if variant == "engine":
        engine = Operon(engine_floor, params={"question": None})
        return lambda: engine.run({"question": USER})
    if variant == "direct":
        return lambda: Runner.run(agent, USER)
    if variant == "runner":

        @op
        async def support(question: str) -> dict:
            res = await Runner.run(agent, question)
            return {"turns": res.turns, "messages": res.messages}

        @graph
        def chat(question=None):
            s = support(question=question)
            START >> s >> END

        engine = Operon(chat, params={"question": None})
        return lambda: engine.run({"question": USER})
    if variant == "stream":

        @op
        async def support_streamed(question: str) -> dict:
            async for event in Runner.stream(agent, question):
                pass
            res = event.result
            return {"turns": res.turns, "messages": res.messages}

        @graph
        def chat_streamed(question=None):
            s = support_streamed(question=question)
            START >> s >> END

        engine = Operon(chat_streamed, params={"question": None})
        return lambda: engine.run({"question": USER})
    raise ValueError(f"unknown variant {variant!r}; one of engine, direct, runner, stream")


def checker(variant: str, turns: int, calls: int) -> Callable[[Any], None]:
    tools = (turns - 1) * calls

    def check(result: Any) -> None:
        if variant == "engine":
            return
        got_turns = result.turns if variant == "direct" else result["turns"]  # noqa
        messages = result.messages if variant == "direct" else result["messages"]
        n_tools = sum(1 for m in messages if m.get("role") == "tool")
        if got_turns != turns or n_tools != tools:
            raise AssertionError(f"{variant}: {got_turns} turns, {n_tools} tool messages")

    return check


async def trace_check(agent: Agent, turns: int, calls: int) -> int:
    @op
    async def support(question: str) -> dict:
        res = await Runner.run(agent, question)
        return {"turns": res.turns}

    @graph
    def chat(question=None):
        s = support(question=question)
        START >> s >> END

    handle = Operon(chat, params={"question": None}).start({"question": USER})
    await handle.result()
    names = [n.op_name for n in handle.trace.nodes]
    expected = 1 + 2 * turns + (turns - 1) * calls
    if len(names) != expected or names.count("turn") != turns:
        raise AssertionError(f"trace has {len(names)} records, expected {expected}: {names}")
    return len(names)


def pct(values: List[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


async def lag_probe(stop: asyncio.Event, out: List[float], interval: float = 0.001) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        t0 = loop.time()
        await asyncio.sleep(interval)
        out.append((loop.time() - t0 - interval) * 1000.0)


async def batch(one, runs: int, concurrency: int, check) -> Dict[str, Any]:
    latencies: List[float] = []
    lags: List[float] = []
    todo = iter(range(runs))

    async def worker() -> None:
        for _ in todo:
            t0 = time.perf_counter()
            result = await one()
            latencies.append((time.perf_counter() - t0) * 1000.0)
            check(result)

    gc.collect()
    stop = asyncio.Event()
    probe = asyncio.create_task(lag_probe(stop, lags))
    await asyncio.sleep(0.01)
    t0 = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    wall = (time.perf_counter() - t0) * 1000.0
    stop.set()
    await probe
    return {"latencies_ms": latencies, "wall_ms": wall, "lags_ms": lags}


async def idle_lag(seconds: float = 2.0) -> List[float]:
    lags: List[float] = []
    stop = asyncio.Event()
    probe = asyncio.create_task(lag_probe(stop, lags))
    await asyncio.sleep(seconds)
    stop.set()
    await probe
    return lags


async def main(args) -> None:
    from operonx.core.loggings import LOGGER

    LOGGER.setLevel(logging.ERROR)  # "Slow op" warnings would time the console, not the loop
    rows = []
    idle = await idle_lag()
    traces = {}
    for calls in args.calls:
        ResourceHub.set_instance(FakeHub(bench=Scripted(args.turns, calls)))
        agent = make_agent(args.turns)
        traces[calls] = await trace_check(agent, args.turns, calls)
        for variant in args.variants.split(","):
            one = runner(variant, agent)
            check = checker(variant, args.turns, calls)
            await batch(one, 20, 1, check)  # warm-up
            for conc in args.concurrency:
                res = await batch(one, args.runs, conc, check)
                lat = res["latencies_ms"]
                row = {
                    "variant": variant,
                    "calls": calls,
                    "concurrency": conc,
                    "turns": args.turns,
                    "runs": args.runs,
                    "turn_p50_ms": statistics.median(lat) / args.turns,
                    "turn_p95_ms": pct(lat, 0.95) / args.turns,
                    "cpu_per_turn_ms": res["wall_ms"] / (args.runs * args.turns),
                    "lag_p99_ms": pct(res["lags_ms"], 0.99) if res["lags_ms"] else 0.0,
                    "lag_max_ms": max(res["lags_ms"]) if res["lags_ms"] else 0.0,
                }
                rows.append(row)
                print(
                    f"{variant:7s} calls={calls} conc={conc:2d}  turn p50 {row['turn_p50_ms']:7.3f}"
                    f" ms  p95 {row['turn_p95_ms']:7.3f}  cpu/turn {row['cpu_per_turn_ms']:6.3f}"
                    f"  lag p99 {row['lag_p99_ms']:6.2f} max {row['lag_max_ms']:6.2f}",
                    flush=True,
                )
    import operonx

    out = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "operonx_file": operonx.__file__,
        "trace_records_per_run": traces,
        "idle_lag_ms": {"p50": statistics.median(idle), "p99": pct(idle, 0.99), "max": max(idle)},
        "rows": rows,
    }
    Path(args.out).write_text(json.dumps(out, indent=1))
    print("trace records per run:", traces)
    print("idle lag (control):", out["idle_lag_ms"])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--variants", default="engine,direct,runner,stream")
    p.add_argument("--calls", type=int, nargs="+", default=[1, 3])
    p.add_argument("--concurrency", type=int, nargs="+", default=[1, 5, 10, 20])
    p.add_argument("--turns", type=int, default=10)
    p.add_argument("--runs", type=int, default=200)
    p.add_argument("--out", required=True)
    asyncio.run(main(p.parse_args()))
