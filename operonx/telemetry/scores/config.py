"""``score_store:`` in ``resources.yaml`` — a score store is a resource like any other.

::

    score_store:
      default:
        backend: files            # files | sqlite | clickhouse
        root: ""                  # files: unset → <runs root>/scores
      team:
        backend: clickhouse       # the database the runs are in: schema v3
        host: ${CLICKHOUSE_HOST}
        user: ${CLICKHOUSE_USER}
        password: ${CLICKHOUSE_PASSWORD}
        database: operonx
        online_ttl_days: 365

Backends import lazily, so declaring a store pulls in no driver its
backend does not use.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from operonx.core.utils.yaml_model import YamlModel

from .base import ONLINE_TTL_DAYS, ScoreStore

__all__ = ["BACKENDS", "ScoreStoreConfig", "create_score_store", "open_score_store"]

BACKENDS = ("files", "sqlite", "clickhouse")


class ScoreStoreConfig(YamlModel):
    """YAML-configurable :class:`ScoreStore`."""

    _category: ClassVar[str] = "score_store"

    backend: str = "files"  # files | sqlite | clickhouse
    root: str = ""  # files
    path: str = ""  # sqlite
    online_ttl_days: float = ONLINE_TTL_DAYS
    # clickhouse
    host: str = ""
    port: int = 0
    user: str = ""
    password: str = ""
    database: str = "operonx"
    secure: bool = False
    timeout: float = 10.0


def open_score_store(spec: Optional[Dict[str, Any]] = None) -> ScoreStore:
    """A store from a plain mapping (the YAML block's fields)."""
    spec = dict(spec or {})
    backend = str(spec.get("backend") or "files")
    ttl = float(spec.get("online_ttl_days") or ONLINE_TTL_DAYS)
    if backend == "files":
        from .files import FilesScoreStore

        return FilesScoreStore(root=spec.get("root") or "", online_ttl_days=ttl)
    if backend == "sqlite":
        from .sqlite import SqliteScoreStore

        return SqliteScoreStore(path=spec.get("path") or "", online_ttl_days=ttl)
    if backend == "clickhouse":
        from operonx.telemetry.runs.config import _flag

        from .clickhouse import ClickHouseScoreStore

        if not spec.get("host"):
            raise ValueError("score_store backend 'clickhouse' needs host")
        return ClickHouseScoreStore(
            host=spec["host"],
            port=int(spec.get("port") or 0),
            user=spec.get("user") or "default",
            password=spec.get("password") or "",
            database=spec.get("database") or "operonx",
            secure=_flag(spec.get("secure")),
            timeout=float(spec.get("timeout") or 10.0),
            online_ttl_days=ttl,
        )
    raise ValueError(f"unknown score_store backend {backend!r}; one of {', '.join(BACKENDS)}")


def create_score_store(cfg: ScoreStoreConfig) -> ScoreStore:
    """The hub's factory for ``score_store:`` entries."""
    return open_score_store(cfg.model_dump())
