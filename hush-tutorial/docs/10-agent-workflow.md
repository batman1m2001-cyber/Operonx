# Agent Workflow

Xây dựng AI agent với tool calling và WhileLoopNode.

> **Ví dụ chạy được**: `examples/11_agent_workflow.py`

## Kiến trúc Agent

```
Init → WhileLoopNode(not done):
         → PromptNode → LLMNode → Check tool_calls
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

### Bước 3: Agent workflow với WhileLoopNode

```python
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT
from hush.core.nodes.iteration import WhileLoopNode
from hush.providers import PromptNode, LLMNode

with GraphNode(name="agent") as graph:
    # Init state
    init = CodeNode(
        name="init",
        code_fn=lambda query: {
            "messages": [
                {"role": "system", "content": "Bạn là assistant có thể tra cứu thời tiết."},
                {"role": "user", "content": query}
            ],
            "iteration": 0,
            "done": False,
            "final_answer": ""
        },
        inputs={"query": PARENT["query"]},
        outputs={"messages": PARENT, "iteration": PARENT, "done": PARENT, "final_answer": PARENT}
    )

    # Agent loop
    with WhileLoopNode(
        name="loop",
        condition=lambda done: not done,
        inputs={"done": PARENT["done"], "messages": PARENT["messages"],
                "iteration": PARENT["iteration"]},
        max_iterations=5,
    ) as loop:
        # Call LLM với tools
        llm = LLMNode(
            name="llm",
            resource_key="gpt-4o",
            inputs={"messages": PARENT["messages"], "tools": tools, "tool_choice": "auto"}
        )

        # Process response
        process = CodeNode(
            name="process",
            code_fn=lambda tool_calls, content, messages, iteration: process_response(
                tool_calls, content, messages, iteration
            ),
            inputs={
                "tool_calls": llm["tool_calls"],
                "content": llm["content"],
                "messages": PARENT["messages"],
                "iteration": PARENT["iteration"]
            },
            outputs={"messages": PARENT, "done": PARENT, "final_answer": PARENT, "iteration": PARENT}
        )

        START >> llm >> process >> END

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
