"""The K6 gate (PLAN §10): graph search against hybrid.

    uv run python scripts/bench_k6.py WORK [--sets 2wiki musique xquad_en xquad_vi corpus_vi]

1. Multi-hop (``scripts/prepare_multihop.py``): MuSiQue and 2WikiMultihopQA, their
   paragraphs pooled per set. The graph's settings were chosen on the ``dev`` split
   (``docs/bench/k6.md``); the gate reads the ``test`` split only.
2. No harm (``scripts/prepare_eval.py``): the K2 single-hop sets, where nothing links
   one answer to another, so a graph can only move the right passage down.

Each set is ingested once into a collection with a dense index
(``multilingual-e5-small``, E5 templates), a lexical index (``simple``; ``vi``+fold
on Vietnamese, the K2 choice) and the concept graph, then evaluated with operonx
``Eval`` and the KB's evaluators in ``hybrid`` and ``graph`` mode, paired per case
(``stats.compare_paired``: a seeded bootstrap on Recall@k, which is a share on a
multi-hop case). Also recorded: ingest time with the concept step's share, the
graph's size, its cold build time, and query latency.

Results go to ``WORK/k6.json``; ``docs/bench/k6.md`` is written from them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_eval  # noqa: E402
import prepare_multihop  # noqa: E402

EMBEDDER = "intfloat/multilingual-e5-small"
LANGS = {"2wiki": "en", "musique": "en", "xquad_en": "en", "xquad_vi": "vi", "corpus_vi": "vi"}
MULTIHOP = ("2wiki", "musique")
METRICS = ("recall@2", "recall@5", "recall@10", "mrr")


def log(msg: str) -> None:
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


def resources(work: Path, sets) -> Path:
    lines = [
        f"kb_catalog:main:\n  path: {work}/kb/catalog.db",
        f"kb_blob:main:\n  root: {work}/kb/blobs",
        f"kb_lexical:main:\n  path: {work}/kb/lexical.db",
        f"embedding:e5:\n  api_type: hf\n  model: {EMBEDDER}",
    ]
    lines += [f"vector_store:{s}:\n  api_type: faiss\n  metric: cosine\n  dim: 384" for s in sets]
    path = work / "resources.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def split(cases: Path, tag: str) -> Path:
    out = cases.with_name(f"cases_{tag}.jsonl")
    rows = [
        line for line in cases.read_text("utf-8").splitlines() if tag in json.loads(line)["tags"]
    ]
    out.write_text("\n".join(rows) + "\n", "utf-8")
    return out


async def ingest(kb, name: str, corpus: Path) -> Dict[str, Any]:
    from operonx_kb import ChunkerSpec, CollectionSpec, DenseIndexSpec, GraphSpec
    from operonx_kb.model.collection import AnalyzerSpec, LexicalIndexSpec

    vi = LANGS[name] == "vi"
    analyzer = AnalyzerSpec(kind="vi", fold_diacritics=True) if vi else AnalyzerSpec()
    kb.create_collection(
        name,
        CollectionSpec(
            chunker=ChunkerSpec(),
            dense=DenseIndexSpec(embedder="e5", store=f"vector_store:{name}", batch_size=64,
                                 passage_template="passage: {text}", query_template="query: {text}"),
            lexical=LexicalIndexSpec(collection=f"lex_{name}", analyzer=analyzer),
            language=LANGS[name],
            graph=GraphSpec(),
        ),
    )  # fmt: skip
    started = time.perf_counter()
    files = sorted(p for p in corpus.iterdir() if p.is_file())
    for i, path in enumerate(files):
        await kb.add(name, str(path), key=path.name)
        if (i + 1) % 500 == 0:
            log(f"  {name}: {i + 1}/{len(files)} documents")
    seconds = time.perf_counter() - started
    report = kb.verify(name)
    assert report.ok, report.problems[:3]
    return {"documents": len(files), "chunks": report.chunks, "seconds": round(seconds, 1)}


def graph_size(kb, name: str) -> Dict[str, Any]:
    from operonx_kb.model.collection import GraphSpec
    from operonx_kb.retrieval import graph as graphs

    spec = kb.collection(name).spec.graph or GraphSpec()
    graphs._graphs.clear()
    started = time.perf_counter()
    g = graphs.graph_for(kb.catalog, kb.catalog_key, name,
                         max_df_share=spec.max_df_share, max_df_min=spec.max_df_min)  # fmt: skip
    return {"chunks": len(g.chunk_ids), "concepts": g.concepts, "edges": g.edges,
            "dropped_common": g.dropped, "cold_build_ms": round(1000 * (time.perf_counter() - started), 1)}  # fmt: skip


def slim(report: Dict[str, Any]) -> Dict[str, Any]:
    return {"cases": report["cases"], "metrics": report["means"], "p50_ms": report["p50_ms"],
            "p95_ms": report["p95_ms"], "errors": len(report.get("errors") or [])}  # fmt: skip


async def main(args) -> None:
    import operonx
    from operonx.core.registry import ResourceHub

    import operonx_kb  # noqa: F401 — registers the kb_* categories
    from operonx_kb import KnowledgeBase
    from operonx_kb.eval import compare_metric, evaluate_search

    work = args.work.resolve()
    (work / "kb").mkdir(parents=True, exist_ok=True)
    sets_dir = work / "sets"
    for name in args.sets:
        if (sets_dir / name / "cases.jsonl").exists():
            continue
        if name in MULTIHOP:
            prepare_multihop.multihop_set(name, sets_dir / name, work / "cache", args.questions)
        elif name.startswith("xquad_"):
            prepare_eval.xquad_set(LANGS[name], sets_dir / name, work / "cache")
        else:
            prepare_eval.corpus_vi_set(sets_dir / name)
    ResourceHub.reset_instance()
    operonx.bootstrap(resources=resources(work, args.sets), env=False)
    kb = KnowledgeBase()
    out: Dict[str, Any] = {"embedder": EMBEDDER, "graph": {}, "sets": {}, "gate": {}}
    from operonx_kb.model.collection import GraphSpec

    out["graph"] = GraphSpec().model_dump()
    for name in args.sets:
        log(f"ingest {name}")
        info = await ingest(kb, name, sets_dir / name / "corpus")
        info["graph"] = graph_size(kb, name)
        out["sets"][name] = info
        log(f"  {info}")
        cases = sets_dir / name / "cases.jsonl"
        if name in MULTIHOP:
            cases = split(cases, "test")
        per_case, row = {}, {}
        for mode in ("hybrid", "graph"):
            report = await evaluate_search(kb, name, cases, mode=mode, ks=(2, 5, 10, 20))
            row[mode] = slim(report)
            per_case[mode] = {"per_case": report["per_case"]}
            log(f"  {name} {mode}: {row[mode]['metrics']}")
        row["graph_minus_hybrid"] = {
            m: compare_metric(per_case["hybrid"], per_case["graph"], m) for m in METRICS
        }
        out["gate"][name] = row
        (work / "k6.json").write_text(json.dumps(out, indent=1), "utf-8")
    log(f"done: {work / 'k6.json'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("work", type=Path)
    ap.add_argument("--sets", nargs="+", default=list(LANGS))
    ap.add_argument("--questions", type=int, default=400)
    asyncio.run(main(ap.parse_args()))
