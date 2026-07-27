"""12 RAG Advanced — keyword RRF (no API key) + hybrid (vector + keyword) RAG.

The `keyword_rrf` scenario is tier 1 (pure compute). The `hybrid` scenario
requires ``OPENAI_API_KEY`` and `embedding:openai` + `llm:gpt-4o-mini`
in ``resources.yaml``.

Run from this directory:

    uv sync
    cp .env.example .env  # only for hybrid
    uv run python main.py
"""

from __future__ import annotations

import asyncio

import numpy as np

import operonx
from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.providers import EmbeddingOp, LLMOp

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


def keyword_search(query: str, documents: list, top_k: int = 5) -> list:
    qt = set(query.lower().split())
    scored = []
    for doc in documents:
        overlap = len(qt & set(doc.lower().split()))
        if overlap > 0:
            scored.append((overlap, doc))
    scored.sort(reverse=True)
    return [doc for _, doc in scored[:top_k]]


def cosine_search(query_vec, doc_vecs, documents, top_k=5):
    qn = np.array(query_vec) / np.linalg.norm(query_vec)
    scored = []
    for i, dv in enumerate(doc_vecs):
        dn = np.array(dv) / np.linalg.norm(dv)
        scored.append((float(np.dot(qn, dn)), documents[i]))
    scored.sort(reverse=True)
    return [doc for _, doc in scored[:top_k]]


def reciprocal_rank_fusion(results_lists, k: int = 60):
    scores: dict = {}
    for results in results_lists:
        for rank, doc in enumerate(results):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank + 1)
    return [doc for doc, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]


@op
def search_original(query, docs):
    return {"results": keyword_search(query, docs, top_k=5)}


@op
def search_expanded(query, docs):
    return {"results": keyword_search(query + " thành phố du lịch", docs, top_k=5)}


@op
def rrf_merge(r1, r2):
    return {"merged": reciprocal_rank_fusion([r1, r2])[:5]}


@op
def kw_search_fn(query, docs):
    return {"results": keyword_search(query, docs, top_k=8)}


@op
def vec_search_fn(qv, docs, dvs):
    return {"results": cosine_search(qv[0], dvs, docs, top_k=8)}


@op
def merge_results(kw, vec):
    return {"context_docs": reciprocal_rank_fusion([kw, vec])[:5]}


@graph
def keyword_rrf(query, documents):
    """Two keyword searches in parallel → RRF merge."""
    s_orig = search_original(query=query, docs=documents)
    s_exp = search_expanded(query=query, docs=documents)
    m = rrf_merge(r1=s_orig["results"], r2=s_exp["results"])
    m["merged"] >> PARENT["results"]
    START >> [s_orig, s_exp] >> m >> END


@graph
def hybrid_rag(query, documents, doc_vectors):
    """Keyword + vector search → RRF → LLM."""
    kw = kw_search_fn(query=query, docs=documents)
    embed_q = EmbeddingOp.of(resource="openai", texts=query)
    vec = vec_search_fn(qv=embed_q["embeddings"], docs=documents, dvs=doc_vectors)
    mrg = merge_results(kw=kw["results"], vec=vec["results"])
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": "Trả lời câu hỏi dựa trên context.\nContext:\n{context}",
            "user": "{query}",
        },
        context=mrg["context_docs"],
        query=query,
    )
    mrg["context_docs"] >> PARENT["sources"]
    START >> [kw, embed_q]
    embed_q >> vec
    [kw, vec] >> mrg >> llm >> END


@graph
def precompute_embeddings(texts):
    """Helper graph used to pre-compute doc vectors before hybrid RAG."""
    e = EmbeddingOp.of(resource="openai", texts=texts)
    e["embeddings"] >> PARENT["vectors"]
    START >> e >> END


async def main() -> None:
    operonx.bootstrap()

    # 1. Pure-compute keyword RRF.
    g = keyword_rrf(query=PARENT["query"], documents=PARENT["documents"])
    result = await Operon(g).run(inputs={"query": "biển đẹp", "documents": DOCUMENTS})
    print(f"[keyword_rrf] {result.get('results', result)}")

    # 2. Hybrid RAG — needs API key + pre-computed doc vectors.
    try:
        precomp = precompute_embeddings(texts=PARENT["texts"])
        emb = await Operon(precomp).run(inputs={"texts": DOCUMENTS})
        doc_vectors = emb["vectors"]

        g = hybrid_rag(
            query=PARENT["query"],
            documents=PARENT["documents"],
            doc_vectors=PARENT["doc_vectors"],
        )
        result = await Operon(g).run(
            inputs={
                "query": "Thành phố nào có bãi biển Mỹ Khê?",
                "documents": DOCUMENTS,
                "doc_vectors": doc_vectors,
            }
        )
        content = {k: v for k, v in result.items() if k != "$state"}
        print(f"[hybrid] {content}")
    except Exception as e:
        print(f"[hybrid] skipped: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
