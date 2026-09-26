"""17 Jobs — one graph, run over a file instead of a socket.

The graph is the same shape every served graph has::

    ingress ─► score ─► egress

`[[serve]]` would mint one run per request from a listener. A ``Job``
mints one run per *item* from a source — here a JSONL file — and writes
what `egress` sends to a sink. The graph cannot tell the difference,
which is the point: nothing in ``score_call`` knows it is in a batch.

What a Job adds over a for-loop is the **record**. Every run leaves::

    /tmp/operonx_jobs/ex17/jobs/score_calls/<run_id>/
      run.json      status, counts, what ran
      items.jsonl   one line per call: key, status, error, trace_id, ms, sent

One of the three calls in ``data/calls.jsonl`` has an empty transcript
and fails. The first run says ``ok=2 failed=1`` and names it; fix the
line and ``--resume`` runs only that one. Run from this directory::

    uv sync
    uv run python main.py                          # ok=2 failed=1
    # …edit data/calls.jsonl, give call c3 a transcript…
    uv run operonx-run main:score_calls --resume   # ok=1 skipped=2
"""

from __future__ import annotations

import sys
from pathlib import Path

import operonx
from operonx.app import Application, Service, env, http
from operonx.app.jobs import Job, Runbook
from operonx.app.serve import egress, ingress
from operonx.core import END, START, graph, op

HERE = Path(__file__).resolve().parent
OUT = Path("/tmp/operonx_jobs/ex17")

# The `source:` / `sink:` blocks in resources.yaml, for the second job.
operonx.bootstrap(resources=HERE / "resources.yaml")


# ── the graph ────────────────────────────────────────────────────────────


@op(bound="sync")
def score(call: dict = None) -> dict:
    """A stand-in for the LLM: score a transcript by how much was said."""
    transcript = (call or {}).get("transcript", "")
    if not transcript.strip():
        raise ValueError("empty transcript")
    words = len(transcript.split())
    return {
        "result": {
            "call_id": call["call_id"],
            "words": words,
            "verdict": "engaged" if words >= 5 else "brief",
        }
    }


@graph
def score_call():
    src = ingress()
    scored = score(call=src["item"])
    out = egress(item=scored["result"])
    START >> src >> scored >> out >> END


# ── the jobs ─────────────────────────────────────────────────────────────

#: Paths straight on the Job: the quickest way to say where things are.
score_calls = Job(
    "score_calls",
    graph=score_call,
    source=HERE / "data" / "calls.jsonl",
    sink=OUT / "scores.jsonl",
    key="call_id",
    concurrency=2,
    on_error="skip",
    record_dir=OUT / "jobs",
    description="Score every call in data/calls.jsonl; one run per call.",
)

#: The same job through resources.yaml, which is where a deployment
#: names its inputs and outputs — the graph and the job stay literal.
score_from_resources = Job(
    "score_from_resources",
    graph=score_call,
    source="source:calls",
    sink="sink:scores",
    key="call_id",
    concurrency=2,
    on_error="retry:1",
    record_dir=OUT / "jobs",
    description="score_calls, with source and sink declared as resources.",
)


# ── after the scores: two more jobs, and a runbook that runs all three ─
# Hand-off is by naming the same file: `score_calls` writes scores.jsonl,
# the two below read it. Nothing is rewired at run time.


@graph
def passthrough():
    """A graph that only moves items: here, from JSONL to CSV."""
    src = ingress()
    out = egress(item=src["item"])
    START >> src >> out >> END


@op(bound="sync")
def bucket(row: dict = None) -> dict:
    return {"line": {"call_id": row["call_id"], "bucket": row["verdict"], "words": row["words"]}}


@graph
def summarise_flow():
    src = ingress()
    b = bucket(row=src["item"])
    out = egress(item=b["line"])
    START >> src >> b >> out >> END


export_csv = Job(
    "export_csv",
    graph=passthrough,
    source=OUT / "scores.jsonl",
    sink=OUT / "scores.csv",
    key="call_id",
    record_dir=OUT / "jobs",
    description="scores.jsonl → scores.csv, one run per row.",
)

summarise = Job(
    "summarise",
    graph=summarise_flow,
    source=OUT / "scores.jsonl",
    sink=OUT / "summary.jsonl",
    session="stream",
    record_dir=OUT / "jobs",
    description="All scores through one run.",
)

#: `>>` wires, a list is one wire per element: score first, then the two
#: readers side by side. A runbook is a record (jobs/nightly/<run>/run.json
#: holds the wires and each job's own run id), never a span.
nightly = Runbook(
    "nightly",
    score_calls >> [export_csv, summarise],
    # `score_calls` skips a bad call and reports the run as failed; the
    # readers should still run over what it did score. `"stop"` (the
    # default) would skip both readers — they are downstream of it.
    on_error="continue",
    record_dir=OUT / "jobs",
    description="Score every call, then export and summarise in parallel.",
)


# One run fed every call through `ingress`: the callbot's shape. One
# trace, shared state, and no per-item accounting — the record counts
# what was fed and what egress sent.
score_stream = Job(
    "score_stream",
    graph=score_call,
    source="data/calls.jsonl",
    sink="/tmp/operonx_jobs/ex17/scores_stream.jsonl",
    session="stream",
    record_dir="/tmp/operonx_jobs/ex17/jobs",
    description="All calls through one run.",
)

# The application: the same graph behind an HTTP route and under the
# jobs above. `operonx.toml` points here; `operonx-serve` and
# `operonx-run` read this object.
APP = Application(
    "ex17-jobs",
    services=[
        Service(
            "score",
            http("POST", "/score", port=env("HTTP_PORT", 8017)),
            graph=score_call,
            description="One call in, one score out.",
        ),
    ],
    jobs=[score_calls, score_from_resources, score_stream, nightly],
    description="One graph: served as HTTP, and run over a JSONL file by a Job with a record per run.",
)


if __name__ == "__main__":
    run = score_calls.run_sync(resume="--resume" in sys.argv)
    print(run.summary())
    print(f"  record: {run.path}")
    print(f"  scores: {OUT / 'scores.jsonl'}")
    for item in run.failed:
        print(f"  failed {item.key}: {item.error}")
    raise SystemExit(0 if run.status == "ok" else 1)
