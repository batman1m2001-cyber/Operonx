# Hush Core

> Workflow engine cốt lõi cho Hush - async orchestration với built-in tracing.

## Cài đặt

```bash
# Qua meta-package (khuyến nghị)
uv pip install "hush-ai[core] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Hoặc editable (cho development)
git clone https://github.com/batman1m2001-cyber/Hush-ai.git && cd hush
uv pip install -e hush-core
```

Xem chi tiết tại [Cài đặt và Thiết lập](../hush-tutorial/docs/01-cai-dat-va-thiet-lap.md).

## Quick Start

```python
import asyncio
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT

async def main():
    with GraphNode(name="my-workflow") as graph:
        step1 = CodeNode(
            name="fetch",
            code_fn=lambda: {"data": [1, 2, 3, 4, 5]},
            outputs={"data": PARENT}
        )
        step2 = CodeNode(
            name="transform",
            code_fn=lambda data: {"result": sum(data)},
            inputs={"data": PARENT["data"]},
            outputs={"result": PARENT}
        )
        START >> step1 >> step2 >> END

    engine = Hush(graph)
    result = await engine.run()
    print(result["result"])  # 15

asyncio.run(main())
```

## Node Types

| Node | Mô tả |
|------|-------|
| `GraphNode` | Container chứa subgraph |
| `CodeNode` | Chạy Python function |
| `BranchNode` | Conditional routing |
| `ForLoopNode` | Sequential iteration |
| `MapNode` | Parallel iteration |
| `WhileLoopNode` | Loop với điều kiện |

## Flow Control

```python
# Sequential
START >> node1 >> node2 >> END

# Fork (parallel)
START >> node1 >> [node2a, node2b] >> node3 >> END

# Branch (conditional)
START >> branch_node >> {"case_a": node_a, "case_b": node_b} >> END
```

## State Management

```python
# Đọc từ parent
inputs={"data": PARENT["input_data"]}

# Ghi ra parent
outputs={"result": PARENT}

# Đọc từ node khác
inputs={"value": other_node["output_key"]}
```

## Local Tracing

```python
from hush.core.tracers import LocalTracer

tracer = LocalTracer()  # ~/.hush/traces.db
engine = Hush(graph, tracer=tracer)
await engine.run()

# Xem traces: python -m hush.core.ui.server
```

## Documentation

- [User Docs](../hush-tutorial/docs/) - Tutorials và guides
- [Architecture](../architecture/) - Internal documentation
  - [Engine](../architecture/engine/) - Execution internals
  - [State](../architecture/state/) - State management
  - [Nodes](../architecture/nodes/) - Node system

## Related Packages

- [hush-providers](../hush-providers/) - LLM, embedding, reranking
- [hush-observability](../hush-observability/) - Langfuse, OpenTelemetry
- [hush-vscode-traceview](../hush-vscode-traceview/) - VS Code extension

## License

MIT
