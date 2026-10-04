"""The K2 gate (PLAN §4, §6): dense vs lexical vs hybrid vs hybrid+rerank on the eval sets.

    uv run python scripts/bench_k2.py WORK [--pg-dsn DSN] [--answers N --llm-resources
        ../Operon/resources.yaml --llm gpt-4o-mini --env ../Operon/.env]

Builds the three sets (``scripts/prepare_eval.py``) under ``WORK/sets``, ingests each
corpus into its own collection with a real embedder (``intfloat/multilingual-e5-small``
through operonx's HFEmbedding, E5's ``query:``/``passage:`` templates) and a SQLite
FTS5 lexical index, then:

1. the analyzer study: on the Vietnamese sets, lexical retrieval with each analyzer
   (simple, simple + folding, vi bigrams, vi + folding), on the questions as written
   and with their diacritics stripped (how people often type);
2. the gate: each retrieval mode on each set, evaluated with operonx ``Eval`` and the
   KB's evaluators (Recall@5/10/20, MRR, nDCG@10 with 95% intervals, item latency),
   plus hybrid − dense Recall@10 with operonx's paired test (McNemar / Newcombe for
   single-label sets);
3. with ``--pg-dsn``, lexical retrieval on Postgres FTS (ts_rank_cd) beside SQLite FTS5
   (bm25) on the same analyzer;
4. with ``--answers N``, a small live answer check: N cases of each set answered by
   the configured model, citation precision and the faithfulness proxy, and the
   answers written to ``WORK/answers.jsonl`` for a human to check.

Results go to ``WORK/k2.json``; ``docs/bench/k2.md`` is written from them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_eval  # noqa: E402

EMBEDDER = "intfloat/multilingual-e5-small"
RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
SETS = ["xquad_vi", "xquad_en", "corpus_vi"]
VI_SETS = ["xquad_vi", "corpus_vi"]
ANALYZERS = {
    "simple": {"kind": "simple", "fold_diacritics": False},
    "simple+fold": {"kind": "simple", "fold_diacritics": True},
    "vi": {"kind": "vi", "fold_diacritics": False},
    "vi+fold": {"kind": "vi", "fold_diacritics": True},
}


def log(msg: str) -> None:
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


def resources(work: Path, pg_dsn: str | None, llm_block: str) -> Path:
    lines = [
        f"kb_catalog:main:\n  path: {work}/kb/catalog.db",
        f"kb_blob:main:\n  root: {work}/kb/blobs",
        f"kb_lexical:main:\n  path: {work}/kb/lexical.db",
        f"embedding:e5:\n  api_type: hf\n  model: {EMBEDDER}",
        f"reranking:ce:\n  api_type: hf\n  model: {RERANKER}",
    ]
    lines += [f"vector_store:{s}:\n  api_type: faiss\n  metric: cosine\n  dim: 384" for s in SETS]
    if pg_dsn:
        lines.append(f"kb_lexical:pg:\n  api_type: postgres\n  dsn: {pg_dsn}\n  db_schema: kbbench")
    path = work / "resources.yaml"
    path.write_text("\n".join(lines) + "\n" + llm_block, encoding="utf-8")
    return path


def unaccented(cases: Path, out: Path) -> Path:
    """The same cases with their questions' diacritics stripped."""
    from operonx_kb.text.analyze import fold_diacritics

    rows = [json.loads(line) for line in cases.read_text("utf-8").splitlines()]
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            row["input"]["query"] = fold_diacritics(row["input"]["query"])
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


async def ingest(kb, name: str, corpus: Path, lexical) -> Dict[str, Any]:
    from operonx_kb import ChunkerSpec, CollectionSpec, DenseIndexSpec

    kb.create_collection(
        name,
        CollectionSpec(
            chunker=ChunkerSpec(),
            dense=DenseIndexSpec(embedder="e5", store=f"vector_store:{name}", batch_size=32,
                                 passage_template="passage: {text}", query_template="query: {text}"),
            lexical=lexical,
            language="vi" if name in VI_SETS else "en",
        ),
    )  # fmt: skip
    started = time.perf_counter()
    files = sorted(p for p in corpus.iterdir() if p.is_file())
    for i, path in enumerate(files):
        await kb.add(name, str(path), key=path.name)
        if (i + 1) % 25 == 0:
            log(f"  {name}: {i + 1}/{len(files)} documents")
    report = kb.verify(name)
    assert report.ok, report.problems[:3]
    return {"documents": len(files), "chunks": report.chunks,
            "seconds": round(time.perf_counter() - started, 1)}  # fmt: skip


