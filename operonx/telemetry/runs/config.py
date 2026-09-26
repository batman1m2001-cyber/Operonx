"""``run_store:`` in ``resources.yaml`` — a store is a resource like any other.

::

    run_store:
      default:
        backend: files            # files | sqlite | postgres | mongo | langfuse
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

BACKENDS = ("files", "sqlite", "postgres", "mongo", "langfuse")


class RunStoreConfig(YamlModel):
    """YAML-configurable :class:`RunStore`."""

    _category: ClassVar[str] = "run_store"

    backend: str = "files"
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
    # postgres, mongo: where large payloads go
    media_dir: str = ""
    # langfuse (read-only)
    host: str = ""
    public_key: str = ""
    secret_key: str = ""


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

        return SqliteRunStore(path=spec.get("path") or "")
    if backend == "postgres":
        from .postgres import PostgresRunStore

        if not spec.get("dsn"):
            raise ValueError("run_store backend 'postgres' needs dsn")
        return PostgresRunStore(spec["dsn"], prefix=spec.get("prefix") or "operonx_",
                                media_dir=spec.get("media_dir") or "")
    if backend == "mongo":
        from .mongo import MongoRunStore

        if not spec.get("uri"):
            raise ValueError("run_store backend 'mongo' needs uri")
        return MongoRunStore(spec["uri"], database=spec.get("database") or "operonx",
                             prefix=spec.get("prefix") or "", media_dir=spec.get("media_dir") or "")
    if backend == "langfuse":
        from .langfuse import LangfuseRunStore

        missing = [k for k in ("host", "public_key", "secret_key") if not spec.get(k)]
        if missing:
            raise ValueError(f"run_store backend 'langfuse' needs {', '.join(missing)}")
        return LangfuseRunStore(spec["host"], spec["public_key"], spec["secret_key"])
    raise ValueError(f"unknown run_store backend {backend!r}; one of {', '.join(BACKENDS)}")


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
        }
    )
