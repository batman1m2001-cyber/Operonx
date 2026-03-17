"""Shared workflow definitions for 12_rag_advanced.

Defines keyword search, hybrid search, and reranking ops + graph builders.
Example 1: no API key (keyword search + RRF demo).
Example 2: requires OPENAI_API_KEY (hybrid vector + keyword).
Example 3: requires OPENAI_API_KEY + PINECONE_API_KEY (reranking).
"""

import numpy as np
from hush.core import END, PARENT, START, GraphOp
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
    """RRF: merge kết quả từ nhiều sources bằng reciprocal rank scoring."""
    scores = {}
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


@op(rust="keyword_search")
def search_original(query, docs):
    """Keyword search — original query."""
    return {"results": keyword_search(query, docs, top_k=5)}


@op(rust="keyword_search_expanded")
def search_expanded(query, docs):
    """Keyword search — expanded query."""
    return {"results": keyword_search(query + " thành phố du lịch", docs, top_k=5)}


@op
def rrf_merge(r1, r2):
    """Merge two result lists with RRF."""
    return {"merged": reciprocal_rank_fusion([r1, r2])[:5]}


# =============================================================================
# Graph builders
# =============================================================================


def build_keyword_rrf():
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
