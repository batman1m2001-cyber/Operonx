"""Where the live ClickHouse tests find a server — or why they skip.

A throwaway local container, never a shared one::

    docker run -d --rm --name ox-ch-test -p 127.0.0.1:18123:8123 \\
        -e CLICKHOUSE_USER=oxtest -e CLICKHOUSE_PASSWORD=oxtest-pw \\
        clickhouse/clickhouse-server
    OPERONX_TEST_CLICKHOUSE=http://oxtest:oxtest-pw@127.0.0.1:18123

Unset, or set but unreachable, every live test skips. Each test works in
a database of its own and drops it.
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import pytest

ENV = "OPERONX_TEST_CLICKHOUSE"


@lru_cache(maxsize=1)
def clickhouse_spec() -> Optional[Dict[str, Any]]:
    """Connection fields from the env URL, if a server answers there."""
    url = os.environ.get(ENV, "")
    if not url:
        return None
    u = urlparse(url)
    spec = {
        "host": u.hostname or "localhost",
        "port": u.port or 8123,
        "user": u.username or "default",
        "password": u.password or "",
        "secure": u.scheme == "https",
    }
    try:
        import clickhouse_connect

        c = clickhouse_connect.get_client(
            host=spec["host"],
            port=spec["port"],
            username=spec["user"],
            password=spec["password"],
            secure=spec["secure"],
            connect_timeout=2,
        )
        c.command("SELECT 1")
        c.close()
    except Exception:  # noqa: BLE001 — unreachable is a skip, not a failure
        return None
    return spec


def why_skip() -> str:
    if not os.environ.get(ENV):
        return f"set {ENV} (a throwaway ClickHouse; see tests/internal/telemetry/_clickhouse.py)"
    return f"{ENV} is set but no ClickHouse answers there"


def open_store(request, tmp_path, **kw):
    """A store on a fresh database, dropped when the test ends."""
    spec = clickhouse_spec()
    if spec is None:
        pytest.skip(why_skip())
    from operonx.telemetry.runs.clickhouse import ClickHouseRunStore

    database = f"t_{uuid.uuid4().hex[:10]}"
    kw.setdefault("media_dir", tmp_path / "media")
    kw.setdefault("ttl_days", 0)
    kw.setdefault("flush_interval", 0.05)
    store = ClickHouseRunStore(database=database, **spec, **kw)

    def drop():
        try:
            store.writer.close(timeout=2)
            store._command(f"DROP DATABASE IF EXISTS {database}")
        finally:
            store.close()

    request.addfinalizer(drop)
    return store