def slim(report: Dict[str, Any]) -> Dict[str, Any]:
    """A report as the results file keeps it: means for tables, estimates for intervals."""
    return {
        "cases": report["cases"],
        "metrics": report["means"],
        "ci": report["metrics"],
        "scored": {k: v["n"] for k, v in report["metrics"].items()},
        "p50_ms": report["p50_ms"],
        "p95_ms": report["p95_ms"],
        "errors": len(report.get("errors") or []),
        "error_samples": (report.get("errors") or [])[:2],
        "gate": (report.get("summary") or {}).get("gate", {}).get("verdict"),
        "fingerprint": (report.get("summary") or {}).get("fingerprint"),
    }


async def main(args) -> None:
    import operonx
    from operonx.core.registry import ResourceHub

    import operonx_kb  # noqa: F401 — registers the kb_* categories
    from operonx_kb import KnowledgeBase, QueryError
    from operonx_kb.eval import compare_metric, evaluate_answers, evaluate_search
    from operonx_kb.model.collection import AnalyzerSpec, LexicalIndexSpec

    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    sets_dir = work / "sets"
    for lang in ("vi", "en"):
        if not (sets_dir / f"xquad_{lang}" / "cases.jsonl").exists():
            prepare_eval.xquad_set(lang, sets_dir / f"xquad_{lang}", work / "cache")
    if not (sets_dir / "corpus_vi" / "cases.jsonl").exists():
        prepare_eval.corpus_vi_set(sets_dir / "corpus_vi")
    if args.limit:  # a smoke run: the first N cases of each set
        for name in SETS:
            path = sets_dir / name / "cases.jsonl"
            lines = path.read_text("utf-8").splitlines()[: args.limit]
            path.write_text("\n".join(lines) + "\n", "utf-8")
    llm_block = ""
    if args.answers:
        from dotenv import load_dotenv

        load_dotenv(args.env, override=False)  # the model's key; never printed
        text = args.llm_resources.read_text("utf-8")
        block = re.search(rf"^llm:{re.escape(args.llm)}:\n(?:[ \t]+.*\n?)+", text, re.M)
        if block is None:
            raise SystemExit(f"no llm:{args.llm} in {args.llm_resources}")
        llm_block = block.group(0)
    ResourceHub.reset_instance()
    operonx.bootstrap(resources=resources(work, args.pg_dsn, llm_block), env=False)
    kb = KnowledgeBase()
    results: Dict[str, Any] = {
        "embedder": EMBEDDER, "reranker": RERANKER, "sets": {}, "analyzers": {}, "gate": {},
        "pg_fts": {}, "answers": {},
    }  # fmt: skip

    for name in SETS:
        cases = sets_dir / name / "cases.jsonl"
        first = LexicalIndexSpec(collection=f"{name}_simple", analyzer=AnalyzerSpec())
        log(f"ingest {name}")
        info = await ingest(kb, name, sets_dir / name / "corpus", first)
        info["cases"] = sum(1 for _ in cases.open(encoding="utf-8"))
        results["sets"][name] = info
        log(f"  {info}")

    # 1. analyzers, on the Vietnamese sets
    best: Dict[str, str] = {name: "simple" for name in SETS}
    for name in VI_SETS:
        cases = sets_dir / name / "cases.jsonl"
        plain = unaccented(cases, sets_dir / name / "cases_unaccented.jsonl")
        for label, cfg in ANALYZERS.items():
            spec = LexicalIndexSpec(collection=f"{name}_{label.replace('+', '_')}",
                                    analyzer=AnalyzerSpec(**cfg))  # fmt: skip
            await kb.rebuild_lexical(name, spec, switch=True)
            row = {}
            for variant, path in (("accented", cases), ("unaccented", plain)):
                report = await evaluate_search(kb, name, path, mode="lexical")
                row[variant] = slim(report)
            results["analyzers"].setdefault(name, {})[label] = row
            log(f"  analyzer {name} {label}: "
                f"R@10 {row['accented']['metrics'].get('recall@10')} / {row['unaccented']['metrics'].get('recall@10')}")  # fmt: skip

    def r10(label: str, variant: str = "accented") -> float:
        rows = [results["analyzers"][n][label][variant]["metrics"]["recall@10"] for n in VI_SETS]
        return sum(rows) / len(rows)

    chosen = max(ANALYZERS, key=lambda lab: (r10(lab), r10(lab, "unaccented")))
    results["analyzer_chosen"] = chosen
    for name in VI_SETS:
        best[name] = chosen
        spec = LexicalIndexSpec(collection=f"{name}_{chosen.replace('+', '_')}",
                                analyzer=AnalyzerSpec(**ANALYZERS[chosen]))  # fmt: skip
        await kb.rebuild_lexical(name, spec, switch=True)
    log(f"analyzer chosen for Vietnamese: {chosen}")

    # 2. the gate
    modes = [("dense", None), ("lexical", None), ("hybrid", None), ("hybrid+rerank", "ce")]
    per_case: Dict[str, Dict[str, Any]] = {}
    for name in SETS:
        cases = sets_dir / name / "cases.jsonl"
        for label, reranker in modes:
            mode = label.split("+")[0]
            log(f"gate {name} {label}")
            report = await evaluate_search(kb, name, cases, mode=mode, reranker=reranker,
                                           rerank_depth=30)  # fmt: skip
            results["gate"].setdefault(name, {})[label] = slim(report)
            per_case.setdefault(name, {})[label] = report["per_case"]
            log(f"  {slim(report)['metrics']}")
        results["gate"][name]["hybrid_minus_dense_r10"] = compare_metric(
            {"per_case": per_case[name]["dense"]},
            {"per_case": per_case[name]["hybrid"]},
            "recall@10",
        )
    wins = [n for n in SETS if results["gate"][n]["hybrid"]["metrics"]["recall@10"]
            > results["gate"][n]["dense"]["metrics"]["recall@10"]]  # fmt: skip
    results["hybrid_wins_r10"] = wins
    results["default_mode"] = "hybrid" if len(wins) >= 2 else "dense"
    (work / "per_case.json").write_text(json.dumps(per_case), encoding="utf-8")

    # 3. Postgres FTS beside SQLite FTS5
    if args.pg_dsn:
        for name in SETS:
            analyzer = AnalyzerSpec(**ANALYZERS[best[name]])
            sqlite = LexicalIndexSpec(
                collection=f"{name}_{best[name].replace('+', '_')}", analyzer=analyzer
            )
            pg = LexicalIndexSpec(index="kb_lexical:pg", collection=f"{name}_pg", analyzer=analyzer)
            await kb.rebuild_lexical(name, pg, switch=True)
            report = await evaluate_search(
                kb, name, sets_dir / name / "cases.jsonl", mode="lexical"
            )
            results["pg_fts"][name] = slim(report)
            await kb.rebuild_lexical(name, sqlite, switch=True)
            log(f"  pg fts {name}: {slim(report)['metrics']}")

    # 4. a small live answer check
    if args.answers:
        rows_out = []
        for name in SETS:
            rows = [
                json.loads(line)
                for line in (sets_dir / name / "cases.jsonl").read_text("utf-8").splitlines()
            ]
            sample = random.Random(11).sample(rows, args.answers)
            subset = work / f"answers_{name}.jsonl"
            subset.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sample), "utf-8"
            )
            report = await evaluate_answers(
                kb, name, subset, args.llm, mode=results["default_mode"], k=6
            )
            results["answers"][name] = slim(report)
            log(f"  answers {name}: {slim(report)['metrics']}")
            for row in sample:
                try:
                    answer = await kb.ask(name, row["input"]["query"], args.llm, k=6,
                                          mode=results["default_mode"])  # fmt: skip
                except QueryError as exc:
                    rows_out.append({"set": name, "id": row["id"], "error": str(exc)})
                    continue
                rows_out.append({"set": name, "id": row["id"], "question": row["input"]["query"],
                                 "expected": row["expected"], "answer": answer["text"],
                                 "citations": [{k: c[k] for k in ("source", "key", "quote", "span", "pages")}
                                               for c in answer["citations"]],
                                 "dropped": answer["dropped"],
                                 "unsupported_sentences": answer["unsupported_sentences"]})  # fmt: skip
        (work / "answers.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows_out), "utf-8"
        )

    (work / "k2.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")
    log(f"done: {work / 'k2.json'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("work", type=Path)
    ap.add_argument("--pg-dsn")
    ap.add_argument(
        "--answers", type=int, default=0, help="cases per set for the live answer check"
    )
    ap.add_argument("--llm", default="gpt-4o-mini", help="llm resource name in --llm-resources")
    ap.add_argument("--llm-resources", type=Path, default=ROOT.parent / "Operon" / "resources.yaml")
    ap.add_argument("--env", type=Path, default=ROOT.parent / "Operon" / ".env")
    ap.add_argument("--limit", type=int, default=0, help="smoke run: first N cases of each set")
    ap.add_argument(
        "--threads", type=int, default=8, help="torch CPU threads (latency is measured)"
    )
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    parsed = ap.parse_args()
    import torch

    torch.set_num_threads(parsed.threads)
    asyncio.run(main(parsed))
