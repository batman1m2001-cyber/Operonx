"""What an eval adds per case over a plain job running the same graph.

The system under test is one cheap sync op, so the graph run is as small
as it gets and the eval's own bookkeeping (dataset read, capture sink,
evaluators, verdict, summary) is as visible as it gets. Each round runs
the same cases three ways, interleaved so drift hits all of them alike:

* ``job``: a plain :class:`~operonx.app.jobs.Job` over the cases;
* ``eval``: an :class:`~operonx.app.evals.Eval` with one ``exact`` check
  and no judge (``repeats=1``, the default);
* ``eval+gate``: the same eval with a ``Gate`` (statistics, must-pass,
  infra rate) but no baseline, when this operonx has one.

Per case = wall time / cases. Overhead = eval − job, median over rounds.
No trace consumer, no LLM, no network::

    uv run python scripts/bench_eval_overhead.py
"""

import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

from operonx.app.evals import Eval, exact
from operonx.app.jobs import Job
from operonx.core import END, START, graph, op

CASES, ROUNDS, CONCURRENCY = 300, 9, 4


@op(bound="sync")
def classify(text: str = "") -> dict:
    return {"label": "refund" if "money back" in text else "other"}


@graph
def flow(text: str = ""):
    c = classify(text=text)
    START >> c >> END


def _rows():
    for i in range(CASES):
        text = f"case {i}: " + ("I want my money back" if i % 3 else "hello")
        want = "refund" if i % 3 else "other"
        yield {"id": f"c{i}", "input": text, "expected": {"label": want}}


async def _time(make) -> float:
    job = make()
    t0 = time.perf_counter()
    run = await job.run()
    took = time.perf_counter() - t0
    assert run.counts["ok"] == CASES, run.summary()
    return took / CASES * 1e6  # µs per case


async def main() -> None:
    try:
        from operonx.app.evals import Gate
    except ImportError:
        Gate = None  # an operonx from before the gate
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / "cases.jsonl"
        data.write_text("".join(json.dumps(r) + "\n" for r in _rows()), encoding="utf-8")
        common = dict(graph=flow, item_input="text", concurrency=CONCURRENCY, trace=[])

        class _CaseInput(Job):
            def item_of(self, raw):  # the job runs the case's input, as the eval does
                return raw["input"]

        def plain():
            return _CaseInput(
                "bench_job", source=str(data), key="id", record_dir=root / "jobs", **common
            )

        def ev():
            return Eval(
                "bench_eval",
                dataset=data,
                evaluators=[exact("label")],
                record_dir=root / "evals",
                **common,
            )

        def ev_gate():
            return Eval(
                "bench_gate",
                dataset=data,
                evaluators=[exact("label")],
                record_dir=root / "evals",
                gate=Gate(must_pass_tag="critical"),
                **common,
            )

        ways = {"job": plain, "eval": ev}
        if Gate is not None:
            ways["eval+gate"] = ev_gate
        for make in ways.values():  # warm imports and the engine
            await _time(make)
        got = {k: [] for k in ways}
        for _ in range(ROUNDS):
            for name, make in ways.items():
                got[name].append(await _time(make))

    base = statistics.median(got["job"])
    print(f"{CASES} cases x {ROUNDS} rounds, concurrency {CONCURRENCY}, µs per case (median, IQR)")
    for name, xs in got.items():
        q = statistics.quantiles(xs, n=4)
        med = statistics.median(xs)
        extra = "" if name == "job" else f"   overhead {med - base:+.1f} µs/case"
        print(f"  {name:10s} {med:8.1f}   [{q[0]:.1f}, {q[2]:.1f}]{extra}")


if __name__ == "__main__":
    asyncio.run(main())
