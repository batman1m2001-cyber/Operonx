"""Evals: a dataset of cases, the system under test, and evaluators.

An eval is a :class:`~operonx.app.jobs.Job` — no new runtime. Its source is
a dataset, its graph is the system under test (any graph a job can run,
doors or not), and after each case's run the evaluators judge what came
out. The item record carries the verdict, the runs carry ``origin=eval``
(filed under ``.operonx/runs/evals/``), and ``run.json`` carries the pass
rate. ``operonx run <eval>`` exits non-zero when a case fails — or, with a
``threshold``, when the pass rate is under it — so CI can gate on it::

    ev = Eval("replies", graph="bot:reply_flow", dataset="datasets/replies.jsonl",
              evaluators=[contains(), llm_judge("llm:judge", "Is the reply polite and correct?")],
              threshold=0.9)
    run = await ev.run()          # JobRun; run.meta["eval"] has the numbers

**A dataset** is a JSONL file, one case per line::

    {"id": "c1", "input": {...}, "expected": ..., "tags": ["refund"], "from": {"run": "…"}}

``input`` is the item the graph receives; ``expected`` is optional (an
evaluator that needs it says so). A line without ``input`` is itself the
input, so a job's data file is already a dataset of cases with no
expectations. ``"dataset:name"`` names ``<project>/datasets/name.jsonl``.

**An evaluator** is a function — plain, async, or an ``@op`` (called for
its body) — that takes any of ``input``, ``output``, ``expected``, ``row``
and ``outputs`` by name and returns a verdict: ``True``/``False``, a score
in [0, 1] (passes at 0.5), or ``{"passed", "score", "reason"}``. Helpers:
:func:`exact`, :func:`contains`, :func:`fuzzy`, :func:`json_match`,
:func:`llm_judge`. ``output`` is what the case produced: the one item the
graph sent (or its result, for a graph with no doors), a list when it sent
several, ``None`` when it sent nothing.
"""

from .dataset import CASE_KEYS, Dataset, case_id, dataset_path
from .evaluators import contains, exact, fuzzy, json_match, llm_judge, verdict_of
from .job import Eval

__all__ = [
    "Dataset",
    "Eval",
    "contains",
    "dataset_path",
    "exact",
    "fuzzy",
    "json_match",
    "llm_judge",
    "verdict_of",
]
