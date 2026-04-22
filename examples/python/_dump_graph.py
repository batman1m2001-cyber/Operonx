"""Regenerate ``examples/rust/<name>/graph.json`` from Python's authoring DSL.

Used when an example's ``workflow.py`` changes. Walks each
``build_*`` factory, serializes via ``graph.serialize()``, strips the
Python-only ``python_callable`` field, and **redacts any sensitive
values** (``api_key``, ``secret_key``, tokens) that ``resources.yaml``
expansion would otherwise inline into the JSON.

Usage::

    uv run python -m examples.python._dump_graph <example> \
        --scenarios hello chain parallel \
        --factories build_hello build_chain build_parallel
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SENSITIVE_KEYS = {
    "api_key",
    "secret_key",
    "access_token",
    "refresh_token",
    "private_key",
    "private_key_id",
}


def _scrub(node):
    """Recursively redact secret-bearing fields and strip Python-only keys."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "python_callable":
                continue
            if k in SENSITIVE_KEYS and isinstance(v, str) and v:
                out[k] = "<REDACTED>"
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(node, list):
        return [_scrub(v) for v in node]
    return node


def dump_graph(factory) -> dict:
    graph = factory()
    if hasattr(graph, "build"):
        graph.build()
    data = graph.serialize()
    cleaned = _scrub(data)
    cleaned["schema_version"] = "1.0"
    return cleaned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="_dump_graph")
    parser.add_argument("example", help="Example name, e.g. ex01_hello_world.")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        required=True,
        help="Scenario keys (one per factory).",
    )
    parser.add_argument(
        "--factories",
        nargs="+",
        required=True,
        help="Factory function names (one per scenario, same order).",
    )
    args = parser.parse_args(argv)

    if len(args.scenarios) != len(args.factories):
        parser.error("--scenarios and --factories must have the same length.")

    sys.path.insert(0, str(REPO_ROOT))
    mod = importlib.import_module(f"examples.python.{args.example}.workflow")

    bundle = {}
    for scenario, factory_name in zip(args.scenarios, args.factories):
        factory = getattr(mod, factory_name)
        bundle[scenario] = dump_graph(factory)

    out_path = REPO_ROOT / "examples" / "rust" / args.example / "graph.json"
    out_path.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
