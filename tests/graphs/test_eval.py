"""The eval runner on operonx's Eval: retrieval metrics per mode, answer metrics,
and labels that fail loudly when they do not resolve (PLAN R8)."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from operonx_kb.eval import evaluate_answers, evaluate_search

GOLDEN = Path(__file__).parents[1] / "golden"
DATASETS = Path(__file__).parents[2] / "datasets"
DOCS = GOLDEN / "docs"


def run(coro):
    return asyncio.run(coro)


CASES = [
    ("leave", "How many days of annual leave do employees get?", "policy.md",
     "Every employee has twelve days of annual leave per calendar year"),
    ("taxi", "taxi refund per ride", "travel.md", "Taxi fares on business trips are refunded up to fifty euros per ride"),
    ("note", "when is a doctor's note needed", "policy.md", "Sick leave needs a doctor's note"),
]  # fmt: skip


@pytest.fixture
def loaded(kbx, tmp_path):
    from tests.graphs.test_retrieve import POLICY, TRAVEL

    for name, text in (("policy.md", POLICY), ("travel.md", TRAVEL)):
        (tmp_path / name).write_text(text, encoding="utf-8")
        run(kbx.add("docs", str(tmp_path / name), key=name))
    rows = [{"id": i, "input": {"query": q, "collection": "docs", "k": 20},
             "expected": {"relevant": [{"doc_key": key, "quote": quote}], "answer": quote}}
            for i, q, key, quote in CASES]  # fmt: skip
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    kbx.dataset = path
    return kbx


@pytest.mark.parametrize("mode", ["dense", "lexical", "hybrid"])
def test_search_metrics_per_mode(loaded, mode):
    report = run(evaluate_search(loaded, "docs", loaded.dataset, mode=mode))
    assert report["cases"] == 3 and report["errors"] == []
    assert set(report["metrics"]) == {"recall@5", "recall@10", "recall@20", "mrr", "ndcg@10"}
    mrr = report["metrics"]["mrr"]  # an estimate, never a bare number
    assert mrr["n"] == 3 and mrr["ci_lo"] <= mrr["mean"] <= mrr["ci_hi"]
    m = report["means"]
    assert (
        m["recall@20"] == 1.0
        and 0 < m["mrr"] <= 1
        and m["recall@5"] <= m["recall@10"] <= m["recall@20"]
    )
    assert report["p50_ms"] is not None


def test_repeats_gate_and_a_paired_comparison(loaded):
    from operonx.app.evals import Gate

    from operonx_kb.eval import compare_metric

    dense = run(evaluate_search(loaded, "docs", loaded.dataset, mode="dense", repeats=2,
                                gate=Gate(threshold=0.5)))  # fmt: skip
    summary = dense["summary"]
    assert summary["trials"] == 6 and summary["cases"] == 3
    assert summary["gate"]["verdict"] in ("pass", "failed") and "fingerprint" in summary
    assert set(dense["per_case"]["mrr"]) == {"leave", "taxi", "note"}  # repeats averaged
    hybrid = run(evaluate_search(loaded, "docs", loaded.dataset, mode="hybrid"))
    diff = compare_metric(dense, hybrid, "recall@20")
    assert diff["n"] == 3 and diff["method"] == "mcnemar" and diff["diff"] == 0


def test_reranked_search_is_evaluated_too(loaded, hub):
    hub.alias("reranking:overlap", "fake_reranking:overlap")
    report = run(evaluate_search(loaded, "docs", loaded.dataset, mode="hybrid", reranker="overlap"))
    assert report["errors"] == [] and report["reranker"] == "overlap"


def test_a_stale_label_is_an_error_not_a_zero(loaded, tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"id": "x", "input": {"query": "leave", "collection": "docs"},
                               "expected": {"relevant": [{"doc_key": "policy.md",
                                                          "quote": "thirty days of leave"}]}}) + "\n",
                   encoding="utf-8")  # fmt: skip
    report = run(evaluate_search(loaded, "docs", bad, mode="dense"))
    assert report["errors"] and all("quote not found" in e for e in report["errors"])
    assert report["metrics"] == {}


def test_answer_metrics_with_a_scripted_model(loaded, hub):
    from tests.graphs.test_answer import quoting

    hub.alias("llm:answerer", "fake_llm:scripted")
    hub.get("fake_llm:scripted").script = quoting()
    report = run(evaluate_answers(loaded, "docs", loaded.dataset, "answerer", mode="hybrid", k=4))
    assert report["errors"] == []
    m = report["means"]
    assert m["citation_precision"] == 1.0 and 0 < m["faithfulness"] <= 1
    assert 0 <= m["grounded_recall"] <= 1


def test_the_committed_vietnamese_set_is_what_the_generator_makes():
    spec = importlib.util.spec_from_file_location("make_corpus", GOLDEN / "make_corpus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    committed = [
        json.loads(line) for line in (DATASETS / "corpus_vi.jsonl").read_text("utf-8").splitlines()
    ]
    assert committed == mod.vi_cases()
    assert len(committed) >= 100 and len({c["input"]["query"] for c in committed}) == len(committed)
