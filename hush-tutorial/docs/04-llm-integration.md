# LLM Integration

Cấu hình và sử dụng LLM providers trong Hush workflows.

> **Ví dụ chạy được**: `examples/03_llm_chat.py`, `examples/04_llm_advanced.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `ChainOp.of()` | `ChainOp` | `ChainOp.of(resource="gpt-4o", template={...}, query=PARENT["q"])` |
> | `LLMOp.of()` | `LLMOp` | `LLMOp.of(resource="gpt-4o", messages=PARENT["msgs"])` |
> | `PromptOp.of()` | `PromptOp` | `PromptOp.of(template={...}, var=PARENT["x"])` |

## Cấu hình Providers trong resources.yaml

### OpenAI

```yaml
llm:gpt-4o:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o

llm:gpt-4o-mini:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini
```

### Azure OpenAI

```yaml
llm:azure-gpt4:
  api_type: azure
  api_key: ${AZURE_OPENAI_API_KEY}
  azure_endpoint: https://your-resource.openai.azure.com
  api_version: "2024-02-15-preview"
  model: gpt-4-deployment-name
```

### Google Gemini

```yaml
llm:gemini:
  _class: GeminiConfig
  project_id: your-gcp-project
  private_key: ${GEMINI_PRIVATE_KEY}
  client_email: your-service-account@project.iam.gserviceaccount.com
  location: us-central1
  model: gemini-2.0-flash-001
```

### vLLM / OpenAI-compatible

```yaml
llm:local-llama:
  api_type: openai
  api_key: "not-needed"
  base_url: http://localhost:8000/v1
  model: meta-llama/Llama-3.1-8B-Instruct
```

### OpenRouter (nhiều models)

```yaml
llm:or-claude-4-sonnet:
  api_type: openai
  api_key: ${OPENROUTER_API_KEY}
  base_url: https://openrouter.ai/api/v1
  model: anthropic/claude-sonnet-4
```

## ChainOp.of() — Classmethod (khuyến nghị)

Cách ngắn nhất để gọi LLM. Kết hợp prompt + LLM trong một op, auto-naming từ biến, `>> END` auto-forward outputs.

```python
from hush.providers import ChainOp

# String template
summarize = ChainOp.of(resource="gpt-4o", template="Tóm tắt văn bản sau: {text}", text=PARENT["text"])

# Dict với system/user
chat = ChainOp.of(
    resource="gpt-4o",
    template={"system": "Bạn là assistant chuyên {task}.", "user": "{query}"},
    task="tóm tắt văn bản",
    query=PARENT["query"],
)

# Với conversation history
chat = ChainOp.of(
    resource="gpt-4o",
    template={"system": "Bạn là assistant hữu ích.", "user": "{query}"},
    conversation_history=PARENT["history"],
    query=PARENT["query"],
)

START >> chat >> END  # auto-forward: result["content"], result["model_used"], ...
```

### Structured output (JSON mode)

```python
classifier = ChainOp.of(
    resource="gpt-4o",
    template={"user": "Phân loại và trả về JSON: {text}"},
    text=PARENT["text"],
    response_format={"type": "json_object"},
)
```

### LLM outputs

| Output | Type | Mô tả |
|--------|------|-------|
| `content` | str | Response content |
| `role` | str | "assistant" |
| `model_used` | str | Model đã dùng |
| `tokens_used` | dict | `{prompt_tokens, completion_tokens, total_tokens}` |
| `tool_calls` | list | Tool calls nếu có |
| `finish_reason` | str | "stop", "tool_calls", etc. |

## ChainOp.of() — Config nâng cao

Khi cần config chi tiết hơn (load balancing, fallback, extract, v.v.):

```python
from hush.providers import ChainOp

chain = ChainOp.of(
    resource=["gpt-4o", "gpt-4o-mini"],
    template={"system": "Bạn là assistant hữu ích.", "user": "{query}"},
    ratios=[0.7, 0.3],
    fallback=["or-claude-4-sonnet"],
    query=PARENT["query"],
)
```

---

## PromptOp.of() + LLMOp.of() — Dùng khi cần linh hoạt

Dùng pattern tách riêng khi cần:
- **Một prompt → nhiều LLMs** (so sánh models, ensemble)
- **Tool calling loops** (reinject tool results vào messages)
- **Pipeline phức tạp** (`@op` xen giữa prompt và LLM)
- **Multimodal prompts** (image, audio)

### PromptOp.of() — Xây dựng Messages

Template hỗ trợ 3 định dạng: string, dict, hoặc list.

```python
from hush.providers import PromptOp

# String → [{"role": "user", "content": "..."}]
p = PromptOp.of(template="Tóm tắt văn bản sau: {text}", text=PARENT["text"])

