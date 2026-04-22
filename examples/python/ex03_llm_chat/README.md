# 03 — LLM Chat (Python)

Three LLM chat graphs showing the basic authoring shortcuts. Requires `OPENAI_API_KEY` in `.env`.

| Scenario    | Ops                                        | Notes                            |
|-------------|--------------------------------------------|----------------------------------|
| `basic`     | `PromptOp → LLMOp`                         | Explicit two-op form.            |
| `chain`     | `chat()`                                   | Single-op all-in-one helper.     |
| `summarize` | `clean_text → PromptOp → LLMOp`            | Pre-processing + summarization.  |

## Run

```bash
uv run python -m examples.python.ex03_llm_chat.demo
uv run python -m examples.python.ex03_llm_chat.demo --runs 20
uv run python -m examples.python.ex03_llm_chat.demo --langfuse
```

Writes `examples/bench_results/ex03_llm_chat_python.json`.
