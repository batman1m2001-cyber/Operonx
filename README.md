# operonx-kb

Knowledge base and document intelligence on [operonx](../Operon): files become versioned documents
whose every character has an address (version, span → page, bbox), indexed for retrieval.

```bash
uv sync --extra pdf --extra faiss     # core + PDF text layer + FAISS index
PYTHONPATH=../operonx-wt/feat-kb-upstream uv run pytest -q   # until operonx PR #74 merges
```

Design and phase gates: [PLAN.md](PLAN.md). Contributor and agent rules: [AGENTS.md](AGENTS.md).
