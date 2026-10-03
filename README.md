# operonx-kb

Knowledge base and document intelligence on [operonx](../Operon): files become versioned documents
whose every character has an address (version, span → page, bbox), indexed for retrieval.

```bash
uv sync --extra pdf      # core + PDF text layer (docling-parse)
uv run pytest -q
```

Design and phase gates: [PLAN.md](PLAN.md). Contributor and agent rules: [AGENTS.md](AGENTS.md).
