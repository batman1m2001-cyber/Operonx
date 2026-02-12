# Workflow Ops (Provider Layer)

## Overview

Provider workflow ops là các op đặc biệt wrap LLM, embedding, và reranking providers để tích hợp vào workflow engine. Chúng kết nối ResourceHub (config) với BaseOp (execution) và cung cấp observability thông qua trace metadata.

Location: `hush-providers/hush/providers/ops/`

## Tổng quan 5 Ops

```
┌─────────────────────────────────────────────────────┐
│                   ChainOp (GraphOp)                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ PromptOp │→ │  LLMOp   │→ │ ParserOp │ (opt)    │
│  └──────────┘  └──────────┘  └──────────┘          │
└─────────────────────────────────────────────────────┘

┌──────────────┐  ┌──────────────┐
│ EmbeddingOp  │  │  RerankOp    │
│ texts→embeds │  │ query+docs→  │
│              │  │ reranked     │
└──────────────┘  └──────────────┘
```

| Op | Kế thừa | Mục đích | Input chính | Output chính |
|----|---------|---------|-------------|-------------|
| PromptOp | BaseOp | Format template thành messages | template, vars | messages |
| LLMOp | BaseOp | Gọi LLM provider | messages | content, tokens_used |
| ChainOp | GraphOp | Kết hợp Prompt→LLM→Parser | template, vars | content hoặc parsed fields |
| EmbeddingOp | BaseOp | Generate embeddings | texts | embeddings |
| RerankOp | BaseOp | Re-rank documents | query, documents | reranks |

## Pattern chung

### @shorthand và Op.of()

Tất cả provider ops sử dụng `@shorthand` decorator để tạo `Op.of()` factory method:

```python
# Shorthand (khuyên dùng)
chat = ChainOp.of(resource_key="gpt-4o", template={"user": "{q}"}, q=PARENT["q"])

# Tương đương với full constructor:
chat = ChainOp(
    resource_key="gpt-4o",
    inputs={"template": {"user": "{q}"}, "q": PARENT["q"]},
)
```

`split_shorthand_kwargs()` tách kwargs thành:
- **Input mappings**: các key là Ref hoặc giá trị → đi vào `inputs=`
- **Init kwargs**: `name=`, `outputs=`, `description=`, ... → đi vào constructor

### Param-based Schema

Mỗi op định nghĩa input/output schema bằng `Param()`:

```python
input_schema = {
    "messages": Param(type=list, required=True),
    "temperature": Param(type=float, default=0.0),
}
output_schema = {
    "content": Param(type=str, required=True),
    "tokens_used": Param(type=dict, default={}),
}
```

Schema tự động merge với user-provided mappings:
```python
self.inputs = self._merge_params(input_schema, normalized_inputs)
self.outputs = self._merge_params(output_schema, normalized_outputs)
```

### ResourceHub Integration

Tất cả ops resolve `resource_key` thành provider instance qua ResourceHub:

```python
try:
    hub = ResourceHub.instance()
except RuntimeError:
    hub = get_hub()

self._llm = hub.llm(self.resource_key)       # LLMOp
self.backend = hub.embedding(self.resource_key)  # EmbeddingOp
self.backend = hub.reranker(self.resource_key)   # RerankOp
```

### Trace Metadata

Mỗi op ghi metadata vào state để observability:

```python
state.record_trace_metadata(
    op_name=self.full_name,
    context_id=context_id,
    contain_generation=True,     # Có LLM generation?
    model=selected_resource_key, # Model đã dùng
    usage=tokens_used,           # Token counts
    cost=cost,                   # Chi phí (nếu có)
    metadata=self.metadata,      # Op-specific metadata
)
```

---

## PromptOp

Format template thành chat messages. Hỗ trợ 3 loại template:

### Template Formats

**1. String** → User message duy nhất:
```python
template = "Hello {name}"
# → [{"role": "user", "content": "Hello Alice"}]
```

**2. Dict (system/user)** → System + user messages:
```python
template = {"system": "You are {role}.", "user": "Help with: {task}"}
# → [{"role": "system", ...}, {"role": "user", ...}]
```

**3. List** → Full messages array (multimodal, complex):
```python
template = [
    {"role": "system", "content": "..."},
    {"role": "user", "content": [
        {"type": "text", "text": "{query}"},
        {"type": "image_url", "image_url": {"url": "{img}"}}
    ]}
]
```

### Reserved Keys

```python
RESERVED_KEYS = {"template", "conversation_history", "tool_results"}
```

- `template`: Template (str/dict/list)
- `conversation_history`: List messages chèn vào trước user message cuối
- `tool_results`: List messages thêm vào cuối

Tất cả key khác → template variables.

### Wildcard Variable Detection

Khi dùng `{"*": PARENT}`, PromptOp tự động phân tích template để tìm các biến cần thiết:

```python
# Với template = {"user": "Hello {name}, age {age}"}
# PromptOp tự động thêm "name" và "age" vào input schema
# Để wildcard forwarding có thể pick up chúng từ parent
```

### Error Handling

Khi thiếu template variable → `PromptError`:

```python
raise PromptError(
    message="Missing template variable(s)",
    template=template,
    missing_vars=["order_id"],
)
```

---

## LLMOp

Gọi LLM provider với đầy đủ tính năng: load balancing, streaming, batch, fallback.

### Input/Output Schema

**16 inputs:**
```
messages (required), temperature, max_tokens, tools, tool_choice,
response_format, top_p, stop, frequency_penalty, presence_penalty,
seed, logprobs, top_logprobs, n, user
```