# Dict → system + user messages
p = PromptOp.of(
    template={"system": "Bạn là assistant chuyên {task}.", "user": "{query}"},
    task="tóm tắt văn bản",
    query=PARENT["query"],
)

# List → full messages array (multimodal)
p = PromptOp.of(
    template=[
        {"role": "system", "content": "Bạn là assistant phân tích hình ảnh."},
        {"role": "user", "content": [
            {"type": "text", "text": "Mô tả hình ảnh: {query}"},
            {"type": "image_url", "image_url": {"url": "{image_url}"}}
        ]}
    ],
    query=PARENT["query"],
    image_url=PARENT["image_url"],
)
```

### LLMOp.of() — Gọi LLM

```python
from hush.providers import LLMOp

llm = LLMOp.of(resource="gpt-4o", messages=p["messages"])
```

### Generation Parameters

```python
llm = LLMOp.of(
    resource="gpt-4o",
    messages=p["messages"],
    temperature=0.7,       # 0.0 = deterministic, 1.0 = creative
    max_tokens=1000,
    top_p=0.9,
    frequency_penalty=0.5,
    presence_penalty=0.5,
    stop=["\n\n", "END"],
    seed=42,
)
```

Hướng dẫn chọn temperature:
- `0.0`: Factual Q&A, code generation
- `0.3-0.5`: General conversation
- `0.7-1.0`: Creative writing

## Streaming

```python
llm = LLMOp.of(
    resource="gpt-4o",
    stream=True,  # Default
    messages=p["messages"],
)

# Subscribe to stream
from hush.core.streams import STREAM_SERVICE

async for chunk in STREAM_SERVICE.subscribe(request_id, channel_name):
    print(chunk.choices[0].delta.content, end="")
```

## Load Balancing

Phân tải requests giữa nhiều models theo tỷ lệ.

```python
llm = LLMOp.of(
    resource=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],  # 30% gpt-4o, 70% gpt-4o-mini
    seed=42,             # Optional: reproducible selection
    messages=p["messages"],
)
```

Xem thêm ví dụ tại `examples/12_multi_model.py`.

## Fallback

Tự động chuyển model khi primary fails.

```python
llm = LLMOp.of(
    resource="gpt-4o",
    fallback=["azure-gpt4", "gemini"],
    messages=p["messages"],
)
# Nếu gpt-4o fails → try azure-gpt4 → try gemini
```

## Tool Use / Function Calling

### Định nghĩa tools

```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Lấy thông tin thời tiết",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "Tên thành phố"}
                },
                "required": ["location"]
            }
        }
    }
]
```

### Sử dụng trong LLMOp.of()

```python
llm = LLMOp.of(
    resource="gpt-4o",
    messages=p["messages"],
    tools=tools,
    tool_choice="auto",
)
```

Xem ví dụ agent workflow đầy đủ tại `examples/11_agent_workflow.py`.

## Structured Output

Force LLM trả về JSON theo schema.

```python
llm = LLMOp.of(
    resource="gpt-4o",
    messages=p["messages"],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "sentiment_response",
            "schema": {
                "type": "object",
                "properties": {
                    "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                    "confidence": {"type": "number"}
                },
                "required": ["sentiment", "confidence"]
            }
        }
    },
)
```

## Cost Tracking

### Cấu hình

```yaml
llm:gpt-4o:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o
  cost_per_input_token: 0.000005    # $5 per 1M input tokens
  cost_per_output_token: 0.000015   # $15 per 1M output tokens
```

### Truy cập cost

```python
result = await engine.run(inputs={...}, tracer=tracer)
# Cost is tracked automatically in trace data sent to tracers
# View costs in hush-eyes web UI or Langfuse dashboard
```

## Multi-turn Chat

```python
from hush.core import op
from hush.providers import PromptOp, LLMOp

@op
def update_history(history: list, message: str, response: str):
    return {"new_history": history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": response}
    ]}

with GraphOp(name="multi-turn-chat") as graph:
    p = PromptOp.of(
        template={"system": "Bạn là assistant hữu ích.", "user": "{message}"},
        conversation_history=PARENT["history"],
        message=PARENT["message"],
    )
    llm = LLMOp.of(
        resource="gpt-4o",
        messages=p["messages"],
        temperature=0.7,
        max_tokens=500,
    )
    update = update_history(
        history=PARENT["history"],
        message=PARENT["message"],
        response=PARENT["response"],
    )
    START >> p >> llm >> update >> END

# Sử dụng
history = []
for msg in ["Xin chào!", "Tên tôi là An.", "Tôi tên gì?"]:
    result = await engine.run(inputs={"message": msg, "history": history})
    history = result["new_history"]
```

## Tiếp theo

- [Loops & Branches](05-loops-branches.md) — Flow control
- [Embeddings & RAG](06-embeddings-rag.md) — Vector search và reranking
- [Multi-model](11-multi-model.md) — Load balancing, ensemble, cost routing
