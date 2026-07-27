# RAG

Retrieval-augmented generation in Operonx is three ops chained together:
embed the query, fetch + rerank candidates, then call an LLM with the
retrieved context.

## Pre-requisites

```bash
pip install "operonx[standard]"
```

Configure embeddings and a reranker in `resources.yaml`:

```yaml
embeddings:
  bge-m3:
    backend: openai
    model: text-embedding-3-large
    api_key: ${OPENAI_API_KEY}

rerankers:
  cohere-rerank:
    backend: cohere
    model: rerank-english-v3.0
    api_key: ${COHERE_API_KEY}

llms:
  gpt-4o:
    backend: openai
    model: gpt-4o
    api_key: ${OPENAI_API_KEY}
```

## Pipeline

```python
import asyncio
import operonx
from operonx.core import Operon, GraphOp, op, START, END, PARENT
from operonx.providers import EmbeddingOp, LLMOp, RerankOp

@op
async def fetch_candidates(query_vec: list[float]):
    # Replace with your vector DB call.
    docs = await my_vector_db.search(query_vec, k=20)
    return {"docs": docs}

async def main():
    operonx.bootstrap()

    with GraphOp(name="rag") as graph:
        embed = EmbeddingOp.of(resource="bge-m3", texts=[PARENT["question"]])
        fetch = fetch_candidates(query_vec=embed["embeddings"][0])
        rerank = RerankOp.of(
            resource="cohere-rerank",
            query=PARENT["question"],
            documents=fetch["docs"],
            top_k=5,
        )
        answer = LLMOp.of(
            resource="gpt-4o",
            prompt={
                "system": "Answer using only the provided context.",
                "user": "Question: {question}\n\nContext:\n{context}",
            },
            question=PARENT["question"],
            context=rerank["documents"],
        )
        START >> embed >> fetch >> rerank >> answer >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"question": "What is Operonx?"})
    print(result["content"])

asyncio.run(main())
```

## Notes

- `EmbeddingOp.of` uses keyword args — never positional.
- `texts` is a list, even for a single query; `embed["embeddings"]` is
  parallel-shaped.
- `fetch_candidates` is your responsibility — Operonx does not bundle a
  vector DB. Wire any async client.
- For local ONNX embeddings, install `operonx[onnx]` and configure
  `backend: onnx` in `resources.yaml`.

## Where to go next

- Agent loops over retrieval: [Agents](05-agents.md).
- Trace each op for debugging: [Tracing](07-tracing.md).
