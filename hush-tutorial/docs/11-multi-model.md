# Multi-model Workflows

Sử dụng nhiều LLM models: load balancing, fallback, ensemble, cost routing.

> **Ví dụ chạy được**: `examples/12_multi_model.py`

## Parallel Model Comparison

So sánh output từ nhiều models song song.

```python
with GraphNode(name="compare") as graph:
    prompt = PromptNode(
        name="prompt",
        inputs={
            "prompt": {"system": "Answer briefly.", "user": "{query}"},
            "query": PARENT["query"]
        }
    )
    llm_a = LLMNode(
        name="gpt4o",
        resource_key="gpt-4o",
        inputs={"messages": prompt["messages"]},
        outputs={"content": PARENT["answer_a"]}
    )
    llm_b = LLMNode(
        name="gpt4o_mini",
        resource_key="gpt-4o-mini",
        inputs={"messages": prompt["messages"]},
        outputs={"content": PARENT["answer_b"]}
    )
    START >> prompt >> [llm_a, llm_b] >> END
```

## Cost-based Routing

Chọn model dựa trên complexity.

```python
from hush.core.nodes.flow.branch_node import if_

with GraphNode(name="cost-routing") as graph:
    classify = CodeNode(
        name="classify",
        code_fn=lambda query: {"complexity": "complex" if len(query) > 100 else "simple"},
        inputs={"query": PARENT["query"]},
        outputs={"complexity": PARENT}
    )
    router = if_(PARENT["complexity"] == "complex", "use_gpt4o").else_("use_mini")

    # Complex → gpt-4o, Simple → gpt-4o-mini
    use_gpt4o = LLMNode(name="use_gpt4o", resource_key="gpt-4o", ...)
    use_mini = LLMNode(name="use_mini", resource_key="gpt-4o-mini", ...)

    START >> classify >> router
    router >> [use_gpt4o, use_mini]
    [use_gpt4o, use_mini] >> ~END
```

## Load Balancing

Phân tải requests giữa nhiều models theo tỷ lệ. LLMNode dùng weighted random selection.

```python
llm = LLMNode(
    name="llm",
    resource_key=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],  # 30% gpt-4o, 70% gpt-4o-mini
    inputs={"messages": prompt["messages"]}
)
```

- Nếu không set `ratios`, mặc định chia đều
- `model_used` output cho biết model nào được chọn

## Fallback

Tự động chuyển sang model khác khi primary fails.

```python
llm = LLMNode(
    name="llm",
    resource_key="gpt-4o",
    fallback=["azure-gpt4", "gemini"],
    inputs={"messages": prompt["messages"]}
)
# gpt-4o fails → try azure-gpt4 → try gemini
```

## Ensemble + Judge

Nhiều models trả lời → model khác chọn câu trả lời tốt nhất.

```python
with GraphNode(name="ensemble") as graph:
    prompt = PromptNode(name="prompt", ...)

    # 3 models trả lời song song
    llm_a = LLMNode(name="a", resource_key="gpt-4o", ...)
    llm_b = LLMNode(name="b", resource_key="gpt-4o-mini", ...)
    llm_c = LLMNode(name="c", resource_key="or-claude-4-sonnet", ...)

    # Judge chọn câu tốt nhất
    judge_prompt = PromptNode(
        name="judge_prompt",
        inputs={
            "prompt": {"system": "Chọn câu trả lời tốt nhất.", "user": "..."},
            "answer_a": PARENT["answer_a"],
            "answer_b": PARENT["answer_b"],
            "answer_c": PARENT["answer_c"]
        }
    )
    judge = LLMNode(name="judge", resource_key="gpt-4o", ...)

    START >> prompt >> [llm_a, llm_b, llm_c]
    [llm_a, llm_b, llm_c] >> judge_prompt >> judge >> END
```

Xem ví dụ đầy đủ tại `examples/12_multi_model.py`.

## Tiếp theo

- [LLM Integration](04-llm-integration.md) — Chi tiết providers, tools, structured output
- [Tracing & Observability](09-tracing-observability.md) — Monitor cost
