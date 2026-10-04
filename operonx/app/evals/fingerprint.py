"""An experiment's identity: what produced its numbers.

Two eval runs are comparable when they ran the same cases with the same
evaluators; the difference between them is meant to be the system under
test. The fingerprint records each of those, as short hashes::

    code_version, version_dirty   the git commit at the eval's root, and
                                  whether the tree had changes no commit holds
    graph_hash                    the graph's topology, literal params and
                                  inline prompts, and each op's source
    config_hash                   the resolved resources its ops use (model,
                                  temperature, endpoint) — secrets left out
    dataset_version               the cases, sorted by id
    evaluators, evaluators_hash   each evaluator's version, and all of them
    operonx_version               the engine

Every hash is sha256 over canonical JSON (sorted keys, no whitespace),
cut to 12 hex characters. Nothing here runs per case.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import inspect
import json
import math
from collections.abc import Mapping
from pathlib import Path, PurePath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union
from urllib.parse import urlsplit, urlunsplit

__all__ = [
    "case_hash",
    "config_spec",
    "dataset_version",
    "digest",
    "evaluator_version",
    "fingerprint",
    "graph_spec",
]

#: Where ``serialize()`` puts an op's *resolved* resource configs. They
#: are the config hash's input, not the graph hash's: a model swap in
#: resources.yaml is a config change.
RESOURCE_KEYS = ("resource_config", "resource_configs", "fallback_configs")

#: Config keys that hold credentials. They are dropped, not redacted: a
#: rotated key is the same configuration.
SECRET_NAMES = {
    "access_token",
    "api_token",
    "apikey",
    "auth_token",
    "authorization",
    "bearer_token",
    "cookie",
    "credentials",
    "id_token",
    "passwd",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
    "token",
}
SECRET_SUFFIXES = ("_key", "_key_id", "_secret", "_password", "_passwd")


# ── canonical JSON ───────────────────────────────────────────────────────


def _source_digest(fn: Any) -> Dict[str, Any]:
    """A function as its name and a hash of its source. One with no
    source file (a builtin, a REPL definition) is known by its name."""
    target = inspect.unwrap(fn) if callable(fn) else fn
    name = getattr(target, "__qualname__", None) or type(target).__qualname__
    module = getattr(target, "__module__", None) or type(target).__module__
    holder = target if inspect.isroutine(target) or inspect.isclass(target) else type(target)
    try:
        source = inspect.getsource(holder)
    except (OSError, TypeError):
        return {"callable": f"{module}.{name}"}
    return {"callable": f"{module}.{name}", "source": _sha(source)}


def canonical(value: Any) -> Any:
    """*value* as JSON-ready data that is the same in every process.

    Objects JSON cannot hold become what identifies them: a model's
    fields, a function's source hash, bytes' hash. Anything else is its
    type name — never a ``repr`` with a memory address in it.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(k): canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canonical(v) for v in value), key=_dumps)
    if isinstance(value, enum.Enum):
        return canonical(value.value)
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, (bytes, bytearray)):
        return {"sha256": hashlib.sha256(bytes(value)).hexdigest()}
    if hasattr(value, "model_dump") and not isinstance(value, type):
        return canonical(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return canonical(dataclasses.asdict(value))
    if callable(value):
        return _source_digest(value)
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


def _dumps(value: Any) -> str:
    """Canonical JSON. Plain data (a dataset's rows) goes straight through
    the C encoder; anything else is made plain by :func:`canonical`."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=canonical
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(value: Any) -> str:
    """12 hex characters of sha256 over *value*'s canonical JSON."""
    return _sha(_dumps(value))[:12]


# ── the graph and its resources ──────────────────────────────────────────


def _relative(name: Any, root: str) -> Any:
    """A full name relative to the root graph, whose own name is whatever
    variable held the engine — not part of what the graph is."""
    if not isinstance(name, str):
        return name
    if name == root:
        return ""
    return name[len(root) + 1 :] if name.startswith(root + ".") else name


def _graph_part(node: Any, root: str) -> Any:
    if isinstance(node, list):
        return [_graph_part(v, root) for v in node]
    if not isinstance(node, Mapping):
        return canonical(node)
    out: Dict[str, Any] = {}
    for key, value in node.items():
        if key in RESOURCE_KEYS:
            continue
        if key == "full_name":
            out[key] = _relative(value, root)
        elif key == "ref" and isinstance(value, Mapping):
            out[key] = {
                k: (_relative(v, root) if k == "source" else _graph_part(v, root))
                for k, v in value.items()
            }
        else:
            out[key] = _graph_part(value, root)
    return out


def graph_spec(spec: Mapping) -> Dict[str, Any]:
    """What ``graph_hash`` hashes: a ``GraphOp.serialize()`` without its
    resolved resource configs, names relative to the root, and each
    op's callable as its source hash."""
    root = str(spec.get("full_name") or spec.get("name") or "")
    out = _graph_part(spec, root)
    out["name"] = ""
    return out


def _secret(key: str) -> bool:
    k = key.lower().replace("-", "_")
    return k in SECRET_NAMES or k.endswith(SECRET_SUFFIXES) or "secret" in k or "password" in k


def _scrub_url(text: str) -> str:
    """``scheme://user:pw@host:port/path?key=…`` → ``scheme://host:port/path``."""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.scheme or not parts.hostname:
        return text
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _scrub(node: Any) -> Any:
    if isinstance(node, Mapping):
        return {str(k): _scrub(v) for k, v in node.items() if not _secret(str(k))}
    if isinstance(node, list):
        return [_scrub(v) for v in node]
    if isinstance(node, str) and "://" in node:
        return _scrub_url(node)
    return node


def config_spec(spec: Mapping) -> List[Dict[str, Any]]:
    """What ``config_hash`` hashes: every op's resolved resource configs,
    by op, with credentials dropped and URLs cut to scheme, host and path."""
    root = str(spec.get("full_name") or spec.get("name") or "")
    found: List[Dict[str, Any]] = []

    def walk(node: Mapping) -> None:
        held = {k: _scrub(canonical(node[k])) for k in RESOURCE_KEYS if node.get(k)}
        if held:
            found.append({"op": _relative(node.get("full_name"), root), **held})
        for child in (node.get("ops") or {}).values():
            if isinstance(child, Mapping):
                walk(child)

    walk(spec)
    return sorted(found, key=lambda r: str(r["op"]))


# ── cases and evaluators ─────────────────────────────────────────────────


def case_hash(row: Mapping) -> str:
    """What a case asks and expects: an edited ``expected`` is a new case
    even though its id stayed."""
    return digest({"input": row.get("input"), "expected": row.get("expected")})


def dataset_version(rows: Iterable[Mapping]) -> str:
    """The dataset's content, cases sorted by id."""
    return digest(sorted((dict(r) for r in rows), key=lambda r: str(r.get("id"))))


def evaluator_version(ev: Any) -> str:
    """An evaluator's ``eval_version`` when it declares one; else a hash
    of its source and of what its closure and defaults hold — so
    ``contains("a")`` and ``contains("b")`` differ, and an ``llm_judge``
    whose rubric changed is a new version. An ``@op`` is its body."""
    declared = getattr(ev, "eval_version", None)
    if declared is not None:
        return str(declared)
    fn = getattr(ev, "__wrapped__", ev)
    state: Dict[str, Any] = {"code": _source_digest(fn)}
    code = getattr(fn, "__code__", None)
    if code is not None and getattr(fn, "__closure__", None):
        state["closure"] = {
            name: canonical(cell.cell_contents)
            for name, cell in zip(code.co_freevars, fn.__closure__)
        }
    if getattr(fn, "__defaults__", None):
        state["defaults"] = canonical(fn.__defaults__)
    if getattr(fn, "__kwdefaults__", None):
        state["kwdefaults"] = canonical(fn.__kwdefaults__)
    if not inspect.isroutine(fn) and hasattr(fn, "__dict__"):
        state["fields"] = canonical(vars(fn))
    return digest(state)


# ── the whole fingerprint ────────────────────────────────────────────────


def fingerprint(
    *,
    graph: Any,
    rows: Sequence[Mapping],
    evaluators: Mapping[str, Any],
    root: Union[str, Path, None] = None,
    code: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """The fingerprint of an experiment (see the module docstring).

    *graph* is the compiled root ``GraphOp``; *evaluators* maps each
    evaluator's name to it; *root* is where git is asked for the commit,
    unless *code* already holds what ``origin.code_version`` said (an
    eval asks git in the background, while its cases run). A graph that
    cannot serialize (a cycle-rewritten loop refuses to) has
    ``graph_hash`` and ``config_hash`` ``None`` and says why.
    """
    import operonx
    from operonx.app.origin import code_version

    if code is None:
        code = code_version(Path(root) if root is not None else Path.cwd())
    out: Dict[str, Any] = {
        "code_version": code.get("version"),
        "version_dirty": code.get("version_dirty"),
    }
    try:
        spec = graph.serialize()
    except NotImplementedError as exc:
        out.update(graph_hash=None, config_hash=None, graph_hash_error=str(exc))
    else:
        out.update(graph_hash=digest(graph_spec(spec)), config_hash=digest(config_spec(spec)))
    versions = {name: evaluator_version(ev) for name, ev in evaluators.items()}
    out.update(
        dataset_version=dataset_version(rows),
        evaluators=versions,
        evaluators_hash=digest(sorted(versions.items())),
        operonx_version=operonx.__version__,
    )
    return out
