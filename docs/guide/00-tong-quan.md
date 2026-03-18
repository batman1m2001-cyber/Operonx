# Hush Documentation

> Async workflow orchestration engine cho GenAI applications.

## Hush là gì?

**Hush** là workflow engine cho các ứng dụng AI/LLM, được thiết kế để xây dựng các pipeline phức tạp một cách đơn giản và hiệu quả. Hush tập trung vào việc điều phối (orchestration) và thực thi (execution) các op trong một graph.

```python
import asyncio
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def greet(name: str):
    return {"message": f"Xin chào, {name}!"}

async def main():
    with GraphOp(name="hello") as graph:
        step = greet(name=PARENT["name"])
        START >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "Hush"})
    print(result["message"])  # Xin chào, Hush!

asyncio.run(main())
```

> Ví dụ trên chỉ dùng `hush-icore`, không cần API key. Để dùng LLM, embedding, tracing — xem [Cài đặt và Thiết lập](01-cai-dat-va-thiet-lap.md).

## Kiến trúc 3 lớp

```
┌─────────────────────────────────────────────────────────┐
│                    hush-telemetry                    │
│       (HushEyesTracer, Langfuse, OpenTelemetry)          │
├─────────────────────────────────────────────────────────┤
│                     hush-providers                       │
│    (LLMOp, PromptOp, EmbeddingOp, RerankOp)      │
├─────────────────────────────────────────────────────────┤
│                       hush-icore                         │
│  (GraphOp, FuncOp, BranchOp, State, ResourceHub)   │
└─────────────────────────────────────────────────────────┘
```

| Package | Mô tả |
|---------|-------|
| `hush-icore` | **Nền tảng** — `@op`, `@graph`, `@graph.loop()`, `if_()`, generator ops (yield) đủ cho gần như mọi workflow |
| `hush-providers` | Add-on — `LLMOp.of()`, `PromptOp.of()`, `EmbeddingOp.of()`, `RerankOp.of()` (cài khi cần) |
| `hush-telemetry` | Add-on — Tracing backends: Langfuse, OTEL (cài khi cần) |

> **Lưu ý:** Bảng kiến trúc bên trên hiển thị tên class gốc (FuncOp, LLMOp, ...). Trong code, hãy dùng **shorthand syntax** (`@op`, `LLMOp.of()`, ...) — xem [Shorthand Reference](12-shorthand-syntax.md).

## Cài đặt nhanh

```bash
# Với pip
pip install "hush-icore @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-icore"

# Với uv (nhanh hơn)
uv pip install "hush-icore @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-icore"
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
| [Quickstart](02-quickstart.md) | `exex01_hello_world/demo.py`, `exex02_data_pipeline/demo.py` | Hello world, data pipeline |
| [Core Concepts](03-core-concepts.md) | `exex01_hello_world/demo.py`, `exex02_data_pipeline/demo.py` | GraphOp, FuncOp, inputs/outputs, PARENT, edges |

### LLM & AI

| Doc | Ví dụ chạy được | Nội dung |
|-----|-----------------|----------|
| [LLM Integration](04-llm-integration.md) | `ex03_llm_chat/demo.py`, `ex04_llm_advanced/demo.py` | PromptOp, LLMOp, providers, tools, structured output |
| [Embeddings & RAG](06-embeddings-rag.md) | `ex07_embeddings_and_rag/demo.py`, `ex12_rag_advanced/demo.py` | Embedding, reranking, RAG pipeline, hybrid search |
| [Multi-model](11-multi-model.md) | `ex10_multi_model/demo.py` | Load balancing, fallback, ensemble, cost routing |
| [Agent Workflow](10-agent-workflow.md) | `ex09_agent_workflow/demo.py` | Tool-calling agent, @graph.loop |

### Flow Control

| Doc | Ví dụ chạy được | Nội dung |
|-----|-----------------|----------|
| [Loops & Branches](05-loops-branches.md) | `ex05_loops_and_branches/demo.py` | Generator ops (yield), @graph.loop, BranchOp |
| [Parallel Execution](08-parallel-execution.md) | `ex11_parallel_advanced/demo.py` | Fan-out/fan-in, generator iteration, partial failure |
| [Error Handling](07-error-handling.md) | `ex08_error_handling/demo.py` | Error capture, retry, fallback, BranchOp routing |

### Performance

| Doc | Ví dụ chạy được | Nội dung |
|-----|-----------------|----------|
| [Rust Mode & Plugin Ops](13-rust-mode-va-plugin.md) | All examples with `serve_rust.py` | Rust execution backend, cdylib plugin system |

### Production

| Doc | Ví dụ chạy được | Nội dung |
|-----|-----------------|----------|
| [Tracing & Observability](09-tracing-observability.md) | `ex06_tracing/demo.py` | HushEyesTracer, Langfuse, OTEL, tags, cost tracking |

## Chạy ví dụ

```bash
cd examples
uv run python exex01_hello_world/demo.py
```
