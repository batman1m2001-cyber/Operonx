# Embeddings và RAG

Sử dụng embedding, reranking cho RAG (Retrieval-Augmented Generation).

> **Ví dụ chạy được**: `examples/07_embeddings_and_rag.py`, `examples/14_rag_advanced.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Viết tắt | Class gốc | Ví dụ |
> |----------|-----------|-------|
> | `embedding_()` | `EmbeddingNode` | `embedding_(resource_key="bge-m3", texts=PARENT["texts"])` |
> | `rerank_()` | `RerankNode` | `rerank_(resource_key="bge-m3", query=PARENT["q"], documents=PARENT["docs"])` |
> | `llm_()` | `LLMNode` | `llm_(resource_key="gpt-4o", messages=PARENT["msgs"])` |
> | `prompt_()` | `PromptNode` | `prompt_(template={...}, var=PARENT["x"])` |

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

## embedding_()

```python
from hush.core import GraphNode, START, END, PARENT
from hush.providers import embedding_

with GraphNode(name="embed-workflow") as graph:
    embed = embedding_(
        resource_key="openai",
        texts=PARENT["documents"],
        outputs={"embeddings": PARENT["vectors"]},  # Rename output
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

## rerank_()

```python
from hush.providers import rerank_

rr = rerank_(
    resource_key="bge-m3",
    query=PARENT["query"],
    documents=PARENT["documents"],
    top_k=5,
)

# Output mapping via >> operator
rr["reranks"] >> PARENT["sources"]  # Map rerank output sang graph output
```

### RerankNode outputs

| Output | Type | Mô tả |
|--------|------|-------|
| `reranked_documents` / `reranks` | list | Documents sorted by relevance |
| `scores` | list | Relevance scores |
| `indices` | list | Original indices |

## RAG Pipeline: Embed → Retrieve → Rerank → Generate

```python
from hush.providers import embedding_, rerank_, prompt_, llm_

@code_node
def retrieve(query_vec, docs, doc_vecs):
    return {"retrieved": cosine_search(query_vec, doc_vecs, docs, top_k=20)}

with GraphNode(name="rag-pipeline") as graph:
    embed_query = embedding_(resource_key="openai", texts=PARENT["query"])
    ret = retrieve(
        query_vec=embed_query["embeddings"],
        docs=PARENT["documents"],
        doc_vecs=PARENT["doc_embeddings"],
        outputs={"context_docs": PARENT},
    )
    rr = rerank_(
        resource_key="bge-m3",
        query=PARENT["query"],
        documents=ret["retrieved"],
        top_k=5,
    )
    p = prompt_(
        template={"system": "Trả lời dựa trên context:\n\n{context}", "user": "{query}"},
        context=rr["reranks"],
        query=PARENT["query"],
    )
    llm = llm_(
        resource_key="gpt-4o",
        messages=p["messages"],
        outputs={"content": PARENT["answer"]},  # Rename output
    )

    rr["reranks"] >> PARENT["sources"]  # Map rerank results to graph output
    START >> embed_query >> ret >> rr >> p >> llm >> END

# result["answer"] = "...", result["sources"] = [...], result["context_docs"] = [...]
```

## Hybrid Search — Keyword + Vector + RRF

Kết hợp keyword search và vector search, merge bằng Reciprocal Rank Fusion.

```python
@code_node
def kw_search(query, docs):
    return {"results": keyword_search(query, docs, top_k=8)}

@code_node
def vec_search(qv, docs, dvs):
    return {"results": cosine_search(qv[0], dvs, docs, top_k=8)}

@code_node
def merge(kw, vec):
    return {"merged": reciprocal_rank_fusion([kw, vec])[:5]}

with GraphNode(name="hybrid-rag") as graph:
    kw = kw_search(query=PARENT["query"], docs=PARENT["documents"])
    embed_q = embedding_(resource_key="openai", texts=PARENT["query"])
    vs = vec_search(qv=embed_q["embeddings"], docs=PARENT["documents"], dvs=PARENT["doc_vectors"])
    m = merge(kw=kw["results"], vec=vs["results"])

    # Parallel: keyword + vector search
    START >> [kw, embed_q]
    embed_q >> vs
    [kw, vs] >> m >> END
```

Xem ví dụ đầy đủ tại `examples/14_rag_advanced.py`.

## Batch Embedding

```python
from hush.core.nodes import map_, Each

@code_node
def make_batches(docs):
    return {"batches": [docs[i:i+100] for i in range(0, len(docs), 100)]}

@code_node
def flatten(batches):
    return {"all_embeddings": [e for b in batches for e in b]}

with GraphNode(name="batch-embed") as graph:
    batch = make_batches(docs=PARENT["documents"])
    with map_(batch=Each(batch["batches"]), max_concurrency=5) as map_node:
        embed = embedding_(resource_key="openai", texts=PARENT["batch"])
        START >> embed >> END

    flat = flatten(batches=map_node["embeddings"])
    START >> batch >> map_node >> flat >> END
```

## Best Practices

1. **Retrieval top_k > Rerank top_k** — Retrieve 20, rerank to 5
2. **Chunk size**: 200-500 tokens với 10-20% overlap
3. **Cache embeddings** — Pre-compute cho knowledge base
4. **Batch embedding** — Dùng MapNode với max_concurrency cho throughput

## Tiếp theo

- [Error Handling](07-error-handling.md) — Xử lý lỗi
- [Parallel Execution](08-parallel-execution.md) — Chi tiết parallel patterns
