# Embeddings và RAG

Sử dụng embedding, reranking cho RAG (Retrieval-Augmented Generation).

> **Ví dụ chạy được**: `examples/07_embeddings_and_rag.py`, `examples/14_rag_advanced.py`

## Embedding Providers

| Provider | Type | Đặc điểm |
|----------|------|----------|
| OpenAI | API-based | Đơn giản, chất lượng tốt |
| Azure OpenAI | API-based | Enterprise, compliance |
| vLLM | Self-hosted | OpenAI-compatible API |
| TEI | Self-hosted | HuggingFace optimized |
| HuggingFace | Local | Chạy local, không cần API |
| ONNX | Local | Fast inference với ONNX Runtime |

## Cấu hình Embedding

### OpenAI

```yaml
embedding:openai:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1/embeddings
  model: text-embedding-3-small
  dimensions: 1536
```

### vLLM / TEI (Self-hosted)

```yaml
embedding:local:
  api_type: vllm
  base_url: http://localhost:8080/v1
  model: BAAI/bge-m3
  dimensions: 1024
  embed_batch_size: 32
```

### ONNX (Local)

```yaml
embedding:bge-m3-onnx:
  api_type: onnx
  model: ${BGE_M3_EMBEDDING_PATH}
  dimensions: 1024
```

## EmbeddingNode

```python
from hush.core import GraphNode, START, END, PARENT
from hush.providers import EmbeddingNode

with GraphNode(name="embed-workflow") as graph:
    embed = EmbeddingNode(
        name="embed",
        resource_key="openai",
        inputs={"texts": PARENT["documents"]},
        outputs={"embeddings": PARENT["vectors"]}
    )
    START >> embed >> END

# Input: {"documents": ["Hello world", "Goodbye world"]}
# Output: {"vectors": [[0.1, 0.2, ...], [0.3, 0.4, ...]]}
```

### EmbeddingNode outputs

| Output | Type | Mô tả |
|--------|------|-------|
| `embeddings` | list | Vector hoặc list of vectors |
| `model_used` | str | Model đã sử dụng |
| `dimensions` | int | Số dimensions |
| `tokens_used` | int | Tổng số tokens |

### Chọn model phù hợp

| Use Case | Model |
|----------|-------|
| General English | `text-embedding-3-small` |
| Multilingual | `BAAI/bge-m3` |
| Code | `text-embedding-3-large` |
| Fast/cheap | `BAAI/bge-small-en-v1.5` |

## Reranking Providers

| Provider | Type | Đặc điểm |
|----------|------|----------|
| Pinecone | API-based | Tích hợp vector DB |
| Cohere | API-based | Chất lượng cao |
| vLLM | Self-hosted | Cross-encoder models |
| ONNX | Local | Fast inference |

## Cấu hình Reranking

```yaml
reranking:bge-m3:
  api_type: pinecone
  api_key: ${PINECONE_API_KEY}
  model: bge-reranker-v2-m3
  base_url: https://api.pinecone.io/rerank
```

## RerankNode

```python
from hush.providers import RerankNode

rerank = RerankNode(
    name="rerank",
    resource_key="bge-m3",
    inputs={
        "query": PARENT["query"],
        "documents": PARENT["documents"],
        "top_k": 5
    }
)
```

### RerankNode outputs

| Output | Type | Mô tả |
|--------|------|-------|
| `reranked_documents` / `reranks` | list | Documents sorted by relevance |
| `scores` | list | Relevance scores |
| `indices` | list | Original indices |

## RAG Pipeline: Embed → Retrieve → Rerank → Generate

