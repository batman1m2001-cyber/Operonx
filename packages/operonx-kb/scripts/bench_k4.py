"""The K4 gate (PLAN §9): contextual enrichment and tree search against hybrid.

    uv run python scripts/bench_k4.py WORK [--sets vi_public,xquad_vi,xquad_en,corpus_vi]
        [--llm gpt-4o-mini --llm-resources ../../resources.yaml --env ../../.env]
        [--tree-cases 100] [--embeddings OLD_CATALOG.db ...] [--budget 8]
        [--cases 100 --docs 150]

For each set (built by ``scripts/prepare_eval.py`` / ``prepare_vi_public.py`` under
``WORK/sets`` when missing), two collections over the same files, chunker,
embedder (``intfloat/multilingual-e5-small``) and analyzer (``vi`` + folding on
Vietnamese, ``simple`` on English, the K2/D5 choices):

- ``<set>``: the K2 default with a ``tree`` index (the tree changes no chunk, so
  its dense, lexical and hybrid results are the K2 baseline's);
- ``<set>_ctx``: the same with ``contextual`` enrichment.

Then, with operonx ``Eval`` and the KB's evaluators:

1. dense, lexical and hybrid on both collections, every case; contextual minus
   baseline per mode with operonx's paired test (McNemar for Recall@k on
   single-label sets, paired bootstrap for MRR and nDCG);
2. ``tree`` on ``--tree-cases`` cases of each set (drawn with a fixed seed: tree
   search calls the model per query), against hybrid on the same cases;
3. the model's tokens and USD for every ingest stage (from the ingest reports)
   and per tree query (from the ``LLMOp`` spans of the eval's traces), priced at
   gpt-4o-mini's list prices unless the resource carries its own.

``--cases N`` makes it a sample: N cases per set (a fixed seed) over a corpus of the
documents their labels cite, every document an earlier run already ingested (its
model calls are cached, so it is free), then random distractors up to ``--docs``.
Both collections of a set get the same sample, so every comparison stays paired; the
model calls of ingest (a tree per document, a context per chunk) follow the corpus,
which is where nearly all of them are. The sample is recorded in ``k4.json``.

``--embeddings`` copies the embedding cache of earlier runs' catalogs (the K2 and
D5 work folders) into this one, so the baseline's chunks are not embedded again.
Results go to ``WORK/k4.json``; ``docs/bench/k4.md`` is written from them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import bench_k2  # noqa: E402

EMBEDDER = bench_k2.EMBEDDER
LANGS = bench_k2.LANGS
SETS = ["vi_public", "xquad_vi", "xquad_en", "corpus_vi"]
#: gpt-4o-mini list prices (USD per token), used when the resource carries none.
PRICES = {"input": 0.15e-6, "cached_input": 0.075e-6, "output": 0.60e-6}
#: Collection types of the default-on rule (PLAN §9 gate).
TYPES = {
    "short heading-less articles": ["xquad_vi", "xquad_en"],
    "templated business documents": ["corpus_vi"],
    "mixed real corpus (Wikipedia paragraphs, legal PDFs)": ["vi_public"],
}
log = bench_k2.log


def resources(work: Path, sets: List[str], llm_block: str) -> Path:
    lines = [
        f"kb_catalog:main:\n  path: {work}/kb/catalog.db",
        f"kb_blob:main:\n  root: {work}/kb/blobs",
        f"kb_lexical:main:\n  path: {work}/kb/lexical.db",
        f"embedding:e5:\n  api_type: hf\n  model: {EMBEDDER}",
    ]
    for name in sets:
        for coll in (name, f"{name}_ctx"):
            lines.append(f"vector_store:{coll}:\n  api_type: faiss\n  metric: cosine\n  dim: 384")
    path = work / "resources.yaml"
    path.write_text("\n".join(lines) + "\n" + llm_block, encoding="utf-8")
    return path


def seed_embeddings(sources: List[Path], target: Path) -> int:
    """Copy embedding-cache rows of earlier catalogs into ``target`` (same schema)."""
    copied = 0
    with sqlite3.connect(target) as dst:
        for src in sources:
            dst.execute("ATTACH DATABASE ? AS old", (str(src),))
            before = dst.total_changes
            dst.execute(
                "INSERT OR IGNORE INTO kb_embedding_cache SELECT * FROM old.kb_embedding_cache"
            )
            copied += dst.total_changes - before
            dst.commit()
            dst.execute("DETACH DATABASE old")
    return copied


def spec(name: str, llm: str, contextual: bool):
    from operonx_kb import ChunkerSpec, CollectionSpec, ContextualSpec, DenseIndexSpec, TreeSpec
    from operonx_kb.model.collection import AnalyzerSpec, LexicalIndexSpec

    analyzer = bench_k2.ANALYZERS["vi+fold" if LANGS[name] == "vi" else "simple"]
    coll = f"{name}_ctx" if contextual else name
    return CollectionSpec(
        chunker=ChunkerSpec(),
        dense=DenseIndexSpec(embedder="e5", store=f"vector_store:{coll}", batch_size=32,
                             passage_template="passage: {text}", query_template="query: {text}"),
        lexical=LexicalIndexSpec(collection=coll, analyzer=AnalyzerSpec(**analyzer)),
        language=LANGS[name],
        contextual=ContextualSpec(llm=llm) if contextual else None,
        tree=None if contextual else TreeSpec(llm=llm),
    )  # fmt: skip


def _usage(stats: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("calls", "cached", "prompt_tokens", "completion_tokens", "cached_tokens")
    return {k: int(stats.get(k) or 0) for k in keys}


def _add(total: Dict[str, Any], part: Dict[str, Any]) -> None:
    for k, v in part.items():
        total[k] = total.get(k, 0) + v


def usd(u: Dict[str, Any]) -> float:
    """List-price cost of a usage total (cached input at the cached rate)."""
    fresh = u.get("prompt_tokens", 0) - u.get("cached_tokens", 0)
    return (fresh * PRICES["input"] + u.get("cached_tokens", 0) * PRICES["cached_input"]
            + u.get("completion_tokens", 0) * PRICES["output"])  # fmt: skip


async def ingest(kb, coll: str, corpus: Path, concurrency: int) -> Dict[str, Any]:
    """Ingest a corpus, a few documents at once; the model usage of each stage, by mime."""
    files = sorted(p for p in corpus.iterdir() if p.is_file())
    gate = asyncio.Semaphore(concurrency)
    usage: Dict[str, Dict[str, Dict[str, int]]] = {}
    done = skipped = 0
    started = time.perf_counter()

    async def one(path: Path) -> None:
        nonlocal done, skipped
        async with gate:
            result = await kb.add(coll, str(path), key=path.name)
        skipped += result["action"] == "skip"
        mime = "pdf" if path.suffix.lower() == ".pdf" else "other"
        stats = result.get("stats") or {}
        tree = stats.get("tree") or {}
        for stage, part in (("contextual", stats.get("contextual") or {}),
                            ("toc", tree.get("toc_usage") or {}),
                            ("summary", tree.get("summary_usage") or {})):  # fmt: skip
            if part:
                _add(usage.setdefault(stage, {}).setdefault(mime, {}), _usage(part))
        done += 1
        if done % 50 == 0:
            log(f"  {coll}: {done}/{len(files)} documents")

    await asyncio.gather(*(one(p) for p in files))
    if skipped:
        # Ingested by an earlier run of this script: the catalog has them, the
        # in-memory FAISS index of this process does not. Nothing is embedded.
        await kb.rebuild(coll)
    report = kb.verify(coll)
    assert report.ok, report.problems[:3]
    return {"documents": len(files), "skipped": skipped, "chunks": report.chunks, "usage": usage,
            "seconds": round(time.perf_counter() - started, 1)}  # fmt: skip


def corpus_size(kb, coll: str) -> Dict[str, Any]:
    """Pages and tokens of a collection's active versions, PDFs apart."""
    out = {
        "pdf": {"documents": 0, "pages": 0, "tokens": 0},
        "other": {"documents": 0, "pages": 0, "tokens": 0},
    }
    cat = kb.catalog
    for doc in kb.documents(coll):
        kind = "pdf" if doc.mime == "application/pdf" else "other"
        version = doc.active_version_id
        occ = cat.version_chunks(version)
        chunks = cat.get_chunks([o.chunk_id for o in occ])
        out[kind]["documents"] += 1
        out[kind]["pages"] += len(cat.pages(version))
        out[kind]["tokens"] += sum(c.token_count for c in chunks.values())
    return out


