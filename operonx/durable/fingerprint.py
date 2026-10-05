"""A compiled graph's structural fingerprint — what a resume checks.

A journal names executions by ``(op_full_name, ctx)``. Those names mean the
same thing on resume only if the graph is the same: the same ops, of the
same types, running the same code, wired the same way. The fingerprint
hashes exactly that, recursively through subgraphs and the synthetic loops
the cycle rewrite makes — so, unlike the evals' ``graph_hash`` (a
``GraphOp.serialize()``, which refuses loops), it covers every graph.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any, Dict, List

__all__ = ["graph_fingerprint"]


def graph_fingerprint(graph: Any) -> str:
    """32 hex characters naming *graph*'s structure and code. Names are
    relative to the root, which takes its engine variable's name."""
    root = getattr(graph, "full_name", "") or ""
    text = json.dumps(_describe(graph, root), sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(text.encode(), digest_size=16).hexdigest()


def _describe(node: Any, root: str) -> Dict[str, Any]:
    name = getattr(node, "full_name", "") or ""
    out: Dict[str, Any] = {
        "name": name[len(root) :] if root and name.startswith(root) else name,
        "type": str(getattr(node, "type", type(node).__name__)),
        "code": _code(getattr(node, "core", None)),
    }
    ops = getattr(node, "_ops", None)
    if ops:
        out["ops"] = {key: _describe(op, root) for key, op in sorted(ops.items())}
        out["edges"] = {
            src: sorted([link.dst, bool(link.soft)] for link in links)
            for src, links in sorted((getattr(node, "_adj", None) or {}).items())
        }
        out["error_edges"] = sorted((getattr(node, "_err_adj", None) or {}).keys())
        out["entries"] = sorted(str(e) for e in getattr(node, "entries", ()) or ())
    return out


def _code(fn: Any) -> List[str]:
    """The callable's qualified name and a hash of its source (its class's,
    for a bound method). A callable with no source is known by name."""
    if fn is None:
        return []
    target = inspect.unwrap(fn)
    holder = getattr(target, "__func__", target)
    name = f"{getattr(holder, '__module__', '')}.{getattr(holder, '__qualname__', repr(holder))}"
    try:
        source = inspect.getsource(holder)
    except (OSError, TypeError):
        return [name]
    return [name, hashlib.blake2b(source.encode(), digest_size=8).hexdigest()]
