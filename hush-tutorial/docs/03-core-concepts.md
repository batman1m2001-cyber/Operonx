# Core Concepts

Hiểu các khái niệm cốt lõi của Hush: nodes, edges, data flow, và state management.

> **Ví dụ chạy được**: `examples/01_hello_world.py`, `examples/02_data_pipeline.py`

## GraphNode — Container

`GraphNode` là container chứa toàn bộ workflow. Tất cả nodes phải được tạo **bên trong** context của GraphNode.

```python
from hush.core import GraphNode

with GraphNode(name="my-workflow") as graph:
    # Tất cả nodes định nghĩa ở đây
    pass
```

**Lưu ý**: Nodes tạo bên ngoài `with GraphNode` sẽ không hoạt động.

## CodeNode — Chạy Python Function

`CodeNode` nhận inputs và trả về dict outputs.

```python
from hush.core import CodeNode, PARENT

def clean_text(text: str) -> dict:
    cleaned = " ".join(text.split())
    return {"cleaned_text": cleaned}

preprocess = CodeNode(
    name="preprocess",
    code_fn=clean_text,
    inputs={"text": PARENT["text"]},       # Lấy 'text' từ parent state
    outputs={"cleaned_text": PARENT}        # Ghi 'cleaned_text' lên parent state
)
```

### @code_node decorator

Cách viết ngắn gọn hơn:

```python
from hush.core.nodes.transform.code_node import code_node

@code_node
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

### inputs — Mapping data vào node

```python
inputs={
    "text": PARENT["text"],        # Lấy từ parent state
    "prefix": "Hello",             # Giá trị cố định
    "count": other_node["count"],  # Lấy từ output node khác
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

`PARENT` là tham chiếu đến state của GraphNode cha. Dùng để:
- **Đọc input**: `PARENT["key"]` lấy giá trị từ parent state
- **Ghi output**: `outputs={"key": PARENT}` ghi lên parent state

```python
with GraphNode(name="demo") as graph:
    node = CodeNode(
        name="node",
        code_fn=lambda x: {"doubled": x * 2},
        inputs={"x": PARENT["value"]},      # Đọc parent["value"]
        outputs={"doubled": PARENT}          # Ghi parent["doubled"]
    )
    START >> node >> END

engine = Hush(graph)
result = await engine.run(inputs={"value": 5})
print(result["doubled"])  # 10
```

## Edges — Kết nối Nodes

### >> operator (Hard Edge)

Node đích chờ **tất cả** predecessors hoàn thành.

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

Node đích chờ **bất kỳ một** soft predecessor hoàn thành. Dùng sau BranchNode khi chỉ 1 nhánh chạy.

```python
# Sau branch, dùng soft edge
[path_a, path_b] >> ~END
```

### Truyền output trực tiếp lên PARENT

```python
# Không cần node trung gian
merge["context_docs"] >> PARENT["sources"]
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

ResourceHub tự động load cấu hình từ `resources.yaml`. LLMNode, EmbeddingNode, RerankNode tham chiếu qua `resource_key`.

```yaml
# resources.yaml
llm:gpt-4o:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o
```

```python
llm = LLMNode(
    name="llm",
    resource_key="gpt-4o",  # Tham chiếu llm:gpt-4o
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
langfuse:default:    # category=langfuse, name=default
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
print(state.execution_order)  # Thứ tự thực thi nodes
print(state.user_id)          # User ID (nếu set)
```

## Tổng kết

| Concept | Mô tả |
|---------|-------|
| `GraphNode` | Container chứa workflow |
| `CodeNode` | Chạy Python function |
| `@code_node` | Decorator viết CodeNode ngắn gọn |
| `PARENT["key"]` | Đọc/ghi data từ parent state |
| `inputs` / `outputs` | Mapping data vào/ra nodes |
| `START >> node >> END` | Hard edge — chờ tất cả |
| `>> ~node` | Soft edge — chờ bất kỳ một |
| `ResourceHub` | Quản lý providers qua resources.yaml |
| `Hush(graph)` | Engine chạy workflow |

## Tiếp theo

- [LLM Integration](04-llm-integration.md) — Sử dụng LLM trong workflows
- [Loops & Branches](05-loops-branches.md) — Flow control
