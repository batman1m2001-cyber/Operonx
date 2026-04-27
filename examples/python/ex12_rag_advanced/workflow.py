"""Shared workflow definitions for ex12_rag_advanced.

Keyword RRF (no API key) + hybrid (vector + keyword) RAG.
"""

import numpy as np
from operonx.core import END, PARENT, START, GraphOp, op

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
    """RRF: merge kết quả từ nhiều sources bằng reciprocal rank scoring."""
    scores: dict = {}
    for results in results_lists:
        for rank, doc in enumerate(results):
            if doc not in scores:
                scores[doc] = 0.0
            scores[doc] += 1.0 / (k + rank + 1)
    sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in sorted_docs]


# =============================================================================
# Ops
# =============================================================================


@op
def search_original(query, docs):
    """Keyword search — original query."""
    return {"results": keyword_search(query, docs, top_k=5)}


@op
def search_expanded(query, docs):
    """Keyword search — expanded query."""
    return {"results": keyword_search(query + " thành phố du lịch", docs, top_k=5)}


@op
def rrf_merge(r1, r2):
    """Merge two result lists with RRF."""
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


# =============================================================================
# Graph builders
# =============================================================================


def build_keyword_rrf() -> GraphOp:
    """Graph: 2 keyword searches parallel → RRF merge."""
    with GraphOp(name="keyword-rrf") as g:
        s_orig = search_original(query=PARENT["query"], docs=PARENT["documents"])
        s_exp = search_expanded(query=PARENT["query"], docs=PARENT["documents"])
        m = rrf_merge(
            r1=s_orig["results"],
            r2=s_exp["results"],
            outputs={"merged": PARENT["results"]},
        )
        START >> [s_orig, s_exp] >> m >> END
    return g


def build_hybrid_rag() -> GraphOp:
    """Graph: keyword + vector search parallel → RRF merge → prompt → LLM.

    Requires ``OPENAI_API_KEY`` (embedding + LLM) and pre-computed
    ``doc_vectors`` in the inputs.
    """
    from operonx.providers import EmbeddingOp, LLMOp, PromptOp

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
            resource="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["answer"]},
        )
        mrg["context_docs"] >> PARENT["sources"]
        START >> [kw, embed_q]
        embed_q >> vec
        [kw, vec] >> mrg >> p >> llm >> END
    return graph
