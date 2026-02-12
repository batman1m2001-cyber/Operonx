# Hush Documentation

> Async workflow orchestration engine cho GenAI applications.

## Hush là gì?

**Hush** là workflow engine cho các ứng dụng AI/LLM, được thiết kế để xây dựng các pipeline phức tạp một cách đơn giản và hiệu quả. Hush tập trung vào việc điều phối (orchestration) và thực thi (execution) các node trong một graph.

```python
import asyncio
from hush.core import Hush, GraphNode, code_node, START, END, PARENT

@code_node
def greet(name: str):
    return {"message": f"Xin chào, {name}!"}

async def main():
    with GraphNode(name="hello") as graph:
        step = greet(name=PARENT["name"])
        START >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "Hush"})
    print(result["message"])  # Xin chào, Hush!

asyncio.run(main())
```

> Ví dụ trên chỉ dùng `hush-core`, không cần API key. Để dùng LLM, embedding, tracing — xem [Cài đặt và Thiết lập](01-cai-dat-va-thiet-lap.md).

## Kiến trúc 3 lớp

```
┌─────────────────────────────────────────────────────────┐
│                    hush-ops                    │
│         (LocalTracer, Langfuse, OpenTelemetry)           │
├─────────────────────────────────────────────────────────┤
│                     hush-providers                       │
│    (LLMNode, PromptNode, EmbeddingNode, RerankNode)      │
├─────────────────────────────────────────────────────────┤
│                       hush-core                          │
│  (GraphNode, CodeNode, BranchNode, State, ResourceHub)   │
└─────────────────────────────────────────────────────────┘
```

| Package | Mô tả |
|---------|-------|
| `hush-core` | **Nền tảng** — `@code_node`, `for_()`, `map_()`, `while_()`, `if_()` đủ cho gần như mọi workflow |
| `hush-providers` | Add-on — `llm_()`, `prompt_()`, `embedding_()`, `rerank_()` (cài khi cần) |
| `hush-ops` | Add-on — Tracing backends: Langfuse, OTEL (cài khi cần) |

> **Lưu ý:** Bảng kiến trúc bên trên hiển thị tên class gốc (CodeNode, LLMNode, ...). Trong code, hãy dùng **shorthand syntax** (`@code_node`, `llm_()`, ...) — xem [Shorthand Reference](12-shorthand-syntax.md).

## Cài đặt nhanh

```bash
# Với pip
pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"

# Với uv (nhanh hơn)
uv pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
```

Xem chi tiết tại [Cài đặt và Thiết lập](01-cai-dat-va-thiet-lap.md).

## Bắt đầu từ đây

| Bước | Nội dung | Link |
|------|----------|------|
| 1 | **Cài đặt + thiết lập `.env` & `resources.yaml`** | [Cài đặt](01-cai-dat-va-thiet-lap.md) |
| 2 | Hello World | [Quickstart](02-quickstart.md) |
| 3 | Core concepts | [Core Concepts](03-core-concepts.md) |

> **Quan trọng:** Bước 1 là bắt buộc. Mọi ví dụ dùng LLM, embedding, hoặc tracing đều cần `.env` (API keys) và `resources.yaml` (provider config) được thiết lập trước.

## Tutorials & Guides

Học Hush từ cơ bản đến nâng cao. Mỗi doc đều có **ví dụ chạy được** trong thư mục `examples/`.

### Cơ bản

| Doc | Ví dụ chạy được | Nội dung |
|-----|-----------------|----------|
| [Quickstart](02-quickstart.md) | `01_hello_world.py`, `02_data_pipeline.py` | Hello world, data pipeline |
| [Core Concepts](03-core-concepts.md) | `01_hello_world.py`, `02_data_pipeline.py` | GraphNode, CodeNode, inputs/outputs, PARENT, edges |

### LLM & AI

| Doc | Ví dụ chạy được | Nội dung |
|-----|-----------------|----------|
| [LLM Integration](04-llm-integration.md) | `03_llm_chat.py`, `04_llm_advanced.py` | PromptNode, LLMNode, providers, tools, structured output |
| [Embeddings & RAG](06-embeddings-rag.md) | `07_embeddings_and_rag.py`, `14_rag_advanced.py` | Embedding, reranking, RAG pipeline, hybrid search |
| [Multi-model](11-multi-model.md) | `12_multi_model.py` | Load balancing, fallback, ensemble, cost routing |
| [Agent Workflow](10-agent-workflow.md) | `11_agent_workflow.py` | Tool-calling agent, WhileLoopNode |

### Flow Control

| Doc | Ví dụ chạy được | Nội dung |
|-----|-----------------|----------|
| [Loops & Branches](05-loops-branches.md) | `05_loops_and_branches.py` | ForLoop, MapNode, WhileLoop, BranchNode |
| [Parallel Execution](08-parallel-execution.md) | `13_parallel_advanced.py` | Fan-out/fan-in, MapNode, partial failure |
| [Error Handling](07-error-handling.md) | `10_error_handling.py` | Error capture, retry, fallback, BranchNode routing |

### Production

| Doc | Ví dụ chạy được | Nội dung |
|-----|-----------------|----------|
| [Tracing & Observability](09-tracing-observability.md) | `06_tracing.py`, `08_langfuse_tracing.py`, `09_otel_tracing.py` | LocalTracer, Langfuse, OTEL, tags, cost tracking |

## Chạy ví dụ

```bash
cd hush-tutorial
uv run python examples/01_hello_world.py
```
