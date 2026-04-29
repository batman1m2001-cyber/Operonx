# 04 — LLM Advanced (Python)

Structured output (JSON schema), tool calling, multi-turn chat. Tier-2
install — depends on `operonx[openai]`.

| Scenario     | Ops                              | Notes                                        |
|--------------|----------------------------------|----------------------------------------------|
| `structured` | `PromptOp → LLMOp`               | Forces JSON-schema response                  |
| `tool`       | `PromptOp → LLMOp → process`     | Calculator tool; `process_response` runs it  |
| `multi_turn` | `PromptOp → LLMOp → update`      | Appends user+assistant to history            |

## Project layout

```
ex04_llm_advanced/
├── pyproject.toml      # operonx[openai]>=0.6.2
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
