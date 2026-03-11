# Agent Workflow

Xây dựng AI agent với tool calling và WhileOp.

> **Ví dụ chạy được**: `examples/11_agent_workflow.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `@op` | `FuncOp` | `@op` decorator trên function |
> | `WhileOp.of()` | `WhileOp` | `WhileOp.of(counter=0, until="counter >= 5")` |
> | `LLMOp.of()` | `LLMOp` | `LLMOp.of(resource="gpt-4o", messages=PARENT["msgs"])` |

## Kiến trúc Agent

```
Init → WhileOp.of(not done):
         → LLMOp.of() → Check tool_calls
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

### Bước 3: Agent workflow với WhileOp.of()

```python
from hush.core import Hush, GraphOp, op, START, END, PARENT
from hush.core import WhileOp
from hush.providers import LLMOp

@op
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

@op
def process(content, tool_calls, messages, iteration):
    return process_response(tool_calls, content, messages, iteration)

with GraphOp(name="agent") as graph:
    init = init_agent(
        query=PARENT["query"],
        outputs={"*": PARENT},  # Forward all init outputs to graph state
    )

    # Agent loop
    with WhileOp.of(
        messages=PARENT["messages"],
        iteration=PARENT["iteration"],
        done=PARENT["done"],
        final_answer=PARENT["final_answer"],
        until="done == True or iteration >= 5",
        max_iterations=10,
    ) as loop:
        llm = LLMOp.of(
            resource="gpt-4o",
            messages=PARENT["messages"],
            tools=tools,
        )
        proc = process(
            content=llm["content"],
            tool_calls=llm["tool_calls"],
            messages=PARENT["messages"],
            iteration=PARENT["iteration"],
        )

        # Update loop state via >> operator
        proc["new_messages"] >> PARENT["messages"]
        proc["new_iteration"] >> PARENT["iteration"]
        proc["is_done"] >> PARENT["done"]
        proc["answer"] >> PARENT["final_answer"]

        START >> llm >> proc >> END

    loop["final_answer"] >> PARENT["answer"]
    START >> init >> loop >> END
```

### process_response logic

```python
def process_response(tool_calls, content, messages, iteration):
    new_messages = messages + [{"role": "assistant", "content": content,
                                **({"tool_calls": tool_calls} if tool_calls else {})}]
    if tool_calls:
        # Có tool calls → execute tools → continue loop
        for tc in tool_calls:
            new_messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": execute_tool(tc)})
        return {
            "new_messages": new_messages,
            "new_iteration": iteration + 1,
            "is_done": False,
            "answer": None,
        }
    else:
        # Không có tool calls → LLM trả lời trực tiếp → done
        return {
            "new_messages": new_messages,
            "new_iteration": iteration + 1,
            "is_done": True,
            "answer": content,
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
