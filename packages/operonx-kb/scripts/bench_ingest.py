"""Ingest throughput of the PDF path (K1 gate e: measured, no target).

    PYTHONPATH=<operonx branch> uv run python scripts/bench_ingest.py [--pages 60] [--repeat 3]

Measures, on the golden PDFs and on a generated N-page two-column PDF:
- parse: docling-parse backend and heuristic layout, separately (PdfParser stats);
- ingest: the whole graph (plan → parse → tree → chunk → embed → index → commit)
  with the HashEmbedder and an in-memory FAISS index, so model latency is excluded;
  the engine is built and the stores opened by a warm-up document first.
Prints a Markdown table.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "tests" / "golden" / "docs"


def big_pdf(pages: int) -> bytes:
    spec = importlib.util.spec_from_file_location(
        "make_pdfs", ROOT / "tests" / "golden" / "make_pdfs.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import io

    buf = io.BytesIO()
    c = mod._canvas(buf)
    w = mod._Writer(c, [(56, 286), (309, 539)], top=780, bottom=60)
    section = 0
    while w.page <= pages:
        section += 1
        w.heading(f"{section} Section about retrieval")
        for _ in range(3):
            w.para(mod.LOREM + " " + mod.LOREM)
    c.save()
    return buf.getvalue()


def parse_times(data: bytes, repeat: int):
    from operonx_kb.pdf.parser import PdfParser

    parser = PdfParser()
    parser.parse(data)  # warm up docling-parse's font resources
    runs = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        parsed = parser.parse(data)
        runs.append(
            (
                time.perf_counter() - t0,
                parsed.stats["backend_s"],
                parsed.stats["layout_s"],
                len(parsed.pages),
            )
        )
    return runs


async def ingest_time(data: bytes, name: str, repeat: int) -> float:
    from operonx.core.registry import ResourceHub

    import operonx_kb.testing.fakes  # noqa: F401
    from operonx_kb import CollectionSpec, DenseIndexSpec, KnowledgeBase

    times = []
    for i in range(repeat):
        root = Path(tempfile.mkdtemp())
        (root / "r.yaml").write_text(
            f"kb_catalog:main:\n  path: {root}/c.db\nkb_blob:main:\n  root: {root}/b\n"
            "vector_store:kb:\n  api_type: faiss\n  metric: cosine\n  dim: 64\nfake_embedding:hash:\n  dim: 64\n"
        )
        ResourceHub.set_instance(ResourceHub.from_yaml(root / "r.yaml"))
        kb = KnowledgeBase()
        kb.create_collection(
            "bench",
            CollectionSpec(
                dense=DenseIndexSpec(embedder="fake_embedding:hash", store="vector_store:kb")
            ),
        )
        (root / name).write_bytes(data)
        (root / "warmup.txt").write_text("Builds the engine and opens the stores before timing.")
        await kb.add("bench", str(root / "warmup.txt"))
        t0 = time.perf_counter()
        await kb.add("bench", str(root / name))
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=60)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()
    inputs = [(p.name, p.read_bytes()) for p in sorted(DOCS.glob("*.pdf"))]
    inputs.append((f"generated_{args.pages}p.pdf", big_pdf(args.pages)))
    print(
        "| file | pages | parse median (s) | backend (s) | layout (s) | parse pages/s | ingest median (s) | ingest pages/s |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for name, data in inputs:
        runs = parse_times(data, args.repeat)
        total = statistics.median(r[0] for r in runs)
        backend = statistics.median(r[1] for r in runs)
        layout = statistics.median(r[2] for r in runs)
        pages = runs[0][3]
        ingest = asyncio.run(ingest_time(data, name, args.repeat))
        print(
            f"| {name} | {pages} | {total:.3f} | {backend:.3f} | {layout:.3f} | {pages / total:.1f} | {ingest:.3f} | {pages / ingest:.1f} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
