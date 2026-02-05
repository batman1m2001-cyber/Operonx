# LLM Integration

Cấu hình và sử dụng LLM providers trong Hush workflows.

> **Ví dụ chạy được**: `examples/03_llm_chat.py`, `examples/04_llm_advanced.py`

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

## PromptNode — Xây dựng Messages

### Cách 1: String prompt

```python
from hush.providers import PromptNode

prompt = PromptNode(
    name="prompt",
    inputs={
        "prompt": "Tóm tắt văn bản sau: {text}",
        "text": PARENT["text"]
    }
)
# Output: [{"role": "user", "content": "Tóm tắt văn bản sau: ..."}]
```

### Cách 2: Dict với system/user

```python
prompt = PromptNode(
    name="prompt",
    inputs={
        "prompt": {
            "system": "Bạn là assistant chuyên {task}.",
            "user": "{query}"
        },
        "task": "tóm tắt văn bản",
        "query": PARENT["query"]
    }
)
# Output: [
#   {"role": "system", "content": "Bạn là assistant chuyên tóm tắt văn bản."},
#   {"role": "user", "content": "..."}
# ]
```

### Cách 3: Full messages array (multimodal)

```python
prompt = PromptNode(
    name="prompt",
    inputs={
        "prompt": [
            {"role": "system", "content": "Bạn là assistant phân tích hình ảnh."},
            {"role": "user", "content": [
                {"type": "text", "text": "Mô tả hình ảnh: {query}"},
                {"type": "image_url", "image_url": {"url": "{image_url}"}}
            ]}
        ],
        "query": PARENT["query"],
        "image_url": PARENT["image_url"]
    }
)
```

### Với conversation history

```python
prompt = PromptNode(
    name="prompt",
    inputs={
        "prompt": {"system": "Bạn là assistant hữu ích.", "user": "{query}"},
        "conversation_history": PARENT["history"],  # List of previous messages
        "query": PARENT["query"]
    }
)
# History được insert trước user message cuối
```

## LLMNode — Gọi LLM

### Basic usage

```python
from hush.providers import LLMNode

llm = LLMNode(
    name="llm",
    resource_key="gpt-4o",  # Tham chiếu llm:gpt-4o trong resources.yaml
    inputs={"messages": prompt["messages"]},
    outputs={"content": PARENT["response"]}
)
```

### Shorthand: llm_()

```python
from hush.providers import llm_

# Shorthand — truyền inputs trực tiếp
llm = llm_(
    "gpt-4o",
    name="llm",
    messages=prompt["messages"],     # Input trực tiếp
    temperature=0.7,
    outputs={"content": PARENT["response"]}
)
```

Xem [Shorthand Syntax](12-shorthand-syntax.md) để biết thêm chi tiết.

### LLMNode outputs

| Output | Type | Mô tả |
|--------|------|-------|
| `role` | str | "assistant" |
| `content` | str | Response content |
| `model_used` | str | Model đã dùng |
| `tokens_used` | dict | `{prompt_tokens, completion_tokens, total_tokens}` |
| `tool_calls` | list | Tool calls nếu có |
| `finish_reason` | str | "stop", "tool_calls", etc. |

### Generation Parameters

```python
llm = LLMNode(
    name="llm",
    resource_key="gpt-4o",
    inputs={
        "messages": prompt["messages"],
        "temperature": 0.7,       # 0.0 = deterministic, 1.0 = creative
        "max_tokens": 1000,
        "top_p": 0.9,
        "frequency_penalty": 0.5,
        "presence_penalty": 0.5,
        "stop": ["\n\n", "END"],
        "seed": 42
    }
)
```

Hướng dẫn chọn temperature:
- `0.0`: Factual Q&A, code generation
- `0.3-0.5`: General conversation
- `0.7-1.0`: Creative writing

## LLMChainNode — All-in-one

Kết hợp PromptNode + LLMNode trong một node.

```python
from hush.providers import LLMChainNode

chain = LLMChainNode(
    name="chain",
    resource_key="gpt-4o",
    prompt={
        "system": "Bạn là assistant hữu ích.",
        "user": "{query}"
    },
    inputs={"query": PARENT["query"]},
    outputs={"content": PARENT["response"]}
)
```

> **Lưu ý**: Nếu template variables không interpolate đúng, dùng pattern PromptNode + LLMNode riêng biệt thay vì LLMChainNode.

## Streaming

```python
llm = LLMNode(
    name="llm",
    resource_key="gpt-4o",
    stream=True,  # Default
    inputs={"messages": prompt["messages"]}
)

# Subscribe to stream
from hush.core.streams import STREAM_SERVICE

async for chunk in STREAM_SERVICE.subscribe(request_id, channel_name):
    print(chunk.choices[0].delta.content, end="")
```

## Load Balancing

Phân tải requests giữa nhiều models theo tỷ lệ.

```python
llm = LLMNode(
    name="llm",
    resource_key=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],  # 30% gpt-4o, 70% gpt-4o-mini
    seed=42,            # Optional: reproducible selection
    inputs={"messages": prompt["messages"]}
)
```

### Shorthand

```python
llm = llm_(
    ["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],
    seed=42,
    name="balanced",
    messages=prompt["messages"]
)
```

Xem thêm ví dụ tại `examples/12_multi_model.py`.

## Fallback

Tự động chuyển model khi primary fails.

```python
llm = LLMNode(
    name="llm",
    resource_key="gpt-4o",
    fallback=["azure-gpt4", "gemini"],
    inputs={"messages": prompt["messages"]}
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

### Sử dụng trong LLMNode

```python
llm = LLMNode(
    name="llm",
    resource_key="gpt-4o",
    inputs={
        "messages": prompt["messages"],
        "tools": tools,
        "tool_choice": "auto"
    }
)
```

Xem ví dụ agent workflow đầy đủ tại `examples/11_agent_workflow.py`.

## Structured Output

Force LLM trả về JSON theo schema.

```python
llm = LLMNode(
    name="llm",
    resource_key="gpt-4o",
    inputs={
        "messages": prompt["messages"],
        "response_format": {
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
        }
    }
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
with GraphNode(name="multi-turn-chat") as graph:
    prompt = PromptNode(
        name="prompt",
        inputs={
            "prompt": {"system": "Bạn là assistant hữu ích.", "user": "{message}"},
            "conversation_history": PARENT["history"],
            "message": PARENT["message"]
        }
    )
    llm = LLMNode(
        name="llm",
        resource_key="gpt-4o",
        inputs={"messages": prompt["messages"], "temperature": 0.7, "max_tokens": 500},
        outputs={"content": PARENT["response"]}
    )
    update = CodeNode(
        name="update",
        code_fn=lambda history, message, response: {
            "new_history": history + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": response}
            ]
        },
        inputs={"history": PARENT["history"], "message": PARENT["message"], "response": PARENT["response"]},
        outputs={"new_history": PARENT}
    )
    START >> prompt >> llm >> update >> END

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
