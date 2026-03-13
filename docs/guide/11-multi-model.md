# Multi-model Workflows

Sử dụng nhiều LLM models: load balancing, fallback, ensemble, cost routing.

> **Ví dụ chạy được**: `examples/10_multi_model/demo.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `LLMOp.of()` | `LLMOp` | `LLMOp.of(resource="gpt-4o", messages=PARENT["msgs"])` |
> | `PromptOp.of()` | `PromptOp` | `PromptOp.of(template={...}, var=PARENT["x"])` |
> | `if_().else_()` | `BranchOp` | `if_(PARENT["x"] > 0, "a").else_("b")` |

## Parallel Model Comparison

So sánh output từ nhiều models song song.

```python
from hush.providers import PromptOp, LLMOp

with GraphOp(name="compare") as graph:
    p = PromptOp.of(
        template={"system": "Answer briefly.", "user": "{query}"},
        query=PARENT["query"],
    )
    a = LLMOp.of(resource="gpt-4o", messages=p["messages"])
    b = LLMOp.of(resource="gpt-4o-mini", messages=p["messages"])
    START >> p >> [a, b] >> END
```

## Cost-based Routing

Chọn model dựa trên complexity.

```python
from hush.core.ops.flow.branch_op import if_

@op
def classify(query: str):
    return {"complexity": "complex" if len(query) > 100 else "simple"}

with GraphOp(name="cost-routing") as graph:
    cls = classify(query=PARENT["query"])
    router = if_(cls["complexity"] == "complex", "use_gpt4o").else_("use_mini")

    # Complex → gpt-4o, Simple → gpt-4o-mini
    use_gpt4o = LLMOp.of(resource="gpt-4o", messages=PARENT["messages"])
    use_mini = LLMOp.of(resource="gpt-4o-mini", messages=PARENT["messages"])

    START >> cls >> router
    router >> [use_gpt4o, use_mini]
    [use_gpt4o, use_mini] >> ~END
```

## Load Balancing

Phân tải requests giữa nhiều models theo tỷ lệ. LLMOp dùng weighted random selection.

```python
llm = LLMOp.of(
    resource=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],  # 30% gpt-4o, 70% gpt-4o-mini
    messages=p["messages"],
)
```

- Nếu không set `ratios`, mặc định chia đều
- `model_used` output cho biết model nào được chọn

## Fallback

Tự động chuyển sang model khác khi primary fails.

```python
llm = LLMOp.of(
    resource="gpt-4o",
    fallback=["azure-gpt4", "gemini"],
    messages=prompt["messages"],
)
# gpt-4o fails → try azure-gpt4 → try gemini
```

## Ensemble + Judge

Nhiều models trả lời → model khác chọn câu trả lời tốt nhất.

> **Lưu ý:** Khi nhiều ops chạy song song và output cùng key (`content`),
> phải dùng `outputs=` để map sang keys khác nhau, tránh ghi đè lẫn nhau.

```python
with GraphOp(name="ensemble") as graph:
    p = PromptOp.of(
        template={"system": "Answer the question.", "user": "{query}"},
        query=PARENT["query"],
    )

    # 3 models trả lời song song — mỗi model map content → key riêng
    llm_a = LLMOp.of(
        resource="gpt-4o",
        messages=p["messages"],
        outputs={"content": PARENT["answer_a"]},
    )
    llm_b = LLMOp.of(
        resource="gpt-4o-mini",
        messages=p["messages"],
        outputs={"content": PARENT["answer_b"]},
    )
    llm_c = LLMOp.of(
        resource="or-claude-4-sonnet",
        messages=p["messages"],
        outputs={"content": PARENT["answer_c"]},
    )

    # Judge chọn câu tốt nhất
    jp = PromptOp.of(
        template={"system": "Chọn câu trả lời tốt nhất.", "user": "{answer_a}\n{answer_b}\n{answer_c}"},
        answer_a=PARENT["answer_a"],
        answer_b=PARENT["answer_b"],
        answer_c=PARENT["answer_c"],
    )
    judge = LLMOp.of(resource="gpt-4o", messages=jp["messages"])

    START >> p >> [llm_a, llm_b, llm_c]
    [llm_a, llm_b, llm_c] >> jp >> judge >> END
```

Xem ví dụ đầy đủ tại `examples/10_multi_model/demo.py`.

## Tiếp theo

- [LLM Integration](04-llm-integration.md) — Chi tiết providers, tools, structured output
- [Tracing & Observability](09-tracing-observability.md) — Monitor cost
