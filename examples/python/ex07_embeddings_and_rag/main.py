"""07 Embeddings & RAG — embed, cosine search, optional rerank.

Requires ``OPENAI_API_KEY`` in ``.env`` and ``resources.yaml`` with:
- ``embedding:openai``    (used for basic-embed and simple-rag)
- ``llm:gpt-4o-mini``     (used for the RAG answer step)
- ``reranker:bge-m3``     (only the rerank scenario; skip if absent)

Run from this directory:

    uv sync
    cp .env.example .env  # fill in OPENAI_API_KEY
    uv run python main.py
"""

from __future__ import annotations

import asyncio

import numpy as np

import operonx
from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.providers import EmbeddingOp, LLMOp, RerankOp

DOCUMENTS = [
    "Hà Nội là thủ đô của Việt Nam, nằm ở miền Bắc, có hơn 1000 năm lịch sử.",
    "TP.HCM là thành phố lớn nhất Việt Nam về dân số, trung tâm kinh tế phía Nam.",
    "Đà Nẵng là thành phố lớn nhất miền Trung, nổi tiếng với bãi biển Mỹ Khê.",
    "Huế là cố đô của Việt Nam, nổi tiếng với Đại Nội và ẩm thực đặc sắc.",
    "Hạ Long là di sản thiên nhiên thế giới với hàng nghìn hòn đảo đá vôi.",
    "Sapa nằm ở Lào Cai, nổi tiếng với ruộng bậc thang và văn hóa dân tộc.",
    "Phú Quốc là đảo lớn nhất Việt Nam, thuộc tỉnh Kiên Giang, nổi tiếng du lịch biển.",
    "Nha Trang thuộc Khánh Hòa, được biết đến với bãi biển đẹp và du lịch nghỉ dưỡng.",
]


def cosine_search(query_vector, doc_vectors, documents, top_k=3):
    qn = np.array(query_vector) / np.linalg.norm(query_vector)
    scored = []
    for i, vec in enumerate(doc_vectors):
        dn = np.array(vec) / np.linalg.norm(vec)
        scored.append((float(np.dot(qn, dn)), documents[i]))
    scored.sort(reverse=True)
    return [doc for _, doc in scored[:top_k]]


@op
def retrieve(query_vec, doc_vectors, documents):
    return {"context_docs": cosine_search(query_vec[0], doc_vectors, documents, top_k=3)}


# ── Graphs ──────────────────────────────────────────────────────────────


@graph
def basic_embedding(texts):
    """Texts → embedding vectors."""
    embed = EmbeddingOp.of(resource="openai", texts=texts)
    embed["embeddings"] >> PARENT["vectors"]
    START >> embed >> END


@graph
def simple_rag(query, documents, doc_vectors):
    """Embed query → cosine search → LLM."""
    embed_query = EmbeddingOp.of(resource="openai", texts=query)
    ret = retrieve(
        query_vec=embed_query["embeddings"],
        doc_vectors=doc_vectors,
        documents=documents,
    )
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": (
                "Trả lời câu hỏi dựa trên context được cung cấp.\n"
                "Nếu không tìm thấy câu trả lời, nói 'Không tìm thấy thông tin.'\n\n"
                "Context:\n{context}"
            ),
            "user": "{query}",
        },
        context=ret["context_docs"],
        query=query,
    )
    START >> embed_query >> ret >> llm >> END


@graph
def rag_with_rerank(query, documents):
    """Reranker-driven RAG."""
    rr = RerankOp.of(
        resource="bge-m3",
        query=query,
        documents=documents,
        top_k=3,
    )
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": "Trả lời dựa trên context:\n\n{context}",
            "user": "{query}",
        },
        context=rr["reranks"],
        query=query,
    )
    START >> rr >> llm >> END


async def main() -> None:
    operonx.bootstrap()

    # 1. Basic embedding
    g = basic_embedding(texts=PARENT["texts"])
    embed_result = await Operon(g).run(inputs={"texts": ["Xin chào!", "Operon workflow engine"]})
    print(f"[embed] vectors={len(embed_result.get('vectors', []))} returned")

    # 2. Pre-compute doc embeddings, then run RAG.
    precomp = basic_embedding(texts=PARENT["texts"])
    doc_emb = await Operon(precomp).run(inputs={"texts": DOCUMENTS})
    doc_vectors = doc_emb["vectors"]

    g = simple_rag(
        query=PARENT["query"],
        documents=PARENT["documents"],
        doc_vectors=PARENT["doc_vectors"],
    )
    rag_result = await Operon(g).run(
        inputs={
            "query": "Thủ đô Việt Nam là gì?",
            "documents": DOCUMENTS,
            "doc_vectors": doc_vectors,
        }
    )
    print(f"[rag] {rag_result.get('content', rag_result)}")

    # 3. RAG + rerank (skip cleanly if reranker not configured).
    try:
        g = rag_with_rerank(query=PARENT["query"], documents=PARENT["documents"])
        rer_result = await Operon(g).run(
            inputs={
                "query": "Thành phố biển đẹp nhất Việt Nam?",
                "documents": DOCUMENTS,
            }
        )
        print(f"[rerank] {rer_result.get('content', rer_result)}")
    except Exception as e:
        print(f"[rerank] skipped: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())

# ── the served front door ───────────────────────────────────────────────
# Every operonx project serves. The [[serve]] block in operonx.toml names
# this graph, `operonx-serve` boots it, and the studio draws it as the
# entry node feeding the flow — no pipeline begins from nowhere.
#
# `ingress` yields one item per request payload and `egress` writes the
# reply back to the caller. Neither names a resource: the run was minted
# by a transport and already carries its session — and with no session the
# same graph still runs under a plain `engine.start()`, so serving costs
# the example nothing.
from operonx.core.serve import egress, ingress


@op
def answer(item=None) -> dict:
    """One request in, this example's reply out."""
    return {"reply": f"ex07 saw: {item!r}"}


@graph
def served():
    request = ingress()
    a = answer(item=request["item"])
    out = egress(item=a["reply"])
    START >> request >> a >> out >> END

