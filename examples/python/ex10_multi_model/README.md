# 10 — Multi-Model (Python)

Patterns for running multiple LLMs together.

| Scenario        | Shape                                                                 |
|-----------------|-----------------------------------------------------------------------|
| `parallel`      | Same prompt → `gpt-4o` + `gpt-4o-mini` in parallel → compare          |
| `routing`       | Classify → `if_` → route to `gpt-4o-mini` or `gpt-4o`                 |
| `load_balanced` | Weighted model selection (70/30)                                      |
| `fallback`      | `gpt-4o` with fallback to `gpt-4o-mini`                               |
| `ensemble`      | Two answers + judge picks the better one                              |

## Project layout

```
ex10_multi_model/
├── pyproject.toml      # operonx[openai]>=0.6.2
├── README.md
├── .env.example        # OPENAI_API_KEY
├── resources.yaml      # llm:gpt-4o + llm:gpt-4o-mini
└── main.py
```

## Run

```bash
uv sync
cp .env.example .env
uv run python main.py
```
