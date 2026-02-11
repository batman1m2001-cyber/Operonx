# Agent Workflow

Xây dựng AI agent với tool calling và WhileLoopNode.

> **Ví dụ chạy được**: `examples/11_agent_workflow.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Viết tắt | Class gốc | Ví dụ |
> |----------|-----------|-------|
> | `@code_node` | `CodeNode` | `@code_node` decorator trên function |
> | `while_()` | `WhileLoopNode` | `while_(counter=0, stop_condition="counter >= 5")` |
> | `llm_()` | `LLMNode` | `llm_(resource_key="gpt-4o", messages=PARENT["msgs"])` |

## Kiến trúc Agent

```
Init → while_(not done):
         → llm_() → Check tool_calls
           → Nếu có: Execute tools → Update messages → Loop
           → Nếu không: Done → Exit
```

## Tool-calling Agent

### Bước 1: Định nghĩa tools

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

### Bước 2: Implement tool execution

```python
import json

def execute_tools(tool_calls, messages):
    """Thực thi tool calls và append kết quả vào messages."""
    new_messages = messages + [{"role": "assistant", "tool_calls": tool_calls}]

    for tc in tool_calls:
        fn_name = tc["function"]["name"]
        args = json.loads(tc["function"]["arguments"])

        if fn_name == "get_weather":
            result = f"Thời tiết tại {args['location']}: 25°C, nắng"
        else:
            result = f"Unknown tool: {fn_name}"

        new_messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result
        })

    return new_messages
```

### Bước 3: Agent workflow với while_()

```python
from hush.core import Hush, GraphNode, code_node, START, END, PARENT
from hush.core.nodes import while_
from hush.providers import llm_

@code_node
def init_agent(query: str):
    return {
        "messages": [
            {"role": "system", "content": "Bạn là assistant có thể tra cứu thời tiết."},
            {"role": "user", "content": query}
        ],
        "iteration": 0,
        "done": False,
        "final_answer": ""
    }

@code_node
def process(tool_calls, content, messages, iteration):
    return process_response(tool_calls, content, messages, iteration)

with GraphNode(name="agent") as graph:
    init = init_agent(query=PARENT["query"])

    # Agent loop
    with while_(
        done=PARENT["done"],
        messages=PARENT["messages"],
        iteration=PARENT["iteration"],
        stop_condition="done == True",
        max_iterations=5,
    ) as loop:
        llm = llm_(
            resource_key="gpt-4o",
            messages=PARENT["messages"],
            tools=tools,
            tool_choice="auto",
        )
        proc = process(
            tool_calls=llm["tool_calls"],
            content=llm["content"],
            messages=PARENT["messages"],
            iteration=PARENT["iteration"],
        )
        START >> llm >> proc >> END

    loop["final_answer"] >> PARENT["answer"]
    START >> init >> loop >> END
```

### process_response logic

```python
def process_response(tool_calls, content, messages, iteration):
    if tool_calls:
        # Có tool calls → execute tools → continue loop
        new_messages = execute_tools(tool_calls, messages)
        return {
            "messages": new_messages,
            "done": False,
            "final_answer": "",
            "iteration": iteration + 1
        }
    else:
        # Không có tool calls → LLM trả lời trực tiếp → done
        return {
            "messages": messages,
            "done": True,
            "final_answer": content,
            "iteration": iteration + 1
        }
```

## Parallel Tool Execution

Khi LLM gọi nhiều tools cùng lúc, có thể execute song song:

```python
import asyncio

async def execute_tools_parallel(tool_calls):
    tasks = [execute_single_tool(tc) for tc in tool_calls]
    return await asyncio.gather(*tasks)
```

## Best Practices

1. **max_iterations** — Luôn set giới hạn loop để tránh infinite loop
2. **Tool validation** — Validate tool arguments trước khi execute
3. **Error handling** — Catch tool execution errors, trả error message cho LLM
4. **Tracing** — Dùng tracer để debug agent reasoning

## Tiếp theo

- [Multi-model](11-multi-model.md) — Load balancing, ensemble
- [Error Handling](07-error-handling.md) — Error patterns
