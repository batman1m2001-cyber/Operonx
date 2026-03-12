"""07 Embeddings & RAG — Run workflows with Hush Python engine.

Cần: OPENAI_API_KEY trong .env + resources.yaml (embedding:openai, llm:gpt-4o-mini)

Chạy: cd examples && uv run python 07_embeddings_and_rag/demo.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush
from workflow import DOCUMENTS, build_basic_embedding, build_rag_with_rerank, build_simple_rag


async def main():
    # =========================================================================
    # 1. Basic Embedding
    # =========================================================================
    print("=" * 50)
    print("Ví dụ 1: Basic Embedding")
    print("=" * 50)

    result = await Hush(build_basic_embedding()).run(
        inputs={"texts": ["Xin chào!", "Hush workflow engine"]}
    )
    vectors = result["vectors"]
    print(f"  Số vectors: {len(vectors)}")
    print(f"  Dimensions: {len(vectors[0])}")
    print(f"  Vector 1 (5 đầu): {vectors[0][:5]}")

    # =========================================================================
    # 2. Simple RAG Pipeline
    # =========================================================================
    print()
    print("=" * 50)
    print("Ví dụ 2: Simple RAG Pipeline")
    print("=" * 50)

    # Pre-compute document embeddings
    print("  Đang embed documents...")
    embed_result = await Hush(build_basic_embedding()).run(inputs={"texts": DOCUMENTS})
    doc_vectors = embed_result["vectors"]
    print(f"  Embedded {len(doc_vectors)} documents ({len(doc_vectors[0])} dims)")

    engine = Hush(build_simple_rag())
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
        print(f"  Sources: {result['context_docs'][:2]}")

    # =========================================================================
    # 3. RAG + Reranking (optional)
    # =========================================================================
    print()
    print("=" * 50)
    print("Ví dụ 3: RAG + Reranking (optional)")
    print("=" * 50)

    try:
        from hush.providers import RerankOp  # noqa: F401
    except ImportError:
        print("  Skipped — RerankOp chưa available")
        return

    if not os.environ.get("PINECONE_API_KEY"):
        print("  Skipped — PINECONE_API_KEY chưa set (cần cho reranking)")
        print("  Thêm reranking:bge-m3 vào resources.yaml để dùng")
        return

    result = await Hush(build_rag_with_rerank()).run(
        inputs={
            "query": "Thành phố biển đẹp nhất Việt Nam?",
            "documents": DOCUMENTS,
        }
    )
    print(f"  Answer: {result['answer']}")
    print(f"  Top sources: {result['sources'][:2]}")


if __name__ == "__main__":
    asyncio.run(main())
