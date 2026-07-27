# 03 — LLM Chat (Python)

Two LLM chat graphs showing the unified `LLMOp` surface. Tier-2 install —
depends on `operonx[openai]`.

| Scenario    | Ops                              | Notes                            |
|-------------|----------------------------------|----------------------------------|
| `basic`     | `LLMOp`                          | Prompt template + call in one op |
| `summarize` | `clean_text → LLMOp`             | Pre-processing + summarization   |

## Project layout

```
ex03_llm_chat/
├── pyproject.toml      # operonx[openai]>=0.6.2
├── README.md
├── .env.example        # OPENAI_API_KEY
├── resources.yaml      # llm:gpt-4o-mini
└── main.py             # @graph factories + LLMOp.of
```

## Run

```bash
uv sync
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY
uv run python main.py
```
