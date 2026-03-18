# Parallel Execution

Thực thi song song trong workflows: fan-out/fan-in, generator iteration, partial failure.

> **Ví dụ chạy được**: `examples/ex11_parallel_advanced/demo.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `@op` | `FuncOp` | `@op` decorator trên function |
> | `yield` | Generator op | `yield {"item": item}` trong `@op` function |
> | `LLMOp.of()` | `LLMOp` | `LLMOp.of(resource="gpt-4o", messages=PARENT["msgs"])` |
> | `PromptOp.of()` | `PromptOp` | `PromptOp.of(template={...}, var=PARENT["x"])` |

## Fan-out / Fan-in

Chạy nhiều ops song song, rồi merge kết quả.

```python
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def analyze_sentiment(text: str):
    positive = sum(1 for w in text.lower().split() if w in {"good", "great", "love"})
    negative = sum(1 for w in text.lower().split() if w in {"bad", "terrible", "hate"})
    return {"sentiment": "positive" if positive > negative else "neutral"}

@op
def extract_keywords(text: str):
    stop_words = {"the", "is", "a", "and", "or", "in", "on", "to", "for", "of"}
    words = [w.lower().strip(".,!?") for w in text.split()]
    return {"keywords": [w for w in words if w not in stop_words and len(w) > 2][:5]}

@op
def count_stats(text: str):
    words = text.split()
    return {"word_count": len(words), "char_count": len(text)}

@op
def merge_analysis(s, k, wc, cc):
    return {"analysis": {"sentiment": s, "keywords": k, "word_count": wc, "char_count": cc}}

with GraphOp(name="fan-out-fan-in") as graph:
    sent = analyze_sentiment(text=PARENT["text"])
    kw = extract_keywords(text=PARENT["text"])
    st = count_stats(text=PARENT["text"])
    m = merge_analysis(
        s=sent["sentiment"], k=kw["keywords"],
        wc=st["word_count"], cc=st["char_count"],
        outputs={"analysis": PARENT},
    )

    START >> [sent, kw, st]  # Fan-out — 3 ops chạy song song
    [sent, kw, st] >> m >> END  # Fan-in — hard edge: chờ tất cả
```

## Generator Iteration

Dùng generator ops (yield) để iterate qua list items. Downstream ops tự động chạy song song cho mỗi item.

```python
@op
def each_item(items: list):
    """Yield từng item — scheduler tự động song song hóa downstream."""
    for item in items:
        yield {"item": item}

@op
def process_item(item: int):
    return {"result": item * item, "status": "ok"}

with GraphOp(name="iteration-demo") as graph:
    src = each_item(items=PARENT["items"])
    proc = process_item(item=src["item"])
    proc["result"] >> PARENT["results"]
    START >> src >> proc >> END

result = await engine.run(inputs={"items": [1, 2, 3, 4, 5]})
# result["results"] = [1, 4, 9, 16, 25]
```

## Partial Failure Handling

Xử lý trường hợp một số items fail trong generator iteration — dùng error-as-value pattern thay vì exceptions.

```python
@op
def each_item(items: list):
    for item in items:
        yield {"item": item}

@op
def safe_process(item: int):
    """Returns error for even numbers instead of raising."""
    if item % 2 != 0:
        return {"result": item * 10, "error": None}
    return {"result": None, "error": f"Even number: {item}"}

with GraphOp(name="partial-failure") as graph:
    src = each_item(items=PARENT["items"])
    proc = safe_process(item=src["item"])
    proc["result"] >> PARENT["results"]
    proc["error"] >> PARENT["errors"]
    START >> src >> proc >> END

result = await engine.run(inputs={"items": [1, 2, 3, 4, 5]})
# result["results"] = [10, None, 30, None, 50]
# result["errors"] = [None, "Even number: 2", None, "Even number: 4", None]
```

## Parallel LLM Calls

Gọi nhiều LLMs song song (ví dụ: so sánh models).

```python
from hush.providers import PromptOp, LLMOp

@op
def merge_results(s, k):
    return {"summary": s, "keywords": k}

with GraphOp(name="parallel-llm") as graph:
    p_summary = PromptOp.of(
        template={"system": "Summarize in one sentence.", "user": "{text}"},
        text=PARENT["text"],
    )
    p_keywords = PromptOp.of(
        template={"system": "List 3 keywords, comma-separated.", "user": "{text}"},
        text=PARENT["text"],
    )

    llm_summary = LLMOp.of(resource="gpt-4o-mini", messages=p_summary["messages"])
    llm_keywords = LLMOp.of(resource="gpt-4o-mini", messages=p_keywords["messages"])

    m = merge_results(
        s=llm_summary["content"],
        k=llm_keywords["content"],
        outputs={"*": PARENT},
    )

    START >> [p_summary, p_keywords]
    p_summary >> llm_summary
    p_keywords >> llm_keywords
    [llm_summary, llm_keywords] >> m >> END
```

### Batch LLM via Generator

Gọi nhiều queries song song qua generator op:

```python
@op
def each_query(queries: list):
    for query in queries:
        yield {"query": query}

with GraphOp(name="batch-llm") as graph:
    src = each_query(queries=PARENT["queries"])
    p = PromptOp.of(
        template={"system": "Answer in one sentence.", "user": "{query}"},
        query=src["query"],
    )
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        messages=p["messages"],
    )
    llm["content"] >> PARENT["answers"]
    START >> src >> p >> llm >> END
```

## Rust Backend

Hush hỗ trợ Rust execution backend cho performance cao hơn so với Python mode. Tất cả parallel patterns (fan-out/fan-in, generator iteration) hoạt động giống nhau.

```python
engine = Hush(graph)
engine.serve(port=8000, backend="rust", rust_ops="rust_ops")
```

Chi tiết đầy đủ: xem [Rust Mode và Plugin Ops](13-rust-mode-va-plugin.md) — bao gồm cả Rust plugin ops (`@op(rust=...)`).

## Best Practices

1. **Fan-out cho independent tasks** — Dùng `START >> [a, b, c]`
2. **Generator ops cho list processing** — Dùng `yield` trong `@op` function
3. **Error-as-value cho partial failure** — Return `{"result": None, "error": msg}` thay vì raise
4. **Hard edge cho fan-in** — `[a, b, c] >> merge` chờ tất cả
5. **Soft edge sau branch** — `[path_a, path_b] >> ~c` khi chỉ 1 nhánh chạy
6. **Rust backend cho throughput** — Dùng `backend="rust"` khi serve

## Tiếp theo

- [Tracing & Observability](09-tracing-observability.md) — Debug parallel workflows
- [Error Handling](07-error-handling.md) — Error patterns
