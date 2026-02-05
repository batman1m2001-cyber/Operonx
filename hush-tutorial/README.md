# Hush Tutorial

Tài liệu hướng dẫn và ví dụ cho Hush framework - một workflow engine cho AI/LLM applications.

## Quick Start

```bash
# Clone và setup
cd hush-tutorial
uv sync

# Chạy ví dụ đầu tiên
uv run python examples/01_hello_world.py
```

## Project Structure

```
hush-tutorial/
├── docs/                    # Tutorial documentation
│   ├── 00-tong-quan.md      # Tổng quan architecture
│   ├── 01-cai-dat-va-thiet-lap.md
│   ├── 02-quickstart.md
│   ├── 03-core-concepts.md  # GraphNode, CodeNode, PARENT, edges
│   ├── 04-llm-integration.md
│   ├── 05-loops-branches.md # ForLoop, Map, While, if_()
│   ├── 06-embeddings-rag.md
│   ├── 07-error-handling.md
│   ├── 08-parallel-execution.md
│   ├── 09-tracing-observability.md
│   ├── 10-agent-workflow.md
│   ├── 11-multi-model.md
│   └── 12-shorthand-syntax.md  # ⭐ Shorthand functions
├── examples/                # Runnable examples
│   ├── 01_hello_world.py
│   ├── 02_data_pipeline.py
│   ├── 03_llm_chat.py
│   ├── 04_llm_advanced.py
│   ├── 05_loops_and_branches.py
│   ├── 06_tracing.py
│   ├── 07_embeddings_and_rag.py
│   ├── 08_langfuse_tracing.py
│   ├── 09_otel_tracing.py
│   ├── 10_error_handling.py
│   ├── 11_agent_workflow.py
│   ├── 12_multi_model.py
│   ├── 13_parallel_advanced.py
│   ├── 14_rag_advanced.py
│   └── 15_shorthand_syntax.py  # ⭐ Shorthand examples
└── pyproject.toml
```

## Documentation Guide

### Beginner Track

1. [Tổng quan](docs/00-tong-quan.md) — Architecture overview
2. [Cài đặt](docs/01-cai-dat-va-thiet-lap.md) — Setup environment
3. [Quickstart](docs/02-quickstart.md) — First workflow
4. [Core Concepts](docs/03-core-concepts.md) — GraphNode, CodeNode, PARENT

### Workflow Control

5. [Loops & Branches](docs/05-loops-branches.md) — ForLoop, Map, While, if_()
6. [Parallel Execution](docs/08-parallel-execution.md) — Fan-out/fan-in patterns
7. [Error Handling](docs/07-error-handling.md) — Error capture và routing

### LLM & AI

8. [LLM Integration](docs/04-llm-integration.md) — PromptNode, LLMNode
9. [Embeddings & RAG](docs/06-embeddings-rag.md) — Vector search
10. [Agent Workflow](docs/10-agent-workflow.md) — Tool calling agents
11. [Multi-model](docs/11-multi-model.md) — Load balancing, fallback

### Production

12. [Tracing](docs/09-tracing-observability.md) — LocalTracer, Langfuse, OpenTelemetry

### Reference

13. [Shorthand Syntax](docs/12-shorthand-syntax.md) — Viết code ngắn gọn

## Shorthand Syntax Cheatsheet

```python
# ❌ Verbose
with ForLoopNode(name="loop", inputs={"item": Each(items), "prefix": "Hello"}) as loop:
    ...

# ✅ Shorthand
with for_(item=Each(items), prefix="Hello") as loop:
    ...
```

| Full Class | Shorthand | Example |
|------------|-----------|---------|
| `CodeNode` | `@code_node` | `@code_node def fn(x): return {"y": x*2}` |
| `ForLoopNode` | `for_()` | `for_(item=Each(items), config=10)` |
| `MapNode` | `map_()` | `map_(x=Each(items), max_concurrency=5)` |
| `WhileLoopNode` | `while_()` | `while_(count=0, stop_condition="count >= 10")` |
| `BranchNode` | `if_()` | `if_(score >= 90, "a").else_("b")` |
| `LLMNode` | `llm_()` | `llm_("gpt-4o", messages=..., temperature=0.7)` |

See [docs/12-shorthand-syntax.md](docs/12-shorthand-syntax.md) for details.

## Running Examples

```bash
# No API key required (hush-core only)
uv run python examples/01_hello_world.py
uv run python examples/02_data_pipeline.py
uv run python examples/05_loops_and_branches.py
uv run python examples/15_shorthand_syntax.py  # ⭐ New!

# Requires API key (set in .env or environment)
uv run python examples/03_llm_chat.py
uv run python examples/11_agent_workflow.py
```

## Configuration

Copy `.env.example` and `resources.starter.yaml`:

```bash
cp docs/.env.example .env
cp docs/resources.starter.yaml resources.yaml
```

Edit `.env` with your API keys:

```bash
OPENAI_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...
```

## Requirements

- Python 3.10+
- `hush-core` and `hush-providers` packages (installed via pyproject.toml)

## License

MIT
