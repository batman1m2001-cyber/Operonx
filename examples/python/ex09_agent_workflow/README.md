# 09 — Agent Workflow (Python)

Tool-calling agent built on `@graph.loop`. The loop body sends the
conversation to the LLM, runs any tool calls, and stops when the LLM
stops calling tools.

| Scenario   | Example query                                                     |
|------------|-------------------------------------------------------------------|
| `calc`     | `What is 25 * 4 + 100?` — exercises `calculator` tool             |
| `search`   | `Tell me about Python programming language.` — exercises `search` |
| `combined` | Combined math + search in one turn                                |

## Project layout

```
ex09_agent_workflow/
├── pyproject.toml      # operonx[openai]>=0.6.2
├── README.md
├── .env.example        # OPENAI_API_KEY
├── resources.yaml      # llm:gpt-4o-mini
└── main.py             # tools + ops + @graph.loop body + outer @graph
```

## Run

```bash
uv sync
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY
uv run python main.py
```
