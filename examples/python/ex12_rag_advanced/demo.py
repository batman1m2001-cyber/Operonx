"""12 RAG Advanced — Python-side demo.

Keyword RRF (no API key) + hybrid (vector + keyword) RAG.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from examples.python._common import (  # noqa: E402
    BenchReporter,
    build_engine,
    load_env,
    parse_args,
    run_async,
)
from examples.python.ex12_rag_advanced.workflow import (  # noqa: E402
    DOCUMENTS,
    build_hybrid_rag,
    build_keyword_rrf,
)


HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex12_rag_advanced")

    # 1. Keyword RRF — pure compute
    kw_graph = build_keyword_rrf()
    kw_engine = build_engine(kw_graph, langfuse=langfuse)
    kw_inputs = {"query": INPUTS["keyword_rrf"]["query"], "documents": DOCUMENTS}
    await reporter.record(
        "keyword_rrf",
        lambda e=kw_engine, i=kw_inputs: e.run(inputs=i),
        runs=runs,
    )

    # 2. Hybrid — needs pre-computed doc embeddings (untimed precomp).
    from operon.core import END, PARENT, START, GraphOp
    from operon.providers import EmbeddingOp

    with GraphOp(name="embed-docs") as embed_graph:
        embed = EmbeddingOp.of(
            resource="openai",
            texts=PARENT["texts"],
            outputs={"embeddings": PARENT["vectors"]},
        )
        START >> embed >> END

    precomp = build_engine(embed_graph, langfuse=False)
    embed_result = await precomp.run(inputs={"texts": DOCUMENTS})
    doc_vectors = embed_result["vectors"]

    hybrid_graph = build_hybrid_rag()
    hybrid_engine = build_engine(hybrid_graph, langfuse=langfuse)
    hybrid_inputs = {
        "query": INPUTS["hybrid"]["query"],
        "documents": DOCUMENTS,
        "doc_vectors": doc_vectors,
    }
    await reporter.record(
        "hybrid",
        lambda e=hybrid_engine, i=hybrid_inputs: e.run(inputs=i),
        runs=runs,
    )

    reporter.save()


def main() -> int:
    args = parse_args("ex12_rag_advanced")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
