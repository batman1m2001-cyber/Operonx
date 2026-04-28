# 03 — LLM Chat (Python)

Three LLM chat graphs showing the basic authoring shortcuts. Tier-2
install — depends on `operonx[openai]`.

| Scenario    | Ops                                  | Notes                            |
|-------------|--------------------------------------|----------------------------------|
| `basic`     | `PromptOp → LLMOp`                   | Explicit two-op form             |
| `chain`     | `chat()`                             | Single-op all-in-one helper      |
| `summarize` | `clean_text → PromptOp → LLMOp`      | Pre-processing + summarization   |

## Project layout

```
ex03_llm_chat/
├── pyproject.toml      # operonx[openai]>=0.6.2
├── README.md
├── .env.example        # OPENAI_API_KEY
├── resources.yaml      # llm:gpt-4o-mini
└── main.py             # @graph factories + chat() / LLMOp / PromptOp
```

## Run

```bash
uv sync
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY
uv run python main.py
```
