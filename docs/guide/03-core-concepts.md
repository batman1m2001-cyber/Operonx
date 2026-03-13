# Core Concepts

Hiểu các khái niệm cốt lõi của Hush: ops, edges, data flow, và state management.

> **Ví dụ chạy được**: `examples/01_hello_world/demo.py`, `examples/02_data_pipeline/demo.py`

## GraphOp — Container

`GraphOp` là container chứa toàn bộ workflow. Tất cả ops phải được tạo **bên trong** context của GraphOp.

```python
from hush.core import GraphOp

with GraphOp(name="my-workflow") as graph:
    # Tất cả ops định nghĩa ở đây
    pass
```

**Lưu ý**: Ops tạo bên ngoài `with GraphOp` sẽ không hoạt động.

## FuncOp — Chạy Python Function

`FuncOp` nhận inputs và trả về dict outputs.

```python
from hush.core import FuncOp, PARENT

def clean_text(text: str) -> dict:
    cleaned = " ".join(text.split())
    return {"cleaned_text": cleaned}

preprocess = FuncOp(
    name="preprocess",
    code_fn=clean_text,
    inputs={"text": PARENT["text"]},       # Lấy 'text' từ parent state
    outputs={"cleaned_text": PARENT}        # Ghi 'cleaned_text' lên parent state
)
```

### @op decorator

Cách viết ngắn gọn hơn:

```python
from hush.core.ops.transform.func_op import op

@op
def clean_text(text: str):
    cleaned = " ".join(text.split())
    return {"cleaned_text": cleaned}

# Sử dụng như function call
preprocess = clean_text(
    name="preprocess",
    inputs={"text": PARENT["text"]},
    outputs={"cleaned_text": PARENT}
)
```

## Inputs và Outputs

### inputs — Mapping data vào op

```python
inputs={
    "text": PARENT["text"],        # Lấy từ parent state
    "prefix": "Hello",             # Giá trị cố định
    "count": other_op["count"],  # Lấy từ output op khác
}
```

### outputs — Mapping data ra parent

```python
# Cách 1: Ghi từng key
outputs={"cleaned_text": PARENT}            # parent["cleaned_text"] = result["cleaned_text"]

# Cách 2: Ghi vào key cụ thể
outputs={"content": PARENT["answer"]}       # parent["answer"] = result["content"]

# Cách 3: Ghi tất cả outputs
outputs={"*": PARENT}                       # parent[key] = result[key] cho mọi key
```

## PARENT — State của Parent Graph

`PARENT` là tham chiếu đến state của GraphOp cha. Dùng để:
- **Đọc input**: `PARENT["key"]` lấy giá trị từ parent state
- **Ghi output**: `outputs={"key": PARENT}` ghi lên parent state

```python
with GraphOp(name="demo") as graph:
    step = FuncOp(
        name="step",
        code_fn=lambda x: {"doubled": x * 2},
        inputs={"x": PARENT["value"]},      # Đọc parent["value"]
        outputs={"doubled": PARENT}          # Ghi parent["doubled"]
    )
    START >> step >> END

engine = Hush(graph)
result = await engine.run(inputs={"value": 5})
print(result["doubled"])  # 10
```

## Edges — Kết nối Ops

### >> operator (Hard Edge)

Op đích chờ **tất cả** predecessors hoàn thành.

```python
from hush.core import START, END

# Tuần tự
START >> node_a >> node_b >> node_c >> END

# Song song (fan-out)
START >> [node_a, node_b, node_c]

# Merge (fan-in) — chờ tất cả
[node_a, node_b, node_c] >> merge_node >> END
```

### ~ operator (Soft Edge)

Op đích chờ **bất kỳ một** soft predecessor hoàn thành. Dùng sau BranchOp khi chỉ 1 nhánh chạy.

```python
# Sau branch, dùng soft edge
[path_a, path_b] >> ~END
```

### Truyền output trực tiếp lên PARENT

```python
# Không cần op trung gian
merge["context_docs"] >> PARENT["sources"]
```

### Tự động forward outputs với >> END

Khi op kết nối trực tiếp đến END mà không có `outputs` định nghĩa sẵn, tất cả output keys sẽ tự động forward lên parent graph.

```python
with GraphOp(name="auto-forward") as graph:
    # Không cần định nghĩa outputs - tự động forward
    step = FuncOp(
        name="compute",
        code_fn=lambda: {"a": 1, "b": 2, "c": 3}
    )
    START >> step >> END

engine = Hush(graph)
result = await engine.run(inputs={})
# result["a"] == 1, result["b"] == 2, result["c"] == 3
```

**Lưu ý**: Nếu op đã có `outputs` định nghĩa sẵn, `>> END` sẽ không thay đổi outputs.

```python
# outputs đã định nghĩa - không bị thay đổi
step = FuncOp(
    name="custom",
    code_fn=lambda: {"result": 42},
    outputs={"result": PARENT["answer"]}  # Explicit mapping
)
START >> step >> END  # Giữ nguyên outputs={"result": PARENT["answer"]}
```

## Hush Engine — Chạy Workflow

