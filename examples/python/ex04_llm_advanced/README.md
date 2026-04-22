# 04 — LLM Advanced (Python)

Structured output, tool calling, multi-turn chat. Requires `OPENAI_API_KEY` in `.env`.

| Scenario     | Ops                              | Notes                                        |
|--------------|----------------------------------|----------------------------------------------|
| `structured` | `PromptOp → LLMOp`               | Forces JSON schema response.                 |
| `tool`       | `PromptOp → LLMOp → process`     | Calculator tool; `process_response` runs it. |
| `multi_turn` | `PromptOp → LLMOp → update`      | Appends user+assistant to history.           |

## Run

```bash
uv run python -m examples.python.ex04_llm_advanced.demo
uv run python -m examples.python.ex04_llm_advanced.demo --runs 5
uv run python -m examples.python.ex04_llm_advanced.demo --langfuse
```

Writes `examples/bench_results/ex04_llm_advanced_python.json`.
