# 08 — Error Handling (Python)

Error-handling patterns: capture, route, retry, LLM fallback.

| Scenario       | Ops                                                  | Needs key?              |
|----------------|------------------------------------------------------|-------------------------|
| `capture`      | `failing`                                            | No                      |
| `routing`      | `safe_divide → if_ → handle_success / handle_error` | No                      |
| `retry`        | `retry_with_backoff → with_fallback`                 | No                      |
| `llm_fallback` | `PromptOp → LLMOp(gpt-4o, fallback=[gpt-4o-mini])`  | `OPENAI_API_KEY`        |

## Run

```bash
uv run python -m examples.python.ex08_error_handling.demo
uv run python -m examples.python.ex08_error_handling.demo --runs 20
```

Writes `examples/bench_results/ex08_error_handling_python.json`.
