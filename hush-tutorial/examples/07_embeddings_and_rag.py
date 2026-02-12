"""Tutorial 07: Embeddings và RAG — Retrieval-Augmented Generation.

Cần: OPENAI_API_KEY trong .env + resources.yaml (embedding:openai, llm:gpt-4o-mini)

Học được:
- EmbeddingOp: chuyển text thành vector
- Cosine similarity search (in-memory)
- RAG pipeline: embed query → search → build context → LLM
- RerankOp (optional): xếp hạng lại kết quả

Chạy: cd hush-tutorial && uv run python examples/07_embeddings_and_rag.py
"""

import asyncio
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import op
from hush.providers import EmbeddingOp, LLMOp, PromptOp

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


async def example_1_basic_embedding():
    """EmbeddingOp — Chuyển text thành vectors."""
    print("=" * 50)
    print("Ví dụ 1: Basic Embedding")
    print("=" * 50)

    with GraphOp(name="embed-texts") as graph:
        embed = EmbeddingOp.of(
            resource="openai",  # Tham chiếu embedding:openai trong resources.yaml
            texts=PARENT["texts"],
            outputs={"embeddings": PARENT["vectors"]},
        )
        START >> embed >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={
            "texts": ["Xin chào!", "Hush workflow engine"],
        }
    )

    vectors = result["vectors"]
    print(f"  Số vectors: {len(vectors)}")
    print(f"  Dimensions: {len(vectors[0])}")
    print(f"  Vector 1 (5 đầu): {vectors[0][:5]}")


async def example_2_simple_rag():
    """RAG pipeline: embed query → cosine search → prompt → LLM."""
    print()
    print("=" * 50)
    print("Ví dụ 2: Simple RAG Pipeline")
    print("=" * 50)

    # Bước 0: Pre-compute document embeddings
    print("  Đang embed documents...")
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
    print(f"  Embedded {len(doc_vectors)} documents ({len(doc_vectors[0])} dims)")

    # RAG workflow
    @op
    def retrieve(query_vec, doc_vectors, documents):
        return {"context_docs": cosine_search(query_vec[0], doc_vectors, documents, top_k=3)}

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

    engine = Hush(graph)

    # Test queries
    queries = [
        "Thủ đô Việt Nam là gì?",
        "Thành phố nào nổi tiếng với bãi biển Mỹ Khê?",
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
        print(f"  Sources: {result['context_docs'][:2]}")  # Top 2 sources


async def example_3_rag_with_rerank():
    """RAG + RerankOp — Thêm bước reranking (optional)."""
    print()
    print("=" * 50)
    print("Ví dụ 3: RAG + Reranking (optional)")
    print("=" * 50)

    try:
        from hush.providers import RerankOp
    except ImportError:
        print("  Skipped — RerankOp chưa available")
        return

    import os

    if not os.environ.get("PINECONE_API_KEY"):
        print("  Skipped — PINECONE_API_KEY chưa set (cần cho reranking)")
        print("  Thêm reranking:bge-m3 vào resources.yaml để dùng")
        return

    with GraphOp(name="rag-rerank") as graph:
        # Rerank documents theo query
        rr = RerankOp.of(
            resource="bge-m3",  # reranking:bge-m3 trong resources.yaml
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

    engine = Hush(graph)
    result = await engine.run(
        inputs={
            "query": "Thành phố biển đẹp nhất Việt Nam?",
            "documents": DOCUMENTS,
        }
    )

    print(f"  Answer: {result['answer']}")
    print(f"  Top sources: {result['sources'][:2]}")


async def main():
    await example_1_basic_embedding()
    await example_2_simple_rag()
    await example_3_rag_with_rerank()


if __name__ == "__main__":
    asyncio.run(main())
