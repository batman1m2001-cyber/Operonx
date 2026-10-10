"""Gate G1's latency check (docs/DOORLESS_SERVICES_PLAN.md).

The same trivial work served two ways: the door shape (``ingress`` → op →
``egress``) and the doorless shape (the op alone, its parameter filled
from the body). Each is called N times through Starlette's TestClient,
interleaved in rounds so drift hits both alike, and the p50/p95 of each
are printed. The doorless p50 must not be slower than the door p50.

    uv run python scripts/bench_doorless.py            # 1000 requests each
    uv run python scripts/bench_doorless.py --n 200
"""

from __future__ import annotations

import argparse
import statistics
import time

from starlette.testclient import TestClient

from operonx import END, START, graph, op
from operonx.app import Application, Service, http
from operonx.app.serve import egress, ingress


@op
def work(question: str = "") -> dict:
    return {"answer": question.upper()}


@op
def read(item: dict = None) -> dict:
    return {"question": (item or {}).get("question", "")}


@graph
def door_flow():
    src = ingress()
    r = read(item=src["item"])
    w = work(question=r["question"])
    out = egress(item=w["answer"])
    START >> src >> r >> w >> out >> END


@graph
def doorless_flow(question):
    w = work(question=question)
    START >> w >> END


def _timed(client: TestClient, path: str) -> float:
    t0 = time.perf_counter()
    reply = client.post(path, json={"question": "hi"})
    elapsed = time.perf_counter() - t0
    assert reply.status_code == 200, reply.text
    return elapsed * 1000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    app = Application(
        "bench",
        services=[
            Service("door", http("POST", "/door", port=8990), graph=door_flow, trace=[]),
            Service(
                "doorless", http("POST", "/doorless", port=8990), graph=doorless_flow, trace=[]
            ),
        ],
    ).asgi()
    times = {"door": [], "doorless": []}
    with TestClient(app) as client:
        for path in ("/door", "/doorless"):  # warm both
            for _ in range(20):
                _timed(client, path)
        per_round = max(args.n // args.rounds, 1)
        for _ in range(args.rounds):
            for name in ("door", "doorless"):
                times[name] += [_timed(client, f"/{name}") for _ in range(per_round)]
    for name, ms in times.items():
        ms.sort()
        p50 = statistics.median(ms)
        p95 = ms[int(len(ms) * 0.95) - 1]
        print(
            f"{name:9s} n={len(ms)}  p50={p50:.2f} ms  p95={p95:.2f} ms  mean={statistics.fmean(ms):.2f} ms"
        )


if __name__ == "__main__":
    main()
