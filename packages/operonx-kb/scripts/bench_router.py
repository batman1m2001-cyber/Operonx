"""The router gate (track5 §9.8): ``mode="auto"`` against ``hybrid`` and ``graph``.

    uv run python scripts/bench_router.py WORK [--sets 2wiki musique xquad_en xquad_vi corpus_vi]

``WORK`` is a ``scripts/bench_k6.py`` work folder whose collections are already
ingested (dense, lexical and the concept graph): nothing is parsed or embedded
here; the in-memory vector stores are rebuilt from the catalog's embedding cache. Each
set's cases (the multi-hop sets' ``test`` split) are searched in ``hybrid``,
``graph`` and ``auto`` mode, and ``auto`` is compared with each, paired per case
(``compare_metric``). Also recorded: the share of questions ``auto`` routed to the
graph. Results go to ``WORK/router.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import bench_k6  # noqa: E402

from operonx_kb.retrieval.router import relation_rule  # noqa: E402

MODES = ("hybrid", "graph", "auto")
METRICS = ("recall@5", "recall@10", "mrr")


async def main(args) -> None:
    import operonx
    from operonx.core.registry import ResourceHub

    import operonx_kb  # noqa: F401 — registers the kb_* categories
    from operonx_kb import KnowledgeBase
    from operonx_kb.eval import compare_metric, evaluate_search

    work = args.work.resolve()
    ResourceHub.reset_instance()
    operonx.bootstrap(resources=bench_k6.resources(work, args.sets), env=False)
    kb = KnowledgeBase()
    out: Dict[str, Any] = {}
    for name in args.sets:
        bench_k6.log(f"rebuild {name}: {await kb.rebuild(name)}")
        cases = work / "sets" / name / "cases.jsonl"
        if name in bench_k6.MULTIHOP:
            cases = bench_k6.split(cases, "test")
        rows = [json.loads(line) for line in cases.read_text("utf-8").splitlines() if line]
        routed = sum(relation_rule(r["input"]["query"]) is not None for r in rows)
        per_case, row = {}, {"cases": len(rows), "routed_to_graph": round(routed / len(rows), 3)}
        for mode in MODES:
            report = await evaluate_search(kb, name, cases, mode=mode, ks=(5, 10))
            row[mode] = bench_k6.slim(report)
            per_case[mode] = {"per_case": report["per_case"]}
            bench_k6.log(f"  {name} {mode}: {row[mode]['metrics']}")
        for other in ("hybrid", "graph"):
            row[f"auto_minus_{other}"] = {
                m: compare_metric(per_case[other], per_case["auto"], m) for m in METRICS
            }
        out[name] = row
        (work / "router.json").write_text(json.dumps(out, indent=1), "utf-8")
    bench_k6.log(f"done: {work / 'router.json'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("work", type=Path)
    ap.add_argument("--sets", nargs="+", default=list(bench_k6.LANGS))
    asyncio.run(main(ap.parse_args()))
