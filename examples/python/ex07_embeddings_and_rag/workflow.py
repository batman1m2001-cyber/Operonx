"""Shared workflow definitions for ex07_embeddings_and_rag.

Embeddings + RAG (cosine search + optional rerank).

Cần: OPENAI_API_KEY trong .env + resources.yaml (embedding:openai, llm:gpt-4o-mini)
"""

import numpy as np
from operonx.core import END, PARENT, START, GraphOp, op
from operonx.providers import EmbeddingOp, LLMOp, PromptOp

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
]

# =============================================================================
# Helper: Cosine similarity search
# =============================================================================


def cosine_search(query_vector, doc_vectors, documents, top_k=3):
    """Tìm documents gần nhất bằng cosine similarity."""
    query_norm = np.array(query_vector) / np.linalg.norm(query_vector)
    scores = []
    for i, doc_vec in enumerate(doc_vectors):
        doc_norm = np.array(doc_vec) / np.linalg.norm(doc_vec)
        sim = float(np.dot(query_norm, doc_norm))
        scores.append((sim, documents[i]))
    scores.sort(reverse=True)
    return [doc for _, doc in scores[:top_k]]


# =============================================================================
# Ops
# =============================================================================


@op
def retrieve(query_vec, doc_vectors, documents):
    return {"context_docs": cosine_search(query_vec[0], doc_vectors, documents, top_k=3)}


# =============================================================================
# Graph builders
# =============================================================================


def build_basic_embedding() -> GraphOp:
    """EmbeddingOp — Chuyển text thành vectors."""
    with GraphOp(name="embed-texts") as graph:
        embed = EmbeddingOp.of(
            resource="openai",
            texts=PARENT["texts"],
            outputs={"embeddings": PARENT["vectors"]},
        )
        START >> embed >> END
    return graph


def build_simple_rag() -> GraphOp:
    """RAG pipeline: embed query → cosine search → prompt → LLM."""
    with GraphOp(name="simple-rag") as graph:
        # Step 1: Embed query
        embed_query = EmbeddingOp.of(
            resource="openai",
            texts=PARENT["query"],
        )

        # Step 2: Cosine similarity search
        ret = retrieve(
            query_vec=embed_query["embeddings"],
            doc_vectors=PARENT["doc_vectors"],
            documents=PARENT["documents"],
            outputs={"context_docs": PARENT},
        )

        # Step 3: Build prompt with context
        p = PromptOp.of(
            template={
                "system": (
                    "Trả lời câu hỏi dựa trên context được cung cấp.\n"
                    "Nếu không tìm thấy câu trả lời, nói 'Không tìm thấy thông tin.'\n\n"
                    "Context:\n{context}"
                ),
                "user": "{query}",
            },
            context=PARENT["context_docs"],
            query=PARENT["query"],
        )

        # Step 4: Generate answer
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["answer"]},
        )

        START >> embed_query >> ret >> p >> llm >> END
    return graph


def build_rag_with_rerank() -> GraphOp:
    """RAG + RerankOp — Thêm bước reranking."""
    from operonx.providers import RerankOp

    with GraphOp(name="rag-rerank") as graph:
        rr = RerankOp.of(
            resource="bge-m3",
            query=PARENT["query"],
            documents=PARENT["documents"],
            top_k=3,
        )

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
        START >> rr >> p >> llm >> END
    return graph