class LLMUsage:
    """A trace consumer adding up the ``LLMOp`` usage of each run (one per eval item)."""

    def __init__(self):
        from operonx.telemetry.consumer import Consumer

        class _Consumer(Consumer):
            def __init__(inner):
                super().__init__()
                inner.runs: List[Dict[str, int]] = []

            def consume(inner, trace):
                total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
                for node in trace.nodes:
                    if node.op_type == "llm" and not node.is_yield:
                        u = (node.outputs or {}).get("usage") or {}
                        total["calls"] += 1
                        for k in ("prompt_tokens", "completion_tokens", "cached_tokens"):
                            total[k] += int(u.get(k) or 0)
                inner.runs.append(total)

        self.consumer = _Consumer()

    def summary(self) -> Dict[str, Any]:
        runs = self.consumer.runs
        n = len(runs) or 1
        total: Dict[str, int] = {}
        for r in runs:
            _add(total, r)
        return {"queries": len(runs), **{f"{k}_per_query": round(v / n, 2) for k, v in total.items()},
                "usd_per_query": round(usd(total) / n, 6), "usd": round(usd(total), 4)}  # fmt: skip


def retarget(cases: Path, collection: str, out: Path) -> Path:
    """The cases with ``input.collection`` set to ``collection`` (a case names the
    collection its labels resolve in)."""
    rows = [json.loads(line) for line in cases.read_text("utf-8").splitlines() if line.strip()]
    for row in rows:
        row["input"]["collection"] = collection
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    return out


