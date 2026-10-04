"""Which score store a project's experiments live in — from its own files.

``operonx eval``, the studio and a baseline lookup all need the answer
without importing the project's code, so it is read the way
:func:`~operonx.telemetry.runs.project_stores` reads where runs go::

    from operonx.telemetry.scores import project_score_store

    src = project_score_store("/srv/callbot")
    print(src.source, "→", src.describe())
    store = src.open()

In order:

1. ``[evals] scores = "score_store:<name>"`` in ``operonx.toml`` — that
   entry of ``resources.yaml``;
2. else the first ``trace_clickhouse:`` sink (or a ``run_store:`` whose
   backend is ``clickhouse``) in the project-wide ``[tracing] sinks`` — a
   ClickHouse score store on the same connection: the runs' database,
   which holds the score tables since schema version 3. It wins over
   ``local`` when both are listed: experiments are compared across
   machines (an MR's CI against main's), and ClickHouse is where a project
   that uses it keeps what it shares;
3. else ``files`` under ``<runs root>/scores`` — where ``local`` keeps the
   runs (``$OPERONX_RUNS_DIR``, else ``<project>/.operonx/runs``).

``[evals]`` has the one key ``scores``. Relative paths anchor where the
store anchors them: a files ``root`` and a sqlite ``path`` under the runs
root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = ["ScoreStoreSource", "project_score_store"]

#: The ``[evals]`` table's keys.
EVALS_KEYS = ("scores",)

#: What a ClickHouse score store takes from a run sink's connection.
_CONNECTION = ("host", "port", "user", "password", "database", "secure", "timeout")


@dataclass(frozen=True)
class ScoreStoreSource:
    """The project's score store, as a spec :func:`open_score_store` takes
    — or ``None`` with the ``reason`` it cannot be opened. ``source`` says
    which setting chose it."""

    spec: Optional[Dict[str, Any]]
    source: str
    reason: str = ""

    @property
    def openable(self) -> bool:
        return self.spec is not None

    def describe(self) -> str:
        """The store in a few words, with no credentials."""
        from operonx.telemetry.runs.project import describe_spec

        if self.spec is None:
            return f"unopenable ({self.reason})"
        if self.spec.get("backend") == "sqlite":
            return f"SQLite {self.spec['path']}"
        return describe_spec(self.spec)

    def open(self) -> Any:
        """The :class:`~operonx.telemetry.scores.ScoreStore` itself."""
        if self.spec is None:
            raise ValueError(f"the project's score store ({self.source}): {self.reason}")
        from .config import open_score_store

        return open_score_store(self.spec)


def _evals_table(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    from operonx.app.manifest import ManifestError

    table = raw.get("evals")
    if table is None:
        return {}
    if not isinstance(table, dict):
        raise ManifestError(f"{where}: [evals] is a table")
    unknown = sorted(set(table) - set(EVALS_KEYS))
    if unknown:
        raise ManifestError(
            f"{where}: [evals] has keys it does not read: {unknown} "
            f'(it reads {list(EVALS_KEYS)}: scores = "score_store:<name>")'
        )
    scores = table.get("scores")
    if scores is not None and not (isinstance(scores, str) and scores.startswith("score_store:")):
        raise ManifestError(
            f"{where}: [evals] scores = {scores!r}; expected a 'score_store:<name>' key "
            "from resources.yaml"
        )
    return dict(table)


def _settle(spec: Dict[str, Any], resolver: Any) -> Dict[str, Any]:
    """Anchor a files root and a sqlite path under the runs root."""
    backend = str(spec.get("backend") or "files")
    if backend == "files":
        root = spec.get("root")
        spec["root"] = str(
            resolver.anchor(root, resolver.runs_root) if root else resolver.runs_root / "scores"
        )
    elif backend == "sqlite":
        path = spec.get("path")
        spec["path"] = str(
            resolver.anchor(path, resolver.runs_root)
            if path
            else resolver.runs_root / "scores.sqlite"
        )
    return spec


def _clickhouse_sink(raw: Mapping[str, Any], resolver: Any, where: str) -> Optional[Tuple]:
    """``(sink, config or None, reason)`` of the first ClickHouse sink in
    the project-wide ``[tracing] sinks``, or ``None`` when there is none."""
    from operonx.app.tracing import parse_tracing

    tracing = parse_tracing(raw.get("tracing"), where)
    for sink in (tracing.sinks if tracing is not None else None) or ():
        category = sink.split(":", 1)[0]
        if category not in ("trace_clickhouse", "run_store"):
            continue
        cfg, why = resolver.config(sink)
        if category == "run_store" and (cfg is None or cfg.get("backend") != "clickhouse"):
            continue
        return sink, cfg, why
    return None


def project_score_store(root: Any, env: Optional[Mapping[str, str]] = None) -> ScoreStoreSource:
    """The project's score store (see the module docstring). ``env`` is
    the environment ``${VAR}`` resolves in (default: this process's, with
    the project's ``.env`` under it). A malformed ``[evals]`` or
    ``[tracing]`` raises :class:`~operonx.app.manifest.ManifestError`."""
    from operonx.telemetry.runs.project import project_files

    raw, resolver = project_files(root, env)
    where = str(resolver.root / "operonx.toml")
    raw = raw or {}
    key = _evals_table(raw, where).get("scores")
    if key:
        source = f"[evals] scores → {key}"
        cfg, why = resolver.config(key)
        if cfg is None:
            return ScoreStoreSource(None, source, why)
        return ScoreStoreSource(_settle({"backend": "files", **cfg}, resolver), source)
    found = _clickhouse_sink(raw, resolver, where)
    if found is not None:
        sink, cfg, why = found
        source = f"[tracing] → {sink}"
        if cfg is None:
            return ScoreStoreSource(None, source, why)
        spec = {"backend": "clickhouse", **{k: cfg[k] for k in _CONNECTION if k in cfg}}
        return ScoreStoreSource(spec, source)
    spec = {"backend": "files", "root": str(resolver.runs_root / "scores")}
    return ScoreStoreSource(spec, "default: files under the runs root")
