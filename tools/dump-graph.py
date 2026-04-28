"""Regenerate ``examples/rust/<name>/graph.json`` from Python's authoring DSL.

Used when an example's ``main.py`` changes — the Rust binary loads the
checked-in JSON instead of round-tripping through Python at runtime.

Walks each ``@graph``-decorated factory in the example's ``main.py``,
calls it with ``None`` for every parameter (so each parameter becomes
an external input ref rather than a literal), then serialises via
``GraphOp.serialize()``. Strips:

- ``python_callable`` — Python-only repr pointer.
- ``resource_config`` / ``resource_configs`` / ``fallback_configs`` —
  inline caches that Python populates from ``resources.yaml`` expansion.
  These embed live env-resolved values (the OpenAI-key leak vector).
  The Rust engine never reads them — it resolves by name
  (``resource: "gpt-4o"``) via its own ``ResourceHub``, which reads the
  same ``resources.yaml`` at run time and does the ``${VAR}``
  interpolation itself.

Any lingering secret-bearing fields (in case a user attaches custom
config blobs outside the known cache fields) are redacted to
``<REDACTED>`` as a belt-and-braces check.

Usage::

    uv run python tools/dump-graph.py ex03_llm_chat \\
        --scenarios basic chain summarize \\
        --factories basic_chat chain_chat summarize_pipeline

Each ``--factories`` entry must be a ``@graph``-decorated function in
the example's ``main.py``. The tool jumps into the example directory
and calls ``operonx.bootstrap()`` so provider ops can resolve their
``resources.yaml`` at build time.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Keys Python adds at serialize time that we either can't (Rust rejects)
# or shouldn't (contain live secrets) persist to disk.
DROP_KEYS = {
    "python_callable",
    "resource_config",
    "resource_configs",
    "fallback_configs",
}

# Belt-and-braces: even after dropping the cache fields above, redact any
# secret-ish value that happens to show up elsewhere. Harmless when the
# strip is already complete.
SENSITIVE_KEYS = {
    "api_key",
    "secret_key",
    "access_token",
    "refresh_token",
    "private_key",
    "private_key_id",
}


def _scrub(node):
    """Drop cached resource configs and redact stray secret-bearing fields."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in DROP_KEYS:
                continue
            if k in SENSITIVE_KEYS and isinstance(v, str) and v:
                out[k] = "<REDACTED>"
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(node, list):
        return [_scrub(v) for v in node]
    return node


def dump_graph(factory, scenario: str) -> dict:
    """Call a @graph factory with `None` placeholders, build, serialize, scrub."""
    sig = inspect.signature(factory)
    kwargs = {p: None for p in sig.parameters}
    graph = factory(**kwargs, name=scenario)
    if hasattr(graph, "build"):
        graph.build()
    data = graph.serialize()
    cleaned = _scrub(data)
    cleaned["schema_version"] = "1.0"
    return cleaned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dump-graph")
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
        help="@graph factory function names (one per scenario, same order).",
    )
    args = parser.parse_args(argv)

    if len(args.scenarios) != len(args.factories):
        parser.error("--scenarios and --factories must have the same length.")

    # Run from the example dir so .env + resources.yaml resolve relative to
    # the example. Provider ops resolve their resource hub at build time.
    example_dir = REPO_ROOT / "examples" / "python" / args.example
    if not example_dir.is_dir():
        parser.error(f"example directory not found: {example_dir}")

    sys.path.insert(0, str(REPO_ROOT))
    os.chdir(example_dir)

    import operonx

    operonx.bootstrap()

    mod = importlib.import_module(f"examples.python.{args.example}.main")

    bundle = {}
    for scenario, factory_name in zip(args.scenarios, args.factories):
        factory = getattr(mod, factory_name, None)
        if factory is None:
            parser.error(f"factory {factory_name!r} not found in {args.example}/main.py")
        bundle[scenario] = dump_graph(factory, scenario)

    out_path = REPO_ROOT / "examples" / "rust" / args.example / "graph.json"
    out_path.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