def sample_set(kb, name: str, sets_dir: Path, work: Path, n_cases: int, n_docs: int):
    """A sample of a set (see ``--cases``): ``(cases, corpus, summary)``, linked under
    ``WORK/sample/<name>``."""
    rows = [json.loads(line) for line in (sets_dir / name / "cases.jsonl").read_text("utf-8").splitlines()
            if line.strip()]  # fmt: skip
    picked = random.Random(4).sample(rows, min(n_cases, len(rows)))
    files = {p.name: p for p in (sets_dir / name / "corpus").iterdir() if p.is_file()}
    cited = {r["doc_key"] for row in picked for r in row["expected"].get("relevant", [])}
    try:
        done = {d.key for d in kb.documents(name)}  # ingested by an earlier run: cached
    except Exception:  # noqa: BLE001 — no such collection yet
        done = set()
    keep = sorted((cited | done) & set(files))
    rest = sorted(set(files) - set(keep))
    extra = random.Random(4).sample(rest, max(0, min(len(rest), n_docs - len(keep))))
    root = work / "sample" / name
    corpus = root / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    for old in corpus.iterdir():
        old.unlink()
    for key in keep + extra:
        (corpus / key).symlink_to(files[key].resolve())
    cases = root / "cases.jsonl"
    cases.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in picked), "utf-8")
    summary = {"cases": len(picked), "of_cases": len(rows), "documents": len(keep) + len(extra),
               "of_documents": len(files), "cited": len(cited & set(files)),
               "already_ingested": len(done & set(files))}  # fmt: skip
    return cases, corpus, summary


def lift(cmp: Dict[str, Any]) -> str:
    """``lift`` / ``loss`` when significant at 0.05, else ``noise``."""
    if cmp["p"] < 0.05:
        return "lift" if cmp["diff"] > 0 else "loss"
    return "noise"


