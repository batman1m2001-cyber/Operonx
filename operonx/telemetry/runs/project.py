"""Which stores a project's runs can be read from — from its own files.

A project says where its runs go in ``operonx.toml``'s ``[tracing]``
(which sinks) and ``resources.yaml`` (how to reach each). A reader that is
not the project — the studio, a script — wants the same answer without
importing the project's code. :func:`project_stores` gives it::

    from operonx.telemetry.runs import project_stores

    for src in project_stores("/srv/callbot"):
        print(src.source, "→", src.describe() if src.readable else src.reason)
    store = next(s for s in project_stores("/srv/callbot") if s.readable).open()
    print(store.list_runs(limit=5).items)

How each sink maps to a store:

* ``"local"`` — the run directories under ``<project>/.operonx/runs``
  (or ``OPERONX_RUNS_DIR``), read as a ``files`` store;
* ``trace_local:<n>`` — the same, at that consumer's ``root``;
* ``trace_clickhouse:<n>`` — ``{backend: clickhouse, ...}`` with the
  consumer's own fields: the consumer *is* the store;
* ``trace_langfuse:<n>`` — the read-only Langfuse store, reached through
  the consumer's ``client_resource`` (``langfuse:<x>``);
* ``run_store:<n>`` — that store, as written;
* anything else (a project's own consumer) has no store that reads what
  it writes: it is returned unreadable, with the reason.

``${VAR}`` and ``${VAR:default}`` resolve against the environment with the
project's ``.env`` under it — what ``operonx.bootstrap()`` does: the file
fills in, the process environment wins. Relative paths anchor where the
writer anchors them: a files ``root`` and a ``media_dir`` at the project,
a sqlite ``path`` under the runs root.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

__all__ = ["StoreSource", "project_stores", "read_dotenv"]

_ENV = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")

#: Where a store keeps blobs when its ``media_dir`` is unset, under the
#: runs root — as each backend's constructor resolves it.
_MEDIA_DEFAULT = {"clickhouse": "media", "postgres": "pg-media", "mongo": "mongo-media"}


@dataclass(frozen=True)
class StoreSource:
    """One trace sink of a project, as a store that can be read.

    ``spec`` is what :func:`~operonx.telemetry.runs.open_run_store` takes,
    or ``None`` when the sink cannot be read — ``reason`` says why.
    ``levels`` are the places in ``operonx.toml`` that name the sink
    (``[tracing]``, ``[tracing.services.call]``, ...); ``source`` says
    the same in one line: ``"[tracing] → trace_clickhouse:default"``."""

    sink: str
    spec: Optional[Dict[str, Any]]
    levels: Tuple[str, ...]
    reason: str = ""

    @property
    def readable(self) -> bool:
        return self.spec is not None

    @property
    def backend(self) -> Optional[str]:
        return str(self.spec.get("backend") or "files") if self.spec is not None else None

    @property
    def source(self) -> str:
        return f"{', '.join(self.levels)} → {self.sink}"

    def describe(self) -> str:
        """The store in a few words, with no credentials:
        ``"ClickHouse callbot_traces at 192.168.1.12:8123"``."""
        if self.spec is None:
            return f"{self.sink} (unreadable: {self.reason})"
        return describe_spec(self.spec)

    def open(self) -> Any:
        """The :class:`~operonx.telemetry.runs.RunStore` itself."""
        if self.spec is None:
            raise ValueError(f"{self.sink} cannot be read: {self.reason}")
        from .config import open_run_store

        return open_run_store(self.spec)


def describe_spec(spec: Mapping[str, Any]) -> str:
    """A store spec in a few words, with no credentials."""
    backend = str(spec.get("backend") or "files")
    if backend == "files":
        return f"files at {spec.get('root') or '.operonx/runs'}"
    if backend == "sqlite":
        return f"SQLite {spec.get('path') or 'runs.sqlite'}"
    if backend == "clickhouse":
        port = f":{spec['port']}" if spec.get("port") else ""
        return f"ClickHouse {spec.get('database') or 'operonx'} at {spec.get('host')}{port}"
    if backend == "postgres":
        u = urlparse(str(spec.get("dsn") or ""))
        where = _hostport(u.netloc)
        db = u.path.lstrip("/") or "postgres"
        return f"Postgres {db} at {where}" if where else f"Postgres {db}"
    if backend == "mongo":
        where = _hostport(urlparse(str(spec.get("uri") or "")).netloc)
        db = spec.get("database") or "operonx"
        return f"MongoDB {db} at {where}" if where else f"MongoDB {db}"
    if backend == "langfuse":
        return f"Langfuse at {spec.get('host')}"
    return backend


def _hostport(netloc: str) -> str:
    return netloc.rsplit("@", 1)[-1]


# ── .env and ${VAR} ──────────────────────────────────────────────────────


def read_dotenv(path: Any) -> Dict[str, str]:
    """``KEY=value`` lines of a ``.env`` file, as python-dotenv reads them
    for the common cases: comments, blank lines, ``export KEY=``, single
    or double quotes, a `` #`` comment after an unquoted value. A missing
    or unreadable file is empty."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _KEY.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = value.replace("\\n", "\n").replace('\\"', '"')
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        out[key] = value
    return out


