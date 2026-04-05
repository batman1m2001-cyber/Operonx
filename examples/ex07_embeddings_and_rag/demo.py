"""07 Embeddings & RAG — Run workflows with Hush Python engine.

Cần: OPENAI_API_KEY trong .env + resources.yaml (embedding:openai, llm:gpt-4o-mini)

Chạy: cd examples && uv run python ex07_embeddings_and_rag/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from hush.core import Hush

EXAMPLES_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = str(EXAMPLES_DIR / ".env")
RESOURCES_FILE = str(EXAMPLES_DIR.parent / "resources.yaml")

from ex07_embeddings_and_rag.workflow import (
    DOCUMENTS,
    build_basic_embedding,
    build_rag_with_rerank,
    build_simple_rag,
)


async def main():
    # =========================================================================
    # 1. Basic Embedding
    # =========================================================================
    print("=" * 50)
    print("Ví dụ 1: Basic Embedding")
    print("=" * 50)

    result = await Hush(build_basic_embedding(), env=ENV_FILE, resources=RESOURCES_FILE).run(
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
    embed_result = await Hush(build_basic_embedding(), env=ENV_FILE, resources=RESOURCES_FILE).run(inputs={"texts": DOCUMENTS})
    doc_vectors = embed_result["vectors"]
    print(f"  Embedded {len(doc_vectors)} documents ({len(doc_vectors[0])} dims)")

    engine = Hush(build_simple_rag(), env=ENV_FILE, resources=RESOURCES_FILE)
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

    result = await Hush(build_rag_with_rerank(), env=ENV_FILE, resources=RESOURCES_FILE).run(
        inputs={
            "query": "Thành phố biển đẹp nhất Việt Nam?",
            "documents": DOCUMENTS,
        }
    )
    print(f"  Answer: {result['answer']}")
    print(f"  Top sources: {result['sources'][:2]}")


if __name__ == "__main__":
    asyncio.run(main())