async def main(args) -> None:
    import operonx
    from operonx.core.registry import ResourceHub

    import operonx_kb  # noqa: F401 — registers the kb_* categories
    from operonx_kb import KnowledgeBase
    from operonx_kb.eval import compare_metric, evaluate_search

    work = args.work.resolve()
    (work / "kb").mkdir(parents=True, exist_ok=True)
    sets_dir = work / "sets"
    for name in SETS:
        if (sets_dir / name / "cases.jsonl").exists():
            continue
        if name.startswith("xquad_"):
            bench_k2.prepare_eval.xquad_set(LANGS[name], sets_dir / name, work / "cache")
        elif name == "corpus_vi":
            bench_k2.prepare_eval.corpus_vi_set(sets_dir / name)
        else:
            bench_k2.prepare_vi_public.vi_public_set(
                sets_dir / name, ROOT / ".operonx" / "cache" / name
            )

    from dotenv import load_dotenv

    load_dotenv(args.env, override=False)  # the model's key; never printed
    text = args.llm_resources.read_text("utf-8")
    block = re.search(rf"^llm:{re.escape(args.llm)}:\n(?:[ \t]+.*\n?)+", text, re.M)
    if block is None:
        raise SystemExit(f"no llm:{args.llm} in {args.llm_resources}")
    llm_block = block.group(0).rstrip("\n") + "\n  max_retries: 4\n  timeout: 60\n"
    ResourceHub.reset_instance()
    operonx.bootstrap(resources=resources(work, SETS, llm_block), env=False)
    kb = KnowledgeBase()
    if args.embeddings:
        log(
            f"embedding cache rows copied: {seed_embeddings(args.embeddings, work / 'kb' / 'catalog.db')}"
        )
    results_path = work / "k4.json"
    results: Dict[str, Any] = (
        json.loads(results_path.read_text("utf-8")) if results_path.exists() else {}
    )
    results.update(embedder=EMBEDDER, llm=args.llm, prices=PRICES, types=TYPES)
    results.setdefault("sets", {})

    def save() -> None:
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")

    def spent() -> float:
        total = 0.0
        for s in results["sets"].values():
            for side in ("ingest", "ingest_ctx"):
                for stage in (s.get(side) or {}).get("usage", {}).values():
                    for part in stage.values():
                        total += usd(part)
            total += (s.get("tree_query") or {}).get("usd", 0.0)
        return total

    for name in SETS:
        row = results["sets"].setdefault(name, {})
        cases = sets_dir / name / "cases.jsonl"
        corpus = sets_dir / name / "corpus"
        if args.cases:
            cases, corpus, row["sample"] = sample_set(
                kb, name, sets_dir, work, args.cases, args.docs
            )
            log(f"sample {name}: {row['sample']}")
        else:
            row.pop("sample", None)
        for coll, contextual, key in ((name, False, "ingest"), (f"{name}_ctx", True, "ingest_ctx")):
            kb.create_collection(coll, spec(name, args.llm, contextual))
            log(f"ingest {coll}")
            done = await ingest(kb, coll, corpus, args.concurrency)
            if not (
                done["skipped"] == done["documents"] and key in row
            ):  # else: the earlier run's costs
                row[key] = done
            row[key]["size"] = corpus_size(kb, coll)
            save()
            log(f"  {coll}: {row[key]['documents']} documents, {row[key]['chunks']} chunks, "
                f"{row[key]['seconds']} s; spent so far ${spent():.2f}")  # fmt: skip
            if spent() > args.budget:
                raise SystemExit(f"spend ${spent():.2f} passed the budget ${args.budget}")

        per_case: Dict[str, Any] = {}
        row["modes"] = {}
        for coll in (name, f"{name}_ctx"):
            dataset = retarget(cases, coll, work / f"cases_{coll}.jsonl")
            for mode in ("dense", "lexical", "hybrid"):
                log(f"eval {coll} {mode}")
                report = await evaluate_search(kb, coll, dataset, mode=mode)
                row["modes"][f"{coll}|{mode}"] = bench_k2.slim(report)
                per_case[f"{coll}|{mode}"] = {"per_case": report["per_case"]}
        row["contextual_minus_baseline"] = {
            mode: {m: compare_metric(per_case[f"{name}|{mode}"], per_case[f"{name}_ctx|{mode}"], m)
                   for m in ("recall@10", "mrr", "ndcg@10")}
            for mode in ("dense", "lexical", "hybrid")
        }  # fmt: skip
        save()

        rows = [
            json.loads(line)
            for line in (work / f"cases_{name}.jsonl").read_text("utf-8").splitlines()
        ]
        sample = random.Random(4).sample(rows, min(args.tree_cases, len(rows)))
        subset = work / f"tree_{name}.jsonl"
        subset.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sample), "utf-8"
        )
        usage = LLMUsage()
        log(f"eval {name} tree on {len(sample)} cases")
        report = await evaluate_search(kb, name, subset, mode="tree", trace=[usage.consumer])
        row["tree"] = bench_k2.slim(report)
        row["tree_query"] = usage.summary()
        row["tree_minus_hybrid"] = {
            m: compare_metric(per_case[f"{name}|hybrid"], {"per_case": report["per_case"]}, m)
            for m in ("recall@10", "mrr", "ndcg@10", "recall@5")
        }
        row["hybrid_on_tree_cases"] = {
            m: round(sum(per_case[f"{name}|hybrid"]["per_case"][m][r["id"]] for r in sample) / len(sample), 4)
            for m in ("recall@5", "recall@10", "mrr", "ndcg@10")
        }  # fmt: skip
        save()
        log(f"  {name}: tree {row['tree']['metrics']} | {row['tree_query']}; spent ${spent():.2f}")
        if spent() > args.budget:
            raise SystemExit(f"spend ${spent():.2f} passed the budget ${args.budget}")

    decisions = {}
    for technique, key in (
        ("contextual", "contextual_minus_baseline"),
        ("tree", "tree_minus_hybrid"),
    ):
        for kind, members in TYPES.items():
            verdicts = []
            for name in (m for m in members if m in SETS):
                cmp = results["sets"][name][key]
                cmp = cmp["hybrid"] if technique == "contextual" else cmp
                r10, mrr = lift(cmp["recall@10"]), lift(cmp["mrr"])
                verdicts.append("lift" in (r10, mrr) and "loss" not in (r10, mrr))
            if verdicts:
                decisions.setdefault(technique, {})[kind] = (
                    "default-on" if all(verdicts) else "opt-in"
                )
    results["decisions"] = decisions
    results["spent_usd"] = round(spent(), 4)
    save()
    log(f"done: {results_path}; decisions {decisions}; spent ${spent():.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("work", type=Path)
    ap.add_argument("--sets", default=",".join(SETS), help=f"comma-separated, of {SETS}")
    ap.add_argument("--llm", default="gpt-4o-mini", help="llm resource name in --llm-resources")
    ap.add_argument("--llm-resources", type=Path, default=ROOT.parent / "Operon" / "resources.yaml")
    ap.add_argument("--env", type=Path, default=ROOT.parent / "Operon" / ".env")
    ap.add_argument("--tree-cases", type=int, default=100, help="cases per set for tree search")
    ap.add_argument(
        "--embeddings",
        type=Path,
        nargs="*",
        default=[],
        help="catalogs to copy the embedding cache from",
    )
    ap.add_argument("--cases", type=int, default=0, help="sample this many cases per set (0: all)")
    ap.add_argument(
        "--docs",
        type=int,
        default=150,
        help="with --cases: documents per set, distractors included",
    )
    ap.add_argument("--concurrency", type=int, default=4, help="documents ingested at once")
    ap.add_argument(
        "--budget", type=float, default=8.0, help="stop when the model spend passes this (USD)"
    )
    ap.add_argument("--threads", type=int, default=8, help="torch CPU threads")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    parsed = ap.parse_args()
    SETS[:] = parsed.sets.split(",")
    import torch

    torch.set_num_threads(parsed.threads)
    asyncio.run(main(parsed))
