# 04 — LLM Advanced (Python)

Structured output (JSON schema), tool calling, multi-turn chat. Tier-2
install — depends on `operonx[openai]`.

| Scenario     | Ops                              | Notes                                        |
|--------------|----------------------------------|----------------------------------------------|
| `structured` | `LLMOp`                          | Forces JSON-schema response                  |
| `tool`       | `LLMOp → process`                | Calculator tool; `process_response` runs it  |
| `multi_turn` | `build_messages → LLMOp → update`| Caller builds history + user turn, then calls the model |

## Project layout

```
ex04_llm_advanced/
├── pyproject.toml      # operonx[openai]>=1.3.0
├── README.md
├── .env.example        # OPENAI_API_KEY
├── resources.yaml      # llm:gpt-4o-mini
└── main.py             # @graph factories + LLMOp + tool helpers
```

## Run

```bash
uv sync
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY
uv run python main.py
```
