"""Tutorial 14: Advanced RAG — Reranking và Hybrid Search.

Ví dụ 1: Không cần API key (keyword search + RRF demo)
Ví dụ 2: Cần OPENAI_API_KEY (vector search + LLM answer)
Ví dụ 3: Cần OPENAI_API_KEY + PINECONE_API_KEY (reranking)

Học được:
- Keyword search (BM25-style) + vector search song song
- Reciprocal Rank Fusion (RRF): merge kết quả từ nhiều sources
- Hybrid search: keyword + vector + RRF → better retrieval
- Two-stage retrieval: retrieve top 20 → rerank to top 5
- RerankOp: cross-encoder reranking

Chạy: cd tutorial && uv run python examples/14_rag_advanced.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

import numpy as np
from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import op

# =============================================================================
# Sample data
# =============================================================================

DOCUMENTS = [
    "Hà Nội là thủ đô của Việt Nam, nằm ở miền Bắc, có hơn 1000 năm lịch sử.",
    "TP.HCM là thành phố lớn nhất Việt Nam về dân số, trung tâm kinh tế phía Nam.",
    "Đà Nẵng là thành phố lớn nhất miền Trung, nổi tiếng với bãi biển Mỹ Khê.",
    "Huế là cố đô của Việt Nam, nổi tiếng với Đại Nội và ẩm thực đặc sắc.",
    "Hạ Long là di sản thiên nhiên thế giới với hàng nghìn hòn đảo đá vôi.",
    "Sapa nằm ở Lào Cai, nổi tiếng với ruộng bậc thang và văn hóa dân tộc.",
    "Phú Quốc là đảo lớn nhất Việt Nam, thuộc tỉnh Kiên Giang, nổi tiếng du lịch biển.",
    "Nha Trang thuộc Khánh Hòa, được biết đến với bãi biển đẹp và du lịch nghỉ dưỡng.",
    "Cần Thơ là thành phố lớn nhất miền Tây, nổi tiếng với chợ nổi Cái Răng.",
    "Đà Lạt là thành phố ngàn hoa, nằm trên cao nguyên Lâm Đồng, khí hậu mát mẻ.",
]


# =============================================================================
# Search utilities
# =============================================================================


def keyword_search(query: str, documents: list, top_k: int = 5) -> list:
    """Simple keyword search — đếm term overlap."""
    query_terms = set(query.lower().split())
    results = []
    for doc in documents:
        doc_terms = set(doc.lower().split())
        overlap = len(query_terms & doc_terms)
        if overlap > 0:
            results.append((overlap, doc))
    results.sort(reverse=True)
    return [doc for _, doc in results[:top_k]]


def cosine_search(query_vec, doc_vecs, documents, top_k=5):
    """Cosine similarity search."""
    query_norm = np.array(query_vec) / np.linalg.norm(query_vec)
    scores = []
    for i, dv in enumerate(doc_vecs):
        dn = np.array(dv) / np.linalg.norm(dv)
        scores.append((float(np.dot(query_norm, dn)), documents[i]))
    scores.sort(reverse=True)
    return [doc for _, doc in scores[:top_k]]


def reciprocal_rank_fusion(results_lists: list, k: int = 60) -> list:
    """RRF: merge kết quả từ nhiều sources bằng reciprocal rank scoring.

    Score(doc) = Σ 1/(k + rank_i) for each result list i
    k=60 is the standard constant (Cormack et al., 2009).
    """
    scores = {}
    for results in results_lists:
        for rank, doc in enumerate(results):
            if doc not in scores:
                scores[doc] = 0.0
            scores[doc] += 1.0 / (k + rank + 1)
    sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in sorted_docs]


# =============================================================================
# Ví dụ 1: Keyword search + RRF (no API key needed)
# =============================================================================


async def example_1_keyword_rrf():
    """Keyword search từ 2 góc nhìn → merge bằng RRF."""
    print("=" * 50)
    print("Ví dụ 1: Keyword Search + RRF")
    print("=" * 50)

    @op
    def search_original(query, docs):
        return {"results": keyword_search(query, docs, top_k=5)}

    @op
    def search_expanded(query, docs):
        return {"results": keyword_search(query + " thành phố du lịch", docs, top_k=5)}

    @op
    def merge(r1, r2):
        return {"merged": reciprocal_rank_fusion([r1, r2])[:5]}

    with GraphOp(name="keyword-rrf") as graph:
        # 2 keyword searches khác nhau: original query + expanded query
        s_orig = search_original(query=PARENT["query"], docs=PARENT["documents"])
        s_exp = search_expanded(query=PARENT["query"], docs=PARENT["documents"])

        # Merge with RRF
        m = merge(
            r1=s_orig["results"],
            r2=s_exp["results"],
            outputs={"merged": PARENT["results"]},
        )

        # Parallel keyword searches → merge
        START >> [s_orig, s_exp] >> m >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={
            "query": "biển đẹp",
            "documents": DOCUMENTS,
        }
    )

    print("  Query: 'biển đẹp'")
    print("  Top results (RRF merged):")
    for i, doc in enumerate(result["results"][:3]):
        print(f"    {i + 1}. {doc[:60]}...")


# =============================================================================
# Ví dụ 2: Hybrid search (keyword + vector) → LLM
# =============================================================================


async def example_2_hybrid_rag():
    """Keyword + vector search song song → RRF merge → LLM answer."""
    print()
    print("=" * 50)
    print("Ví dụ 2: Hybrid RAG (keyword + vector)")
    print("=" * 50)

    import os

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return

    from hush.providers import EmbeddingOp, LLMOp, PromptOp

    # Step 0: Pre-compute document embeddings
    print("  Embedding documents...")
    with GraphOp(name="embed-docs") as embed_graph:
        embed = EmbeddingOp.of(
            resource="openai",
            texts=PARENT["texts"],
            outputs={"embeddings": PARENT["vectors"]},
        )
        START >> embed >> END

    embed_engine = Hush(embed_graph)
    embed_result = await embed_engine.run(inputs={"texts": DOCUMENTS})
    doc_vectors = embed_result["vectors"]
    print(f"  Embedded {len(doc_vectors)} documents")

    # Hybrid RAG workflow
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
        # Branch A: Keyword search
        kw = kw_search_fn(query=PARENT["query"], docs=PARENT["documents"])

        # Branch B: Vector search
        embed_q = EmbeddingOp.of(resource="openai", texts=PARENT["query"])
        vec = vec_search_fn(
            qv=embed_q["embeddings"],
            docs=PARENT["documents"],
            dvs=PARENT["doc_vectors"],
        )

        # RRF merge
        mrg = merge_results(kw=kw["results"], vec=vec["results"])

        # LLM answer
        p = PromptOp.of(
            template={
                "system": (
                    "Trả lời câu hỏi dựa trên context.\n"
                    "Nếu không tìm thấy, nói 'Không tìm thấy thông tin.'\n\n"
                    "Context:\n{context}"
                ),
                "user": "{query}",
            },
            context=mrg["context_docs"],
            query=PARENT["query"],
        )

        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["answer"]},
        )

        mrg["context_docs"] >> PARENT["sources"]

        # Parallel: keyword + vector search
        START >> [kw, embed_q]
        embed_q >> vec
        [kw, vec] >> mrg >> p >> llm >> END

    engine = Hush(graph)

    queries = [
        "Thành phố nào có bãi biển Mỹ Khê?",
        "Đảo lớn nhất Việt Nam ở đâu?",
    ]

    for query in queries:
        result = await engine.run(
            inputs={
                "query": query,
                "documents": DOCUMENTS,
                "doc_vectors": doc_vectors,
            }
        )
        print(f"\n  Q: {query}")
        print(f"  A: {result['answer']}")
        print(f"  Sources: {[s[:40] + '...' for s in result['sources'][:2]]}")


# =============================================================================
# Ví dụ 3: Two-stage retrieval (retrieve → rerank)
# =============================================================================


async def example_3_rerank():
    """Retrieve top 8 → RerankOp → top 3 → LLM answer."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Two-stage Retrieval (Reranking)")
    print("=" * 50)

    import os

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return
    if not os.environ.get("PINECONE_API_KEY"):
        print("  Skipped — PINECONE_API_KEY chưa set (cần cho RerankOp)")
        print("  Thêm reranking:bge-m3 vào resources.yaml:")
        print("    reranking:bge-m3:")
        print("      api_type: pinecone")
        print("      api_key: ${PINECONE_API_KEY}")
        print("      model: bge-reranker-v2-m3")
        print("      base_url: https://api.pinecone.io/rerank")
        return

    from hush.providers import LLMOp, PromptOp, RerankOp

    @op
    def retrieve(query, docs):
        return {"candidates": keyword_search(query, docs, top_k=8)}

    with GraphOp(name="rerank-rag") as graph:
        # Stage 1: Keyword retrieve top 8
        ret = retrieve(query=PARENT["query"], docs=PARENT["documents"])

        # Stage 2: Rerank to top 3
        rr = RerankOp.of(
            resource="bge-m3",
            query=PARENT["query"],
            documents=ret["candidates"],
            top_k=3,
        )

        # LLM answer
        p = PromptOp.of(
            template={
                "system": "Trả lời dựa trên context:\n\n{context}",
                "user": "{query}",
            },
            context=rr["reranks"],
            query=PARENT["query"],
        )

        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["answer"]},
        )

        rr["reranks"] >> PARENT["sources"]
        START >> ret >> rr >> p >> llm >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={
            "query": "Thành phố du lịch biển đẹp nhất?",
            "documents": DOCUMENTS,
        }
    )

    print("  Q: Thành phố du lịch biển đẹp nhất?")
    print(f"  A: {result['answer']}")
    print("  Top sources (reranked):")
    for i, s in enumerate(result.get("sources", [])[:3]):
        src = s if isinstance(s, str) else str(s)
        print(f"    {i + 1}. {src[:60]}...")


# =============================================================================
# Main
# =============================================================================


async def main():
    await example_1_keyword_rrf()
    await example_2_hybrid_rag()
    await example_3_rerank()


if __name__ == "__main__":
    asyncio.run(main())
