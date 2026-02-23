# hush-tutorial

User documentation (Vietnamese) and runnable examples for learning Hush.

## Structure

```
hush-tutorial/
├── docs/               # Documentation in Vietnamese
│   ├── 00-tong-quan.md           # Overview
│   ├── 01-cai-dat-va-thiet-lap.md # Installation & setup
│   ├── 02-quickstart.md          # First workflow
│   ├── 03-core-concepts.md       # GraphOp, FuncOp, PARENT
│   ├── 04-llm-integration.md     # LLM usage
│   ├── 05-loops-branches.md      # Control flow
│   ├── 06-embeddings-rag.md      # Vector search
│   ├── 07-error-handling.md      # Error patterns
│   ├── 08-parallel-execution.md  # Async patterns
│   ├── 09-tracing-observability.md # Observability setup
│   ├── 10-agent-workflow.md      # Agent patterns
│   ├── 11-multi-model.md         # Multi-provider setups
│   ├── 12-shorthand-syntax.md    # Syntactic sugar
│   └── 13-rust-mode-va-plugin.md # Rust mode & plugin ops
├── examples/           # Runnable Python files
│   ├── 01_hello_world.py         # No API keys needed
│   ├── 02_data_pipeline.py       # No API keys needed
│   ├── 03_llm_chat.py            # Requires API key
│   ├── 04_llm_advanced.py        # Requires API key
│   ├── 05_loops_and_branches.py
│   ├── 06_tracing.py             # HushEyesTracer
│   ├── 07_embeddings_and_rag.py
│   ├── 08_langfuse_tracing.py    # External tracer
│   ├── 09_otel_tracing.py
│   ├── 10_error_handling.py
│   ├── 11_agent_workflow.py
│   ├── 12_multi_model.py
│   ├── 13_parallel_advanced.py
│   ├── 14_rag_advanced.py
│   ├── 15_shorthand_syntax.py
│   ├── 17_rust_mode.py          # Requires rush-core
│   └── 18_rust_plugin_ops.py    # Requires rush-core + rush-ops-builtin
└── resources.starter.yaml        # Config template
```

## Documentation Conventions

- **Language**: Vietnamese
- **Progression**: Numbered 00-13 for reading order
- **Format**: Each doc covers one concept with code examples
- **Cross-references**: Link to related examples

## Example Conventions

- **Numbering**: Matches documentation chapters
- **Naming**: `{number}_{topic}.py` with underscores
- **Docstrings**: Each file has a module docstring explaining:
  - What the example demonstrates
  - Prerequisites (API keys, etc.)
  - How to run it
- **No API keys**: Examples 01-02, 05-06 work without external services

## When to Add Documentation

1. **New feature in hush-core/providers**: Add to relevant existing doc or create new one
2. **New op type**: Add to 03-core-concepts.md or create dedicated doc
3. **New provider**: Add to 04-llm-integration.md or 06-embeddings-rag.md

## When to Add Examples

1. **New feature**: Create example demonstrating the feature
2. **Complex pattern**: Create advanced example showing best practices
3. **Numbering**: Use next available number, keep logical grouping

## Example Template

```python
"""
{Number}. {Title}

Demonstrates:
- Feature A
- Feature B

Prerequisites:
- [Optional] API key: OPENAI_API_KEY

Run:
    python examples/{number}_{topic}.py
"""

import asyncio
from hush.core import Hush, GraphOp, FuncOp, START, END, PARENT


async def main():
    # Define workflow
    with GraphOp(name="example") as graph:
        # ... ops ...
        START >> ... >> END

    # Run
    engine = Hush(graph)
    result = await engine.run(inputs={...})
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
```

## Updating Documentation

1. Keep Vietnamese language consistent
2. Update code examples if API changes
3. Ensure example numbers match doc numbers where applicable
4. Test all code snippets before committing

## Sync with Code Changes

When code changes in hush-core or hush-providers, update docs/ and examples/ accordingly.

See [/CLAUDE.md](../CLAUDE.md) for the full sync mapping:

| Code Change | Update Here |
|-------------|-------------|
| New/changed op types | docs/03-core-concepts.md |
| LLM provider changes | docs/04-llm-integration.md |
| Embedding/reranker changes | docs/06-embeddings-rag.md |
| Tracer changes | docs/09-tracing-observability.md |
| Control flow changes | docs/05-loops-branches.md |

Always create/update a matching example when documenting new features.