**11 outputs:**
```
role, content, finish_reason, model_used, tokens_used,
tool_calls, thinking_content, context_used, refusal, logprobs,
error_code, error_message
```

### Load Balancing

Phân phối request giữa nhiều models:

```python
llm = LLMOp.of(
    resource_key=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.7, 0.3],  # 70% gpt-4o, 30% gpt-4o-mini
    messages=PARENT["messages"],
)
```

Thuật toán: `random.choices(weights=ratios)` sử dụng dedicated RNG instance (cách ly khỏi global random state). Seed có thể truyền qua `seed=` parameter.

### Streaming Mode

Khi `stream=True`, LLMOp tích hợp với `STREAM_SERVICE`:

```python
async for chunk in llm.stream(**params):
    response += chunk.choices[0].delta.content or ""
    # Push từng chunk đến STREAM_SERVICE
    asyncio.create_task(STREAM_SERVICE.push(request_id, channel, chunk))

asyncio.create_task(STREAM_SERVICE.end(request_id, channel))
```

Consumer (API/WebSocket) đọc từ `STREAM_SERVICE.get()`.

### Batch Mode

Sử dụng OpenAI Batch API (50% rẻ hơn):

```python
llm = LLMOp.of(resource_key="gpt-4o", batch_mode=True, messages=PARENT["msgs"])
```

Batch mode sử dụng `BatchCoordinator` từ `llms/batch_coordinator.py`.

### Fallback Chain

Khi primary model thất bại, thử fallback theo thứ tự:

```python
llm = LLMOp.of(
    resource_key="gpt-4o",
    fallback=["claude-3-sonnet", "gpt-4o-mini"],  # Thử lần lượt
    messages=PARENT["messages"],
)
```

Logic: primary fail → try fallback[0] → fail → try fallback[1] → ... → tất cả fail → ghi error vào state.

### Cost Tracking

Tự động tính chi phí nếu LLM config có `cost_per_input_token` / `cost_per_output_token`:

```python
cost = {
    "input": input_tokens * cost_per_input_token,
    "output": output_tokens * cost_per_output_token,
    "total": input_cost + output_cost,
}
```

### Custom run()

LLMOp override `run()` (không chỉ dùng `core`):
- Gọi `_select_llm()` cho load balancing
- Xử lý 3 mode: batch, stream, generate
- Fallback logic
- Ghi trace metadata (model, usage, cost)

---

## ChainOp

Composite op kế thừa `GraphOp`, tự động build internal graph:

### 2 Mode

**Text Generation** (không có `extract`):
```
PromptOp → LLMOp → Output
```

**Structured Output** (có `extract`):
```
PromptOp → LLMOp → ParserOp → Output
```

### Internal Graph Building

```python
def _build_graph(self):
    with self:
        _prompt = PromptOp(name="prompt", inputs={"*": PARENT})
        llm_inputs = {"messages": _prompt["messages"]}

        if self.extract:
            _llm = LLMOp(name="llm", resource_key=..., inputs=llm_inputs)
            _parser = ParserOp(name="parser", format=..., extract=...,
                              inputs={"text": _llm["content"]}, outputs={"*": PARENT})
            START >> _prompt >> _llm >> _parser >> END
        else:
            _llm = LLMOp(name="llm", resource_key=..., inputs=llm_inputs,
                         outputs={"*": PARENT})
            START >> _prompt >> _llm >> END

    self.build()
```

### Features

- `response_format`: JSON mode (`{"type": "json_object"}`)
- `enable_thinking`: Reasoning mode
- `ratios`, `fallback`: Pass-through đến LLMOp
- `parser`: Format cho ParserOp (mặc định "xml")

---

## EmbeddingOp

Wrapper đơn giản cho embedding provider:

```python
embed = EmbeddingOp.of(resource_key="bge-m3", texts=PARENT["texts"])
# Output: embed["embeddings"]  → List[List[float]]
```

- Input: `texts` (List[str])
- Output: `embeddings` (List[List[float]])
- Gọi `backend.run(texts)` từ ResourceHub
- Error: wrap trong `EmbeddingError`

## RerankOp

Wrapper cho reranking provider với xử lý linh hoạt:

```python
rerank = RerankOp.of(resource_key="bge-m3", query=PARENT["q"], documents=PARENT["docs"])
# Output: rerank["reranks"]  → List[Dict] with score
```

- Input: `query` (str), `documents` (List[str] hoặc List[Dict]), `top_k`, `threshold`
- Output: `reranks` (List[Dict] với `score` field)
- Tự động xử lý cả `List[str]` và `List[Dict]` (extract `content` field)
- Error: wrap trong `RerankError`

---

## Error Wrapping

Tất cả provider ops wrap exception thành OpError subclass:

| Op | Exception | Context |
|----|-----------|---------|
| PromptOp | PromptError | template_type, template, missing_vars |
| LLMOp | (ghi error vào state) | error_code, error_message |
| EmbeddingOp | EmbeddingError | resource_key, text_count |
| RerankOp | RerankError | resource_key, query, document_count |

## Xem thêm

- [LLM Abstraction](llm-abstraction.md) - BaseLLM interface
- [Embedding Provider](embedding-provider.md) - BaseEmbedder interface
- [Reranker Provider](reranker-provider.md) - BaseReranker interface
- [Streaming System](../streams/streaming-system.md) - STREAM_SERVICE
- [Exception Hierarchy](../ops/exception-hierarchy.md) - Error types
- [ParserOp](../ops/parser-op.md) - Parser trong ChainOp
- [BaseOp Anatomy](../ops/base-op.md) - Param system
