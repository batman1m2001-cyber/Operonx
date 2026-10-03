"""What an eval adds over a plain job running the same graph.

The system under test is one cheap sync op, so the graph run is as small
as it gets and the eval's own bookkeeping (dataset read, fingerprint,
capture sink, evaluators, verdict, summary) is as visible as it gets.
Each round runs the same cases three ways, interleaved so drift hits all
of them alike:

* ``job``: a plain :class:`~operonx.app.jobs.Job` over the cases;
* ``eval``: an :class:`~operonx.app.evals.Eval` with one ``exact`` check
  and no judge (``repeats=1``, the default);
* ``eval+gate``: the same eval with a ``Gate`` (statistics, must-pass,
  error budget) but no baseline, when this operonx has one;
* ``eval+trace``: the eval plus a check that reads the case's
  ``TraceView`` (its path), when this operonx has one — what asking for
  the trace costs; ``eval`` shows what not asking costs.

An eval's cost is a fixed part per run (reading the dataset, the
fingerprint, the summary) and a part per case. Running two dataset sizes
separates them: per case = (T_large − T_small) / (large − small), fixed =
T_small − small · per case. Times are this process's CPU time
(``time.process_time``): on a shared machine wall time moves with other
people's load, CPU time much less. Medians over rounds. No trace
consumer, no LLM, no network::

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

SIZES, ROUNDS, CONCURRENCY = (300, 1200), 7, 4


@op(bound="sync")
def classify(text: str = "") -> dict:
    return {"label": "refund" if "money back" in text else "other"}


@graph
def flow(text: str = ""):
    c = classify(text=text)
    START >> c >> END


def _rows(n):
    for i in range(n):
        text = f"case {i}: " + ("I want my money back" if i % 3 else "hello")
        want = "refund" if i % 3 else "other"
        yield {"id": f"c{i}", "input": text, "expected": {"label": want}}


class _CaseInput(Job):
    def item_of(self, raw):  # the job runs the case's input, as the eval does
        return raw["input"]


async def _time(make, n) -> float:
    job = make()
    t0 = time.process_time()
    run = await job.run()
    took = time.process_time() - t0
    assert run.counts["ok"] == n, run.summary()
    return took * 1e3  # CPU ms per run


async def main() -> None:
    try:
        from operonx.app.evals import Gate
    except ImportError:
        Gate = None  # an operonx from before the gate
    try:
        from operonx.app.evals import TraceView
    except ImportError:
        TraceView = None  # an operonx from before evaluators could read the trace

    def one_step(trace=None):
        return trace.path() == ["c"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        common = dict(graph=flow, item_input="text", concurrency=CONCURRENCY, trace=[])

        def ways(data):
            out = {
                "job": lambda: _CaseInput(
                    "bench_job", source=str(data), key="id", record_dir=root / "jobs", **common
                ),
                "eval": lambda: Eval(
                    "bench_eval",
                    dataset=data,
                    evaluators=[exact("label")],
                    record_dir=root / "evals",
                    **common,
                ),
            }
            if Gate is not None:
                out["eval+gate"] = lambda: Eval(
                    "bench_gate",
                    dataset=data,
                    evaluators=[exact("label")],
                    record_dir=root / "evals",
                    gate=Gate(must_pass_tag="critical"),
                    **common,
                )
            if TraceView is not None:
                out["eval+trace"] = lambda: Eval(
                    "bench_trace",
                    dataset=data,
                    evaluators=[exact("label"), one_step],
                    record_dir=root / "evals",
                    **common,
                )
            return out

        got = {}
        for n in SIZES:
            data = root / f"cases-{n}.jsonl"
            data.write_text("".join(json.dumps(r) + "\n" for r in _rows(n)), encoding="utf-8")
            for make in ways(data).values():  # warm imports and the engine
                await _time(make, n)
            for _ in range(ROUNDS):
                for name, make in ways(data).items():
                    got.setdefault((name, n), []).append(await _time(make, n))

    small, large = SIZES
    med = {k: statistics.median(v) for k, v in got.items()}
    print(f"sizes {SIZES}, {ROUNDS} rounds, concurrency {CONCURRENCY} (CPU time, medians)")
    names = sorted({name for name, _ in med}, key=["job", "eval", "eval+gate", "eval+trace"].index)
    per_case = {m: (med[m, large] - med[m, small]) / (large - small) * 1e3 for m in names}
    fixed = {m: med[m, small] - small * per_case[m] / 1e3 for m in names}
    for m in names:
        extra = ""
        if m != "job":
            extra = (
                f"   over job: {per_case[m] - per_case['job']:+.1f} µs/case,"
                f" {fixed[m] - fixed['job']:+.1f} ms/run"
            )
        print(
            f"  {m:10s} {per_case[m]:7.1f} µs/case  {fixed[m]:6.1f} ms/run"
            f"  ({med[m, small]:.0f} ms @{small}, {med[m, large]:.0f} ms @{large}){extra}"
        )


if __name__ == "__main__":
    asyncio.run(main())