```python
from hush.core import Hush

engine = Hush(graph)
result = await engine.run(
    inputs={"query": "Hello"},
    tracer=tracer,           # Optional: tracing
    user_id="user-123",      # Optional: correlation
    session_id="session-456" # Optional: correlation
)
```

## ResourceHub — Quản lý Providers

ResourceHub tự động load cấu hình từ `resources.yaml`. LLMOp, EmbeddingOp, RerankOp tham chiếu qua `resource`.

```yaml
# resources.yaml
llm:gpt-4o:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o
```

```python
llm = LLMOp(
    name="llm",
    resource="gpt-4o",  # Tham chiếu llm:gpt-4o
    inputs={"messages": prompt["messages"]}
)
```

### Thứ tự tìm resources.yaml

1. `HUSH_CONFIG` environment variable
2. `resources.yaml` trong thư mục hiện tại
3. `resources.yaml` trong thư mục cha (recursive)

### Format: `category:name`

```yaml
llm:gpt-4o:         # category=llm, name=gpt-4o
embedding:openai:    # category=embedding, name=openai
reranking:bge-m3:    # category=reranking, name=bge-m3
langfuse:hush:    # category=langfuse, name=default
otel:default:        # category=otel, name=default
```

### ${ENV_VAR} syntax

Giá trị `${VAR_NAME}` sẽ được thay bằng environment variable tương ứng.

## $state — Debug và Metadata

```python
result = await engine.run(inputs={...})
state = result["$state"]

# Thông tin debug
print(state.request_id)       # Unique request ID
print(list(state.iter_executed("op_name")))  # Thứ tự thực thi ops
print(state.user_id)          # User ID (nếu set)
```

## @graph — Modular Workflow

`@graph` biến một builder function thành factory tạo `GraphOp` có thể tái sử dụng. Các tham số của function tự động trở thành `PARENT` refs.

> **Ví dụ chạy được**: `examples/13_graph/demo.py`

```python
from hush.core import graph, op, START, END, PARENT, GraphOp, Hush

@op
def double(x: int):
    return {"result": x * 2}

@graph
def double_flow(val):
    step = double(x=val)      # val = PARENT["val"] (injected)
    START >> step >> END

# Sử dụng trong graph
with GraphOp(name="main") as main:
    d = double_flow(val=PARENT["input"])  # d.name == "d" (auto-named)
    START >> d >> END

engine = Hush(main)
result = await engine.run(inputs={"input": 5})
# result["result"] == 10
```

### Tái sử dụng

Cùng một graph có thể gọi nhiều lần (chain):

```python
with GraphOp(name="chain") as main:
    d1 = double_flow(val=PARENT["input"])
    d2 = double_flow(val=d1["result"])
    START >> d1 >> d2 >> END

# 3 * 2 * 2 = 12
```

### Output renaming

```python
@graph
def double_flow(val):
    step = double(x=val)
    step["result"] >> PARENT["doubled"]   # rename output
    START >> step >> END

with GraphOp(name="main") as main:
    d = double_flow(val=PARENT["input"])
    d["doubled"] >> PARENT["answer"]      # map to graph output
    START >> d >> END
```

### Khi nào dùng @graph?

| Trường hợp | Dùng gì |
|-------------|---------|
| Logic dùng lại nhiều nơi | `@graph` |
| Workflow chính | `with GraphOp(name="main") as graph:` |
| Chỉ chạy 1 function | `@op` |

## Executor — Thread Pool cho Sync Ops

Mặc định, sync ops chạy trực tiếp trên event loop (zero overhead). Nếu op thực hiện blocking I/O (HTTP requests, file I/O, ...), dùng `executor="thread"` để chạy trong thread pool:

```python
@op(executor="thread")
def fetch_data(url: str):
    """Chạy trong ThreadPoolExecutor — không block event loop."""
    import requests
    return {"body": requests.get(url).text}
```

Override tại call time:

```python
step = my_sync_op(x=PARENT["x"], executor="thread")
```

| `executor` | Hành vi |
|------------|---------|
| `None` (mặc định) | Chạy trực tiếp trên event loop |
| `"thread"` | Chạy trong `ThreadPoolExecutor` via `asyncio.to_thread()` |

**Lưu ý**: Async ops luôn chạy trên event loop bất kể `executor` setting.

## Tổng kết

| Concept | Mô tả |
|---------|-------|
| `GraphOp` | Container chứa workflow |
| `FuncOp` | Chạy Python function |
| `@op` | Decorator viết FuncOp ngắn gọn |
| `@graph` | Decorator tạo reusable workflow module |
| `PARENT["key"]` | Đọc/ghi data từ parent state |
| `inputs` / `outputs` | Mapping data vào/ra ops |
| `START >> step >> END` | Hard edge — chờ tất cả |
| `>> ~step` | Soft edge — chờ bất kỳ một |
| `ResourceHub` | Quản lý providers qua resources.yaml |
| `Hush(graph)` | Engine chạy workflow |
| `executor="thread"` | Thread pool cho blocking sync ops |

## Tiếp theo

- [LLM Integration](04-llm-integration.md) — Sử dụng LLM trong workflows
- [Loops & Branches](05-loops-branches.md) — Flow control
