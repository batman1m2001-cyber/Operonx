# LLM Integration

Cấu hình và sử dụng LLM providers trong Hush workflows.

> **Ví dụ chạy được**: `examples/03_llm_chat.py`, `examples/04_llm_advanced.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Viết tắt | Class gốc | Ví dụ |
> |----------|-----------|-------|
> | `llmchain_()` | `LLMChainNode` | `llmchain_(resource_key="gpt-4o", template={...}, query=PARENT["q"])` |
> | `llm_()` | `LLMNode` | `llm_(resource_key="gpt-4o", messages=PARENT["msgs"])` |
> | `prompt_()` | `PromptNode` | `prompt_(template={...}, var=PARENT["x"])` |

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

## llmchain_() — Shorthand (khuyến nghị)

Cách ngắn nhất để gọi LLM. Kết hợp prompt + LLM trong một node, auto-naming từ biến, `>> END` auto-forward outputs.

```python
from hush.providers import llmchain_

# String template
summarize = llmchain_(resource_key="gpt-4o", template="Tóm tắt văn bản sau: {text}", text=PARENT["text"])

# Dict với system/user
chat = llmchain_(
    resource_key="gpt-4o",
    template={"system": "Bạn là assistant chuyên {task}.", "user": "{query}"},
    task="tóm tắt văn bản",
    query=PARENT["query"],
)

# Với conversation history
chat = llmchain_(
    resource_key="gpt-4o",
    template={"system": "Bạn là assistant hữu ích.", "user": "{query}"},
    conversation_history=PARENT["history"],
    query=PARENT["query"],
)

START >> chat >> END  # auto-forward: result["content"], result["model_used"], ...
```

### Structured output (JSON mode)

```python
classifier = llmchain_(
    resource_key="gpt-4o",
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

## llmchain_() — Config nâng cao

Khi cần config chi tiết hơn (load balancing, fallback, extract, v.v.):

```python
from hush.providers import llmchain_

chain = llmchain_(
    resource_key=["gpt-4o", "gpt-4o-mini"],
    template={"system": "Bạn là assistant hữu ích.", "user": "{query}"},
    ratios=[0.7, 0.3],
    fallback=["or-claude-4-sonnet"],
    query=PARENT["query"],
)
```

---

## prompt_() + llm_() — Dùng khi cần linh hoạt

Dùng pattern tách riêng khi cần:
- **Một prompt → nhiều LLMs** (so sánh models, ensemble)
- **Tool calling loops** (reinject tool results vào messages)
- **Pipeline phức tạp** (`@code_node` xen giữa prompt và LLM)
- **Multimodal prompts** (image, audio)

### prompt_() — Xây dựng Messages

Template hỗ trợ 3 định dạng: string, dict, hoặc list.

```python
from hush.providers import prompt_

# String → [{"role": "user", "content": "..."}]
p = prompt_(template="Tóm tắt văn bản sau: {text}", text=PARENT["text"])

# Dict → system + user messages
p = prompt_(
    template={"system": "Bạn là assistant chuyên {task}.", "user": "{query}"},
    task="tóm tắt văn bản",
    query=PARENT["query"],
)

# List → full messages array (multimodal)
p = prompt_(
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

### llm_() — Gọi LLM

```python
from hush.providers import llm_

llm = llm_(resource_key="gpt-4o", messages=p["messages"])
```

### Generation Parameters

```python
llm = llm_(
    resource_key="gpt-4o",
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
llm = llm_(
    resource_key="gpt-4o",
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
llm = llm_(
    resource_key=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],  # 30% gpt-4o, 70% gpt-4o-mini
    seed=42,             # Optional: reproducible selection
    messages=p["messages"],
)
```

Xem thêm ví dụ tại `examples/12_multi_model.py`.

## Fallback

Tự động chuyển model khi primary fails.

```python
llm = llm_(
    resource_key="gpt-4o",
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

### Sử dụng trong llm_()

```python
llm = llm_(
    resource_key="gpt-4o",
    messages=p["messages"],
    tools=tools,
    tool_choice="auto",
)
```

Xem ví dụ agent workflow đầy đủ tại `examples/11_agent_workflow.py`.

## Structured Output

Force LLM trả về JSON theo schema.

```python
llm = llm_(
    resource_key="gpt-4o",
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
state = result["$state"]

for node_name, metadata in state.trace_metadata.items():
    if "cost" in metadata:
        print(f"{node_name}: ${metadata['cost']:.6f}")
```

## Multi-turn Chat

```python
from hush.core import code_node
from hush.providers import prompt_, llm_

@code_node
def update_history(history: list, message: str, response: str):
    return {"new_history": history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": response}
    ]}

with GraphNode(name="multi-turn-chat") as graph:
    p = prompt_(
        template={"system": "Bạn là assistant hữu ích.", "user": "{message}"},
        conversation_history=PARENT["history"],
        message=PARENT["message"],
    )
    llm = llm_(
        resource_key="gpt-4o",
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
