"""Every run store, opened empty for a test — the backends the contract runs on.

``files``, ``sqlite`` and ``mongo`` (mongomock) always run; ``postgres`` needs
``OPERONX_TEST_PG_DSN`` and ``clickhouse`` ``OPERONX_TEST_CLICKHOUSE``, each a
throwaway server (see ``_clickhouse.py``), and skip otherwise.
"""

from __future__ import annotations

import os
import uuid

import pytest

#: A real Postgres for the postgres store — e.g. a throwaway container:
#: docker run --rm -d -p 127.0.0.1:55439:5432 -e POSTGRES_PASSWORD=x pgvector/pgvector:pg16
#: OPERONX_TEST_PG_DSN=postgresql://postgres:x@127.0.0.1:55439/postgres
PG_DSN = os.environ.get("OPERONX_TEST_PG_DSN", "")

BACKENDS = [
    "files",
    "sqlite",
    "mongo",
    pytest.param(
        "postgres", marks=pytest.mark.skipif(not PG_DSN, reason="set OPERONX_TEST_PG_DSN")
    ),
    # a throwaway server: see _clickhouse.py; skips when none answers
    "clickhouse",
]

#: Every table a Postgres store creates under its prefix.
PG_TABLES = ("runs", "op_rollups", "records", "live")


def open_backend(kind: str, request, tmp_path, **kw):
    """A fresh, empty store of *kind*, removed when the test ends."""
    if kind == "files":
        from operonx.telemetry.runs.files import FilesRunStore

        return FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    if kind == "sqlite":
        from operonx.telemetry.runs.sqlite import SqliteRunStore

        return SqliteRunStore(path=tmp_path / "runs.sqlite", **kw)
    if kind == "mongo":
        mongomock = pytest.importorskip("mongomock")
        from operonx.telemetry.runs.mongo import MongoRunStore

        return MongoRunStore(
            client=mongomock.MongoClient(),
            database=f"t{uuid.uuid4().hex[:8]}",
            media_dir=tmp_path / "media",
        )
    if kind == "clickhouse":
        from tests.internal.telemetry._clickhouse import open_store

        return open_store(request, tmp_path, **kw)
    from operonx.telemetry.runs.postgres import PostgresRunStore

    prefix = f"t{uuid.uuid4().hex[:8]}_"
    store = PostgresRunStore(PG_DSN, prefix=prefix, media_dir=tmp_path / "media", **kw)

    def drop():
        with store.index._tx() as cur:
            for table in PG_TABLES:
                cur.execute(f"DROP TABLE IF EXISTS {prefix}{table}")

    request.addfinalizer(drop)
    return store
