"""12 RAG Advanced — Run locally.

Keyword search + RRF, hybrid search, reranking.

Example 1: no API key (keyword + RRF).
Examples 2-3: require OPENAI_API_KEY (and optionally PINECONE_API_KEY).

Chạy: cd examples && uv run python ex12_rag_advanced/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush

from ex12_rag_advanced.workflow import DOCUMENTS, build_keyword_rrf


async def main():
    # 1. Keyword search + RRF (no API key needed)
    print("=" * 55)
    print("1. Keyword Search + RRF")
    print("=" * 55)
    engine = Hush(build_keyword_rrf())
    result = await engine.run(inputs={"query": "biển đẹp", "documents": DOCUMENTS})
    print("  Query: 'biển đẹp'")
    print("  Top results (RRF merged):")
    for i, doc in enumerate(result["results"][:3]):
        print(f"    {i + 1}. {doc[:60]}...")

    # 2. Hybrid RAG (keyword + vector) — needs OPENAI_API_KEY
    if not os.environ.get("OPENAI_API_KEY"):
        print("\n  Examples 2-3 skipped — OPENAI_API_KEY chưa set")
        return

    print()
    print("=" * 55)
    print("2. Hybrid RAG (keyword + vector)")
    print("=" * 55)

    from hush.core import END, PARENT, START, GraphOp
    from hush.core.ops import op
    from hush.providers import EmbeddingOp, LLMOp, PromptOp

    from ex12_rag_advanced.workflow import cosine_search, keyword_search, reciprocal_rank_fusion

    # Pre-compute doc embeddings
    print("  Embedding documents...")
    with GraphOp(name="embed-docs") as embed_graph:
        embed = EmbeddingOp.of(
            resource="openai",
            texts=PARENT["texts"],
            outputs={"embeddings": PARENT["vectors"]},
        )
        START >> embed >> END

    embed_result = await Hush(embed_graph).run(inputs={"texts": DOCUMENTS})
    doc_vectors = embed_result["vectors"]

    @op
    def kw_search_fn(query, docs):
        return {"results": keyword_search(query, docs, top_k=8)}

    @op
    def vec_search_fn(qv, docs, dvs):
        return {"results": cosine_search(qv[0], dvs, docs, top_k=8)}

    @op
    def merge_results(kw, vec):
        return {"context_docs": reciprocal_rank_fusion([kw, vec])[:5]}

    with GraphOp(name="hybrid-rag") as graph:
        kw = kw_search_fn(query=PARENT["query"], docs=PARENT["documents"])
        embed_q = EmbeddingOp.of(resource="openai", texts=PARENT["query"])
        vec = vec_search_fn(
            qv=embed_q["embeddings"], docs=PARENT["documents"], dvs=PARENT["doc_vectors"]
        )
        mrg = merge_results(kw=kw["results"], vec=vec["results"])
        p = PromptOp.of(
            template={
                "system": "Trả lời câu hỏi dựa trên context.\nContext:\n{context}",
                "user": "{query}",
            },
            context=mrg["context_docs"],
            query=PARENT["query"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini", messages=p["messages"], outputs={"content": PARENT["answer"]}
        )
        mrg["context_docs"] >> PARENT["sources"]
        START >> [kw, embed_q]
        embed_q >> vec
        [kw, vec] >> mrg >> p >> llm >> END

    for query in ["Thành phố nào có bãi biển Mỹ Khê?", "Đảo lớn nhất Việt Nam ở đâu?"]:
        result = await Hush(graph).run(
            inputs={"query": query, "documents": DOCUMENTS, "doc_vectors": doc_vectors}
        )
        print(f"\n  Q: {query}")
        print(f"  A: {result['answer']}")


if __name__ == "__main__":
    asyncio.run(main())
