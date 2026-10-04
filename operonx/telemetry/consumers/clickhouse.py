"""``trace_clickhouse:`` — record runs into ClickHouse from ``resources.yaml``.

::

    trace_clickhouse:
      default:
        host: ${CLICKHOUSE_HOST:localhost}
        port: 8123                  # 8443 with secure: true
        user: ${CLICKHOUSE_USER:default}
        password: ${CLICKHOUSE_PASSWORD:}
        database: operonx
        ttl_days:                   # unset: per origin; 0 = keep forever
        media: local                # local: blobs in media_dir; clickhouse: in the database
        media_dir: /data/operonx-media

    Operon(graph, trace=["trace_langfuse:edupia", "trace_clickhouse:default"])

The consumer **is** a :class:`~operonx.telemetry.runs.clickhouse.ClickHouseRunStore`:
its ``consume`` queues the run and returns, a background thread writes.
``run_store: {backend: clickhouse, ...}`` with the same fields opens the
same store for reading (what the studio does). Nothing here imports the
driver: that happens when the first batch is written.
"""

from __future__ import annotations

from typing import Any, ClassVar, Optional

from operonx.core.utils.yaml_model import YamlModel

__all__ = ["ClickHouseConsumerConfig"]


class ClickHouseConsumerConfig(YamlModel):
    """YAML-configurable ClickHouse trace consumer."""

    _category: ClassVar[str] = "trace_clickhouse"

    host: str = "localhost"
    port: int = 0  # 0: 8123, or 8443 when secure
    user: str = "default"
    password: str = ""
    database: str = "operonx"
    secure: bool = False
    ttl_days: Optional[float] = None
    media_dir: str = ""
    media_threshold: int = 1024
    batch_size: int = 10000
    flush_interval: float = 1.0
    queue_size: int = 1000
    timeout: float = 10.0
    media: str = "local"  # local (media_dir) | clickhouse (the media table)
    live: bool = True  # write each run while it goes (status "running")


def _create_clickhouse_consumer(cfg: ClickHouseConsumerConfig) -> Any:
    from operonx.telemetry.runs.clickhouse import ClickHouseRunStore

    return ClickHouseRunStore(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        secure=cfg.secure,
        ttl_days=cfg.ttl_days,
        media_dir=cfg.media_dir,
        media_threshold=cfg.media_threshold,
        batch_size=cfg.batch_size,
        flush_interval=cfg.flush_interval,
        queue_size=cfg.queue_size,
        timeout=cfg.timeout,
        media=cfg.media or "local",
        live=cfg.live,
    )
