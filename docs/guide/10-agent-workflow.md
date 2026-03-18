# Agent Workflow

Xây dựng AI agent với tool calling và `@graph.loop()`.

> **Ví dụ chạy được**: `examples/ex09_agent_workflow/demo.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `@op` | `FuncOp` | `@op` decorator trên function |
> | `@graph.loop()` | Loop GraphOp | `@graph.loop(until="done == True", max_iterations=10)` |
> | `LLMOp.of()` | `LLMOp` | `LLMOp.of(resource="gpt-4o", messages=..., tools=...)` |

## Kiến trúc Agent

```
Init → @graph.loop(until="done == True"):
         → LLMOp.of(tools=...) → process_response
           → Có tool_calls: Execute tools → Update messages → Loop
           → Không tool_calls: Done → Exit
```

## Tool-calling Agent

### Bước 1: Định nghĩa tools

```python
import json

TOOLS = {
    "calculator": lambda expr: {"result": str(eval(expr, {"__builtins__": {}}, {}))},
    "search": lambda query: {"result": "Mock search result for: " + query},
}

TOOL_DESCRIPTIONS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate mathematical expressions",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Math expression"}
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search for information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        },
    },
]
```

### Bước 2: Implement agent ops

```python
from hush.core import graph, op, START, END, PARENT, GraphOp
from hush.providers import LLMOp

@op
def init_agent(query: str):
    """Khởi tạo agent state."""
    return {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant with access to tools."},
            {"role": "user", "content": query},
        ],
        "done": False,
        "answer": "",
    }

@op
def process_response(content, tool_calls, messages):
    """Xử lý response từ LLM: execute tools hoặc return final answer."""
    new_messages = list(messages)
    assistant_msg = {"role": "assistant", "content": content or ""}
    if tool_calls:
        assistant_msg["tool_calls"] = tool_calls
    new_messages.append(assistant_msg)

    if tool_calls:
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            result = TOOLS.get(func_name, lambda **kw: {"error": f"Unknown: {func_name}"})(**args)
            new_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result),
            })
        return {"messages": new_messages, "done": False, "answer": ""}
    else:
        return {"messages": new_messages, "done": True, "answer": content or ""}
```

### Bước 3: Agent workflow với @graph.loop()

```python
@graph.loop(until="done == True", max_iterations=10)
def agent_loop(messages, done, answer):
    """Repeat LLM -> process until done == True."""
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        messages=messages,
        tools=TOOL_DESCRIPTIONS,
    )

    proc = process_response(
        content=llm["content"],
        tool_calls=llm["tool_calls"],
        messages=messages,
    )

    # Update loop state cho iteration tiếp theo
    proc["messages"] >> PARENT["messages"]
    proc["done"] >> PARENT["done"]
    proc["answer"] >> PARENT["answer"]

    START >> llm >> proc >> END


def build_agent():
    """Graph: init agent → loop (LLM + tools) → answer."""
    with GraphOp(name="agent") as g:
        init = init_agent(query=PARENT["query"])

        loop = agent_loop(
            messages=init["messages"],
            done=init["done"],
            answer=init["answer"],
        )

        loop["answer"] >> PARENT["answer"]
        START >> init >> loop >> END
    return g
```

### Giải thích @graph.loop

- `@graph.loop(until="done == True")` — Loop cho đến khi `done` state == `True`
- `max_iterations=10` — Safety net tránh infinite loop
- Function params (`messages`, `done`, `answer`) là loop state — carry qua mỗi iteration
- `proc["messages"] >> PARENT["messages"]` — Update loop state sau mỗi iteration
- `loop["answer"] >> PARENT["answer"]` — Map loop output ra graph output

### Luồng thực thi

```
Iteration 1: LLM trả về tool_calls → process_response execute tools → done=False → loop
Iteration 2: LLM nhận tool results → trả lời trực tiếp → done=True → exit
Result: loop["answer"] chứa câu trả lời cuối cùng
```

## Rust Mode

Agent workflow chạy được ở cả Python và Rust mode. Dùng `@op(rust="...")` để viết Rust ops cho phần init và process:

```python
@op(rust="./rust_ops::pipeline::init_agent")
def init_agent(query: str):
    return {"messages": [...], "done": False, "answer": ""}

@op(rust="./rust_ops::pipeline::process_agent_response")
def process_response(content, tool_calls, messages):
    ...  # Python fallback
```

Rust plugin phải **thực sự compute tool results** — không được simulate. LLM cần kết quả chính xác để quyết định dừng loop.

## Best Practices

1. **max_iterations** — Luôn set giới hạn loop để tránh infinite loop
2. **Tool validation** — Validate tool arguments trước khi execute
3. **Error handling** — Catch tool execution errors, trả error message cho LLM
4. **Tracing** — Dùng tracer để debug agent reasoning
5. **Rust ops phải compute thật** — Đặc biệt trong agent loops, tool results phải chính xác

## Tiếp theo

- [Multi-model](11-multi-model.md) — Load balancing, ensemble
- [Error Handling](07-error-handling.md) — Error patterns
