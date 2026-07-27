# 08 — Error Handling (Python)

Capture, route, retry+fallback, and LLM fallback chains.

| Scenario       | Ops                                                  | Needs key?       |
|----------------|------------------------------------------------------|------------------|
| `capture`      | `failing`                                            | No               |
| `routing`      | `safe_divide → if_ → handle_success / handle_error`  | No               |
| `retry`        | `retry_with_backoff → with_fallback`                 | No               |
| `llm_fallback` | `LLMOp(gpt-4o, fallback=[gpt-4o-mini])`              | `OPENAI_API_KEY` |

The `llm_fallback` scenario gracefully skips if no API key is set.

## Project layout

```
ex08_error_handling/
├── pyproject.toml      # operonx[openai]>=0.6.2 (for the llm_fallback scenario)
├── README.md
├── .env.example        # OPENAI_API_KEY (only for llm_fallback)
├── resources.yaml      # llm:gpt-4o + llm:gpt-4o-mini
└── main.py
```

## Run

```bash
uv sync
cp .env.example .env  # only needed for llm_fallback
uv run python main.py
```
