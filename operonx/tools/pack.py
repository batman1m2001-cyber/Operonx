"""``operonx-pack`` — serialise ``@graph`` factories to the JSON spec
consumed by the Rust runtime.

Picks targets pytest-style: each positional argument is
``<dotted.module>::<symbol>`` where ``symbol`` is a ``@graph``-decorated
function (or any callable returning a ``GraphOp``). The factory is
called with ``None`` for every parameter — those become PARENT external
inputs in the serialised graph rather than literals.

Output goes to **stdout** by default; pass ``-o <path>`` to write a file.
When more than one target is supplied, the result is a JSON object whose
keys are the symbol names and whose values are the per-target specs.

Usage::

    # Single target — print to stdout
    operonx-pack examples.python.ex03_llm_chat.main::basic_chat

    # Multiple targets bundled into one file
    operonx-pack \\
        examples.python.ex03_llm_chat.main::basic_chat \\
        examples.python.ex03_llm_chat.main::chain_chat \\
        examples.python.ex03_llm_chat.main::summarize_pipeline \\
        -o examples/rust/ex03_llm_chat/graph.json

The script ``operonx.bootstrap()`` from the current working directory so
provider ops can resolve ``./resources.yaml`` + ``./.env`` at build
time. Run from inside the example directory when targeting a graph that
needs provider resources.

Ships with ``pip install operonx`` — registered via the
``[project.scripts] operonx-pack = "operonx.tools.pack:main"`` entry
point.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Callable

# Keys Python adds at serialise time that we either can't (Rust rejects)
# or shouldn't (contain live secrets) persist to disk.
DROP_KEYS = {
    "python_callable",
    "resource_config",
    "resource_configs",
    "fallback_configs",
}

# Belt-and-braces: even after dropping the cache fields above, redact
# any secret-ish value that happens to show up elsewhere. Harmless when
# the strip is already complete.
SENSITIVE_KEYS = {
    "api_key",
    "secret_key",
    "access_token",
    "refresh_token",
    "private_key",
    "private_key_id",
}


def _scrub(node: Any) -> Any:
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


def _split_target(target: str) -> tuple[str, str]:
    """``"pkg.mod::factory"`` → ``("pkg.mod", "factory")``."""
    if "::" not in target:
        raise SystemExit(
            f"target {target!r} must be of the form 'module.path::symbol' "
            "(pytest-style)."
        )
    module, symbol = target.split("::", 1)
    if not module or not symbol:
        raise SystemExit(
            f"target {target!r} has an empty module or symbol; expected "
            "'module.path::symbol'."
        )
    return module, symbol


def _load_factory(target: str) -> Callable[..., Any]:
    module_path, symbol = _split_target(target)
    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        raise SystemExit(f"failed to import {module_path!r}: {e}") from e
    factory = getattr(mod, symbol, None)
    if factory is None:
        raise SystemExit(f"symbol {symbol!r} not found in {module_path!r}")
    if not callable(factory):
        raise SystemExit(f"{target!r} is not callable")
    return factory


def pack_one(factory: Callable[..., Any], scenario: str) -> dict:
    """Call ``factory`` with ``None`` for every parameter, build the
    resulting GraphOp, serialise + scrub.

    The ``None`` placeholders make every parameter an external input
    (resolved at ``engine.run`` time) rather than a literal embedded in
    the spec.
    """
    sig = inspect.signature(factory)
    kwargs = {p: None for p in sig.parameters}
    graph = factory(**kwargs, name=scenario)
    if hasattr(graph, "build"):
        graph.build()
    if not hasattr(graph, "serialize"):
        raise SystemExit(
            f"{factory.__module__}.{factory.__qualname__} did not return "
            "a GraphOp (no `.serialize()`)."
        )
    cleaned = _scrub(graph.serialize())
    cleaned["schema_version"] = "1.0"
    return cleaned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx-pack",
        description=(
            "Serialise @graph factories to the JSON spec the Rust runtime "
            "loads. Pass one or more `module.path::symbol` targets."
        ),
    )
    parser.add_argument(
        "targets",
        nargs="+",
        metavar="MODULE::SYMBOL",
        help="One or more @graph factories, e.g. 'pkg.mod::factory'.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write JSON to PATH instead of stdout.",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help=(
            "Skip `operonx.bootstrap()`. Useful for pure-compute graphs "
            "that don't reference any resources."
        ),
    )
    args = parser.parse_args(argv)

    if not args.no_bootstrap:
        # Lazy import — bootstrap pulls the registry plugins, which we
        # don't need for the no-bootstrap path.
        import operonx

        operonx.bootstrap()

    # Make the current working directory importable so users can target
    # local-only modules (e.g. `main::basic_chat` from inside an example).
    sys.path.insert(0, str(Path.cwd()))

    bundle: dict = {}
    for target in args.targets:
        _, symbol = _split_target(target)
        factory = _load_factory(target)
        bundle[symbol] = pack_one(factory, symbol)

    # Single target → top-level spec; multi-target → keyed bundle.
    payload = next(iter(bundle.values())) if len(bundle) == 1 else bundle
    text = json.dumps(payload, indent=2, ensure_ascii=False)

    if args.output is None:
        sys.stdout.write(text + "\n")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
