"""``run_store:`` in ``resources.yaml`` — a store is a resource like any other.

::

    run_store:
      default:
        backend: files            # files | sqlite | postgres | mongo | langfuse | clickhouse
        root: ""                  # files: unset → <project>/.operonx/runs
      archive:
        backend: sqlite
        path: /data/runs.sqlite
      team:
        backend: postgres         # one database every service and the studio share
        dsn: ${RUNS_PG_DSN}
      docs:
        backend: mongo
        uri: ${RUNS_MONGO_URI}
        database: operonx
      events:
        backend: clickhouse       # many runs, many writers
        host: ${CLICKHOUSE_HOST}
        user: ${CLICKHOUSE_USER}
        password: ${CLICKHOUSE_PASSWORD}
        database: operonx
        media: clickhouse         # blobs in the database too; local (default): media_dir
        timeout: 10               # connect timeout, seconds
      remote:
        backend: langfuse
        host: ${LANGFUSE_HOST}
        public_key: ${LANGFUSE_PUBLIC_KEY}
        secret_key: ${LANGFUSE_SECRET_KEY}

Because a store is a trace consumer, ``trace=["run_store:default"]``
records into it directly. Backends import lazily, so declaring a store
pulls in no driver its backend does not use.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from operonx.core.utils.yaml_model import YamlModel

from .base import RunStore

__all__ = ["BACKENDS", "RunStoreConfig", "create_run_store", "open_run_store"]

BACKENDS = ("files", "sqlite", "postgres", "mongo", "langfuse", "clickhouse")


class RunStoreConfig(YamlModel):
    """YAML-configurable :class:`RunStore`."""

    _category: ClassVar[str] = "run_store"

    backend: str = "files"  # files | sqlite | postgres | mongo | langfuse | clickhouse
    # files
    root: str = ""
    layout: str = "origin"
    # sqlite
    path: str = ""
    # postgres
    dsn: str = ""
    prefix: str = ""
    # mongo
    uri: str = ""
    database: str = "operonx"
    # postgres, mongo, clickhouse: where large payloads go
    media_dir: str = ""
    # langfuse (read-only), clickhouse
    host: str = ""
    public_key: str = ""
    secret_key: str = ""
    # clickhouse (``database`` above too)
    port: int = 0
    user: str = ""
    password: str = ""
    secure: bool = False
    ttl_days: Optional[float] = None
    media_threshold: int = 1024
    batch_size: int = 10000
    flush_interval: float = 1.0
    queue_size: int = 1000
    timeout: float = 10.0  # clickhouse: connect timeout, seconds
    media: str = "local"  # clickhouse: where blobs go — local (media_dir) | clickhouse
    live: bool = True  # sqlite, postgres, clickhouse: write each run while it goes


def open_run_store(spec: Optional[Dict[str, Any]] = None) -> RunStore:
    """A store from a plain mapping (the YAML block's fields). The studio
    opens a project's store this way without importing the project."""
    spec = dict(spec or {})
    backend = str(spec.get("backend") or "files")
    if backend == "files":
        from .files import FilesRunStore

        return FilesRunStore(root=spec.get("root") or "", layout=spec.get("layout") or "origin")
    if backend == "sqlite":
        from .sqlite import SqliteRunStore

        return SqliteRunStore(path=spec.get("path") or "", live=_flag(spec.get("live", True)))
    if backend == "postgres":
        from .postgres import PostgresRunStore

        if not spec.get("dsn"):
            raise ValueError("run_store backend 'postgres' needs dsn")
        return PostgresRunStore(
            spec["dsn"],
            prefix=spec.get("prefix") or "operonx_",
            media_dir=spec.get("media_dir") or "",
            live=_flag(spec.get("live", True)),
        )
    if backend == "mongo":
        from .mongo import MongoRunStore

        if not spec.get("uri"):
            raise ValueError("run_store backend 'mongo' needs uri")
        return MongoRunStore(
            spec["uri"],
            database=spec.get("database") or "operonx",
            prefix=spec.get("prefix") or "",
            media_dir=spec.get("media_dir") or "",
        )
    if backend == "langfuse":
        from .langfuse import LangfuseRunStore

        missing = [k for k in ("host", "public_key", "secret_key") if not spec.get(k)]
        if missing:
            raise ValueError(f"run_store backend 'langfuse' needs {', '.join(missing)}")
        return LangfuseRunStore(spec["host"], spec["public_key"], spec["secret_key"])
    if backend == "clickhouse":
        from .clickhouse import ClickHouseRunStore

        if not spec.get("host"):
            raise ValueError("run_store backend 'clickhouse' needs host")
        ttl = spec.get("ttl_days")
        return ClickHouseRunStore(
            host=spec["host"],
            port=int(spec.get("port") or 0),
            user=spec.get("user") or "default",
            password=spec.get("password") or "",
            database=spec.get("database") or "operonx",
            secure=_flag(spec.get("secure")),
            ttl_days=None if ttl in (None, "") else float(ttl),
            media_dir=spec.get("media_dir") or "",
            media_threshold=int(spec.get("media_threshold") or 1024),
            batch_size=int(spec.get("batch_size") or 10000),
            flush_interval=float(spec.get("flush_interval") or 1.0),
            queue_size=int(spec.get("queue_size") or 1000),
            timeout=float(spec.get("timeout") or 10.0),
            media=str(spec.get("media") or "local"),
            live=_flag(spec.get("live", True)),
        )
    raise ValueError(f"unknown run_store backend {backend!r}; one of {', '.join(BACKENDS)}")


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def create_run_store(cfg: RunStoreConfig) -> RunStore:
    """The hub's factory for ``run_store:`` entries."""
    return open_run_store(
        {
            "backend": cfg.backend,
            "root": cfg.root,
            "layout": cfg.layout,
            "path": cfg.path,
            "dsn": cfg.dsn,
            "prefix": cfg.prefix,
            "uri": cfg.uri,
            "database": cfg.database,
            "media_dir": cfg.media_dir,
            "host": cfg.host,
            "public_key": cfg.public_key,
            "secret_key": cfg.secret_key,
            "port": cfg.port,
            "user": cfg.user,
            "password": cfg.password,
            "secure": cfg.secure,
            "ttl_days": cfg.ttl_days,
            "media_threshold": cfg.media_threshold,
            "batch_size": cfg.batch_size,
            "flush_interval": cfg.flush_interval,
            "queue_size": cfg.queue_size,
            "timeout": cfg.timeout,
            "media": cfg.media,
            "live": cfg.live,
        }
    )
