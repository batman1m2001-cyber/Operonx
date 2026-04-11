"""Shared helpers for prompt-caching test scripts."""

from __future__ import annotations

import os
from pathlib import Path


def load_env() -> None:
    """Load KEY=VALUE pairs from the repo-root .env into os.environ."""
    # scripts/ -> hush-providers/ -> python/ -> repo root
    root = Path(__file__).resolve().parents[3]
    env_file = root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def big_static_context(target_tokens: int = 5000) -> str:
    """Return a deterministic block of text roughly `target_tokens` tokens long.

    Uses a ~4 chars/token approximation. Content is fixed so prefix hashes
    match across runs, which is what enables cache hits.
    """
    passage = (
        "The Hush workflow engine treats every computation as a node in a "
        "directed graph. Nodes advertise their inputs and outputs through a "
        "declarative schema, and the scheduler walks the graph breadth-first, "
        "dispatching ready nodes to an async worker pool. State is stored in "
        "an append-only log so that any step can be replayed deterministically "
        "from a checkpoint. This design lets operators compose IO-bound AI "
        "steps (LLM calls, embeddings, retrieval) with CPU-bound native ops "
        "without changing the programming model. "
    )
    approx_chars = target_tokens * 4
    repeats = (approx_chars // len(passage)) + 1
    return (passage * repeats)[:approx_chars]


def section(title: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}\n{title}\n{bar}")


def show_usage(label: str, usage: dict) -> None:
    print(f"[{label}] usage: {usage}")