def _interpolate(value: Any, env: Mapping[str, str], missing: List[str]) -> Any:
    if isinstance(value, str):

        def sub(m: "re.Match[str]") -> str:
            name, default = m.group(1), m.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            missing.append(name)
            return m.group(0)

        return _ENV.sub(sub, value)
    if isinstance(value, dict):
        return {k: _interpolate(v, env, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, env, missing) for v in value]
    return value


# ── the project's files ──────────────────────────────────────────────────


def _toml(path: Path) -> Dict[str, Any]:
    try:
        import tomllib as toml
    except ModuleNotFoundError:  # pragma: no cover — 3.10
        import tomli as toml  # type: ignore[no-redef]
    return toml.loads(path.read_text(encoding="utf-8"))


def _resources(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    import yaml

    from operonx.core.registry.storage.yaml import flatten_resources

    return flatten_resources(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _sinks_in_use(raw: Dict[str, Any], where: str) -> List[Tuple[str, str]]:
    """``(sink, level)`` for every sink the file has a run go to."""
    from operonx.app.tracing import parse_tracing

    tracing = parse_tracing(raw.get("tracing"), where)
    if tracing is None:
        return []
    out: List[Tuple[str, str]] = [(s, "[tracing]") for s in tracing.sinks or ()]
    for kind, table in (("services", tracing.services), ("jobs", tracing.jobs)):
        for name, sinks in table.items():
            out.extend((s, f"[tracing.{kind}.{name}]") for s in sinks)
    # a [[serve]] / [[job]] block's own `trace =`, where no [tracing.*]
    # entry overrides it
    for block_key, table, label in (
        ("serve", tracing.services, "[[serve]]"),
        ("job", tracing.jobs, "[[job]]"),
    ):
        blocks = raw.get(block_key) or []
        for block in blocks if isinstance(blocks, list) else [blocks]:
            if not isinstance(block, dict) or "trace" not in block:
                continue
            name = str(block.get("name") or "")
            if name in table:
                continue
            own = block["trace"] if isinstance(block["trace"], list) else [block["trace"]]
            out.extend((str(s), f"{label} {name}".rstrip()) for s in own)
    return out


class _Resolver:
    def __init__(
        self, root: Path, resources: Dict[str, Any], res_name: str, env: Mapping[str, str]
    ):
        self.root = root
        self.resources = resources
        self.res_name = res_name
        self.env = env
        runs = env.get("OPERONX_RUNS_DIR")
        self.runs_root = self.anchor(runs) if runs else root / ".operonx" / "runs"

    def anchor(self, value: Any, base: Optional[Path] = None) -> Path:
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else (base or self.root) / path

    def config(self, key: str) -> Tuple[Optional[Dict[str, Any]], str]:
        if key not in self.resources:
            return None, f"{key} is not in {self.res_name}"
        cfg = self.resources[key]
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            return None, f"{key} in {self.res_name} is not a mapping"
        missing: List[str] = []
        cfg = _interpolate(dict(cfg), self.env, missing)
        if missing:
            names = ", ".join("${" + m + "}" for m in dict.fromkeys(missing))
            return None, f"{key} needs {names} — set it in .env or the environment"
        return cfg, ""

    def store(self, sink: str) -> Tuple[Optional[Dict[str, Any]], str]:
        from operonx.app.tracing import LOCAL

        if sink == LOCAL:
            return {"backend": "files", "root": str(self.runs_root)}, ""
        category = sink.split(":", 1)[0]
        if category == "trace_local":
            cfg, why = self.config(sink)
            if cfg is None:
                return None, why
            root = self.anchor(cfg["root"]) if cfg.get("root") else self.runs_root
            spec = {"backend": "files", "root": str(root)}
            if cfg.get("layout"):
                spec["layout"] = str(cfg["layout"])
            return spec, ""
        if category == "trace_clickhouse":
            cfg, why = self.config(sink)
            if cfg is None:
                return None, why
            return self.settle({**cfg, "backend": "clickhouse"}), ""
        if category == "trace_langfuse":
            cfg, why = self.config(sink)
            if cfg is None:
                return None, why
            client = str(cfg.get("client_resource") or "")
            if not client:
                return None, f"{sink} has no client_resource"
            lf, why = self.config(client)
            if lf is None:
                return None, f"{sink}: {why}"
            missing = [k for k in ("public_key", "secret_key") if not lf.get(k)]
            if missing:
                return None, f"{client} has no {', '.join(missing)}"
            host = str(lf.get("host") or "https://cloud.langfuse.com").rstrip("/")
            return {
                "backend": "langfuse",
                "host": host,
                "public_key": str(lf["public_key"]),
                "secret_key": str(lf["secret_key"]),
            }, ""
        if category == "run_store":
            cfg, why = self.config(sink)
            if cfg is None:
                return None, why
            return self.settle({"backend": "files", **cfg}), ""
        return None, (
            f"{category!r} is the project's own consumer — no run store reads what it writes"
        )

    def settle(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Anchor a store spec's paths where its writer anchors them."""
        backend = str(spec.get("backend") or "files")
        if backend == "files":
            spec["root"] = str(self.anchor(spec["root"]) if spec.get("root") else self.runs_root)
        elif backend == "sqlite":
            path = spec.get("path")
            spec["path"] = str(
                self.anchor(path, self.runs_root) if path else self.runs_root / "runs.sqlite"
            )
        elif backend in _MEDIA_DEFAULT:
            media = spec.get("media_dir")
            spec["media_dir"] = str(
                self.anchor(media) if media else self.runs_root / _MEDIA_DEFAULT[backend]
            )
        return spec


def project_stores(root: Any, env: Optional[Mapping[str, str]] = None) -> List[StoreSource]:
    """One :class:`StoreSource` per trace sink the project's ``operonx.toml``
    names in ``[tracing]`` — the project-wide list, every
    ``[tracing.services.<n>]`` and ``[tracing.jobs.<n>]``, and the
    ``trace =`` of any ``[[serve]]`` / ``[[job]]`` block none of those
    overrides — each sink once, in that order.

    ``env`` is the environment to resolve ``${VAR}`` in (default: this
    process's); the project's ``.env`` fills in what it lacks. An empty
    list when there is no manifest or no ``[tracing]`` table. A malformed
    ``[tracing]`` raises :class:`~operonx.app.manifest.ManifestError`."""
    # absolute: a relative spec would resolve against whichever process opens it
    root = Path(root).expanduser().absolute()
    path = root / "operonx.toml"
    if not path.is_file():
        return []
    merged: Dict[str, str] = {
        **read_dotenv(root / ".env"),
        **dict(os.environ if env is None else env),
    }
    raw = _interpolate(_toml(path), merged, [])
    if raw.get("tracing") is None:
        return []
    used = _sinks_in_use(raw, str(path))
    if not used:
        return []
    overlay = (
        (raw.get("resources") or {}).get("overlay")
        if isinstance(raw.get("resources"), dict)
        else None
    )
    res_path = root / str(overlay or "resources.yaml")
    resolver = _Resolver(root, _resources(res_path), str(overlay or "resources.yaml"), merged)
    levels: Dict[str, List[str]] = {}
    for sink, level in used:
        have = levels.setdefault(sink, [])
        if level not in have:
            have.append(level)
    out = []
    for sink, where in levels.items():
        spec, reason = resolver.store(sink)
        out.append(StoreSource(sink=sink, spec=spec, levels=tuple(where), reason=reason))
    return out