```python
with GraphNode(name="rag-pipeline") as graph:
    embed_query = EmbeddingNode(
        name="embed_query",
        resource_key="openai",
        inputs={"texts": PARENT["query"]}
    )
    retrieve = CodeNode(
        name="retrieve",
        code_fn=lambda query_vec, docs, doc_vecs: {
            "retrieved": cosine_search(query_vec, doc_vecs, docs, top_k=20)
        },
        inputs={
            "query_vec": embed_query["embeddings"],
            "docs": PARENT["documents"],
            "doc_vecs": PARENT["doc_embeddings"]
        }
    )
    rerank = RerankNode(
        name="rerank",
        resource_key="bge-m3",
        inputs={
            "query": PARENT["query"],
            "documents": retrieve["retrieved"],
            "top_k": 5
        }
    )
    prompt = PromptNode(
        name="prompt",
        inputs={
            "prompt": {"system": "Trả lời dựa trên context:\n\n{context}", "user": "{query}"},
            "context": rerank["reranked_documents"],
            "query": PARENT["query"]
        }
    )
    llm = LLMNode(
        name="llm",
        resource_key="gpt-4o",
        inputs={"messages": prompt["messages"]},
        outputs={"content": PARENT["answer"]}
    )
    START >> embed_query >> retrieve >> rerank >> prompt >> llm >> END
```

## Hybrid Search — Keyword + Vector + RRF

Kết hợp keyword search và vector search, merge bằng Reciprocal Rank Fusion.

```python
with GraphNode(name="hybrid-rag") as graph:
    # Song song: keyword + vector
    kw_search = CodeNode(
        name="keyword_search",
        code_fn=lambda query, docs: {"results": keyword_search(query, docs, top_k=8)},
        inputs={"query": PARENT["query"], "docs": PARENT["documents"]}
    )
    embed_q = EmbeddingNode(
        name="embed_query",
        resource_key="openai",
        inputs={"texts": PARENT["query"]}
    )
    vec_search = CodeNode(
        name="vector_search",
        code_fn=lambda qv, docs, dvs: {"results": cosine_search(qv[0], dvs, docs, top_k=8)},
        inputs={"qv": embed_q["embeddings"], "docs": PARENT["documents"], "dvs": PARENT["doc_vectors"]}
    )
    merge = CodeNode(
        name="merge",
        code_fn=lambda kw, vec: {"merged": reciprocal_rank_fusion([kw, vec])[:5]},
        inputs={"kw": kw_search["results"], "vec": vec_search["results"]}
    )

    # Parallel: keyword + vector search
    START >> [kw_search, embed_q]
    embed_q >> vec_search
    [kw_search, vec_search] >> merge >> END
```

Xem ví dụ đầy đủ tại `examples/14_rag_advanced.py`.

## Batch Embedding

```python
from hush.core.nodes.iteration import MapNode
from hush.core.nodes.iteration.base import Each

with GraphNode(name="batch-embed") as graph:
    batch = CodeNode(
        name="batch",
        code_fn=lambda docs: {
            "batches": [docs[i:i+100] for i in range(0, len(docs), 100)]
        },
        inputs={"docs": PARENT["documents"]}
    )
    with MapNode(
        name="embed_batches",
        inputs={"batch": Each(batch["batches"])},
        max_concurrency=5
    ) as map_node:
        embed = EmbeddingNode(
            name="embed",
            resource_key="openai",
            inputs={"texts": PARENT["batch"]},
            outputs={"embeddings": PARENT}
        )
        START >> embed >> END

    flatten = CodeNode(
        name="flatten",
        code_fn=lambda batches: {"all_embeddings": [e for b in batches for e in b]},
        inputs={"batches": map_node["embeddings"]},
        outputs={"*": PARENT}
    )
    START >> batch >> map_node >> flatten >> END
```

## Best Practices

1. **Retrieval top_k > Rerank top_k** — Retrieve 20, rerank to 5
2. **Chunk size**: 200-500 tokens với 10-20% overlap
3. **Cache embeddings** — Pre-compute cho knowledge base
4. **Batch embedding** — Dùng MapNode với max_concurrency cho throughput

## Tiếp theo

- [Error Handling](07-error-handling.md) — Xử lý lỗi
- [Parallel Execution](08-parallel-execution.md) — Chi tiết parallel patterns
