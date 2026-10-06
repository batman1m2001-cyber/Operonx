"""17 Jobs — one graph, run over a file instead of a socket.

The graph is the same shape every served graph has::

    ingress ─► score ─► egress

A ``Service`` mints one run per request. A ``Job`` mints one run per
*item* — here each line of ``data/calls.jsonl`` — and keeps what `egress`
sends as that item's result. The graph cannot tell the difference, which
is the point: nothing in ``score_call`` knows it is in a batch.

What a Job adds over a for-loop is the **record**. Every run leaves::

    /tmp/operonx_jobs/ex17/jobs/score_calls/<run_id>/
      run.json       status, counts, what ran
      items.jsonl    one line per call: key, status, error, trace_id, ms
      results.jsonl  one line per call that finished: key, result

One of the three calls has an empty transcript and fails. The first run
says ``ok=2 failed=1`` and names it; fix the line and ``--resume`` runs
only that one. Run from this directory::

    uv sync
    uv run python main.py                          # ok=2 failed=1
    # …edit data/calls.jsonl, give call c3 a transcript…
    uv run operonx run score_calls --resume        # ok=1 skipped=2
    uv run operonx run nightly                     # score, then the report
"""

from __future__ import annotations

import sys
from pathlib import Path

from operonx.app import Application, Service, env, http
from operonx.app.jobs import Job
from operonx.app.serve import egress, ingress
from operonx.core import END, START, graph, op

HERE = Path(__file__).resolve().parent
OUT = Path("/tmp/operonx_jobs/ex17")


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


# ── once over every result ───────────────────────────────────────────────


@op(bound="sync")
def tally(results: list = None) -> dict:
    verdicts = [r["verdict"] for r in results or []]
    return {"report": {"calls": len(verdicts), "engaged": verdicts.count("engaged")}}


@graph
def report(results):
    t = tally(results=results)
    START >> t >> END


# ── the jobs ─────────────────────────────────────────────────────────────

#: One run per line of the file; every result kept in the record, also
#: exported to scores.jsonl as it finishes, and reduced to one report.
score_calls = Job(
    "score_calls",
    graph=score_call,
    items=HERE / "data" / "calls.jsonl",
    key="call_id",
    output=OUT / "scores.jsonl",
    reduce=report,
    concurrency=2,
    record_dir=OUT / "jobs",
    description="Score every call in data/calls.jsonl; one run per call.",
)


def recent_calls():
    """A custom loader: any function that yields items. Called on every run."""
    yield {"call_id": "r1", "transcript": "vâng em nghe máy rồi ạ, chị cứ nói"}
    yield {"call_id": "r2", "transcript": "để sau nhé"}


score_recent = Job(
    "score_recent",
    graph=score_call,
    items=recent_calls,
    key="call_id",
    record_dir=OUT / "jobs",
    description="The same graph over a loader function.",
)

#: Jobs in order, as one command. The first step that is not ok stops the
#: rest; each step keeps its own record.
nightly = Job(
    "nightly",
    steps=[score_recent, score_calls],
    record_dir=OUT / "jobs",
    description="Score the recent calls, then the file.",
)

# The application: the same graph behind an HTTP route and under the
# jobs above. `operonx.toml` points here; `operonx serve` and
# `operonx run` read this object.
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
    jobs=[score_calls, score_recent, nightly],
    # Every run — a request the service answered, an item a job scored —
    # is recorded here unless it names its own consumers: one directory
    # per run under .operonx/runs, filed by origin. The studio's Runs
    # screen reads it.
    trace=["trace_local:default"],
    description="One graph: served as HTTP, and run over a JSONL file by a Job with a record per run.",
)


if __name__ == "__main__":
    run = score_calls.run_sync(resume="--resume" in sys.argv)
    print(run.summary())
    print(f"  record: {run.path}")
    print(f"  report: {run.reduced}")
    for item in run.failed:
        print(f"  failed {item.key}: {item.error}")
    raise SystemExit(0 if run.status == "ok" else 1)
