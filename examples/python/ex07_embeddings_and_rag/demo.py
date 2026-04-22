"""07 Embeddings & RAG — Python-side demo.

Basic embedding, simple RAG (cosine search), optional reranking.
Requires ``OPENAI_API_KEY`` + a ``resources.yaml`` with ``openai`` embedding
and ``gpt-4o-mini`` LLM resources. The ``rerank`` scenario also needs a
``bge-m3`` reranker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from examples.python._common import (  # noqa: E402
    BenchReporter,
    Scenario,
    build_engine,
    load_env,
    parse_args,
    run_async,
)
from examples.python.ex07_embeddings_and_rag.workflow import (  # noqa: E402
    DOCUMENTS,
    build_basic_embedding,
    build_rag_with_rerank,
    build_simple_rag,
)


HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex07_embeddings_and_rag")

    # 1. Basic embedding — texts only.
    embed_graph = build_basic_embedding()
    embed_engine = build_engine(embed_graph, langfuse=langfuse)
    await reporter.record(
        "embed",
        lambda e=embed_engine, i=INPUTS["embed"]: e.run(inputs=i),
        runs=runs,
    )

    # 2. Simple RAG — pre-compute doc embeddings (untimed), then query.
    precomp = build_engine(build_basic_embedding(), langfuse=False)
    embed_result = await precomp.run(inputs={"texts": DOCUMENTS})
    doc_vectors = embed_result["vectors"]

    rag_graph = build_simple_rag()
    rag_engine = build_engine(rag_graph, langfuse=langfuse)
    rag_inputs = {
        "query": INPUTS["rag"]["query"],
        "documents": DOCUMENTS,
        "doc_vectors": doc_vectors,
    }
    await reporter.record(
        "rag",
        lambda e=rag_engine, i=rag_inputs: e.run(inputs=i),
        runs=runs,
    )

    # 3. RAG + rerank (optional — needs bge-m3 resource).
    rerank_graph = build_rag_with_rerank()
    rerank_engine = build_engine(rerank_graph, langfuse=langfuse)
    rerank_inputs = {"query": INPUTS["rerank"]["query"], "documents": DOCUMENTS}
    await reporter.record(
        "rerank",
        lambda e=rerank_engine, i=rerank_inputs: e.run(inputs=i),
        runs=runs,
    )

    reporter.save()


def main() -> int:
    args = parse_args("ex07_embeddings_and_rag")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
