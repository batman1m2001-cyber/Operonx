"""`project_stores(root)` — the stores a project's trace sinks can be read from.

Read from ``operonx.toml`` ``[tracing]`` and ``resources.yaml`` (with
``${VAR}`` from the project's ``.env``), never by importing the project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operonx.app.manifest import ManifestError
from operonx.telemetry.runs import StoreSource, project_stores
from operonx.telemetry.runs.files import FilesRunStore


def _project(tmp_path: Path, toml: str, resources: str = "", env: str = "") -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "operonx.toml").write_text('[project]\nname = "p"\n\n' + toml, encoding="utf-8")
    if resources:
        (root / "resources.yaml").write_text(resources, encoding="utf-8")
    if env:
        (root / ".env").write_text(env, encoding="utf-8")
    return root


def _by_sink(stores):
    return {s.sink: s for s in stores}


def test_no_tracing_table_is_no_stores(tmp_path):
    root = _project(tmp_path, "")
    assert project_stores(root, env={}) == []


def test_no_manifest_is_no_stores(tmp_path):
    assert project_stores(tmp_path, env={}) == []


def test_local_is_the_projects_runs_dir(tmp_path):
    root = _project(tmp_path, '[tracing]\nsinks = ["local"]\n')
    (s,) = project_stores(root, env={})
    assert isinstance(s, StoreSource)
    assert s.sink == "local" and s.readable
    assert s.spec == {"backend": "files", "root": str(root / ".operonx" / "runs")}
    assert s.source == "[tracing] → local"
    assert s.levels == ("[tracing]",)
    assert s.describe() == f"files at {root / '.operonx' / 'runs'}"


def test_local_follows_operonx_runs_dir_like_the_writer(tmp_path):
    root = _project(tmp_path, '[tracing]\nsinks = ["local"]\n', env="OPERONX_RUNS_DIR=traces\n")
    (s,) = project_stores(root, env={})
    assert s.spec["root"] == str(root / "traces")


def test_a_local_consumer_resource_reads_its_root_anchored_at_the_project(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_local:calls", "trace_local:abs"]\n',
        "trace_local:\n  calls:\n    root: data/calls\n    layout: flat\n"
        "  abs:\n    root: /srv/runs\n",
    )
    got = _by_sink(project_stores(root, env={}))
    assert got["trace_local:calls"].spec == {
        "backend": "files",
        "root": str(root / "data" / "calls"),
        "layout": "flat",
    }
    assert got["trace_local:abs"].spec["root"] == "/srv/runs"
    assert got["trace_local:calls"].source == "[tracing] → trace_local:calls"


def test_a_local_consumer_with_no_root_is_the_default_dir(tmp_path):
    root = _project(tmp_path, '[tracing]\nsinks = ["trace_local:d"]\n', "trace_local:\n  d: {}\n")
    (s,) = project_stores(root, env={})
    assert s.spec["root"] == str(root / ".operonx" / "runs")


def test_clickhouse_maps_to_the_clickhouse_store_with_env_from_dotenv(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_clickhouse:default"]\n',
        "trace_clickhouse:\n  default:\n    host: ${CH_HOST}\n    port: 8123\n"
        "    user: ${CH_USER:default}\n    password: ${CH_PASSWORD}\n"
        "    database: callbot_traces\n    media_dir: media/ch\n    timeout: 4\n",
        env='# a comment\nCH_HOST=ch.internal\nexport CH_PASSWORD="s3cret"\n',
    )
    (s,) = project_stores(root, env={})
    assert s.readable
    assert s.spec["backend"] == "clickhouse"
    assert s.spec["host"] == "ch.internal"
    assert s.spec["user"] == "default"  # the ${VAR:default}
    assert s.spec["password"] == "s3cret"  # quotes and `export` are .env syntax
    assert s.spec["database"] == "callbot_traces"
    assert s.spec["port"] == 8123
    assert s.spec["timeout"] == 4
    assert s.spec["media_dir"] == str(root / "media" / "ch")
    assert s.source == "[tracing] → trace_clickhouse:default"
    assert s.describe() == "ClickHouse callbot_traces at ch.internal:8123"


def test_the_process_environment_wins_over_dotenv(tmp_path):
    # as operonx.bootstrap() loads .env: without override
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_clickhouse:x"]\n',
        "trace_clickhouse:\n  x:\n    host: ${CH_HOST}\n",
        env="CH_HOST=from-dotenv\n",
    )
    (s,) = project_stores(root, env={"CH_HOST": "from-shell"})
    assert s.spec["host"] == "from-shell"


def test_clickhouse_with_no_media_dir_reads_the_projects_default_media(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_clickhouse:x"]\n',
        "trace_clickhouse:\n  x:\n    host: h\n",
    )
    (s,) = project_stores(root, env={})
    assert s.spec["media_dir"] == str(root / ".operonx" / "runs" / "media")
    assert s.describe() == "ClickHouse operonx at h"


def test_an_unset_variable_skips_the_sink_naming_it(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_clickhouse:x"]\n',
        "trace_clickhouse:\n  x:\n    host: ${NOPE_HOST}\n",
    )
    (s,) = project_stores(root, env={})
    assert not s.readable and s.spec is None
    assert "NOPE_HOST" in s.reason


def test_langfuse_reads_through_its_client_resource(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_langfuse:edupia"]\n',
        "trace_langfuse:\n  edupia:\n    client_resource: langfuse:edupia\n"
        "langfuse:\n  edupia:\n    host: https://lf.example/\n    public_key: ${LF_PK}\n"
        "    secret_key: ${LF_SK}\n",
        env="LF_PK=pk-1\nLF_SK=sk-1\n",
    )
    (s,) = project_stores(root, env={})
    assert s.spec == {
        "backend": "langfuse",
        "host": "https://lf.example",
        "public_key": "pk-1",
        "secret_key": "sk-1",
    }
    assert s.describe() == "Langfuse at https://lf.example"


def test_langfuse_host_defaults_to_the_cloud(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_langfuse:a"]\n',
        "trace_langfuse:\n  a:\n    client_resource: langfuse:a\n"
        "langfuse:\n  a:\n    public_key: pk\n    secret_key: sk\n",
    )
    (s,) = project_stores(root, env={})
    assert s.spec["host"] == "https://cloud.langfuse.com"


def test_langfuse_with_a_missing_client_is_skipped(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_langfuse:a"]\n',
        "trace_langfuse:\n  a:\n    client_resource: langfuse:gone\n",
    )
    (s,) = project_stores(root, env={})
    assert not s.readable and "langfuse:gone" in s.reason


def test_a_run_store_sink_is_read_as_that_store(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["run_store:archive"]\n',
        "run_store:\n  archive:\n    backend: sqlite\n    path: data/runs.sqlite\n",
    )
    (s,) = project_stores(root, env={})
    # as SqliteRunStore resolves it: a relative path is under the runs root
    path = root / ".operonx" / "runs" / "data" / "runs.sqlite"
    assert s.spec == {"backend": "sqlite", "path": str(path)}
    assert s.describe() == f"SQLite {path}"


def test_postgres_and_mongo_run_stores_anchor_media_and_hide_credentials(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["run_store:pg", "run_store:mg"]\n',
        "run_store:\n  pg:\n    backend: postgres\n    dsn: postgresql://u:pw@db.host:5432/runs\n"
        "  mg:\n    backend: mongo\n    uri: mongodb://u:pw@mongo.host:27017\n    database: ox\n"
        "    media_dir: m\n",
    )
    got = _by_sink(project_stores(root, env={}))
    assert got["run_store:pg"].spec["media_dir"] == str(root / ".operonx" / "runs" / "pg-media")
    assert got["run_store:mg"].spec["media_dir"] == str(root / "m")
    assert got["run_store:pg"].describe() == "Postgres runs at db.host:5432"
    assert got["run_store:mg"].describe() == "MongoDB ox at mongo.host:27017"
    assert "pw" not in got["run_store:pg"].describe() + got["run_store:mg"].describe()


def test_the_flat_key_form_is_read_too(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["run_store:default", "trace_clickhouse:default"]\n',
        "run_store:default:\n  backend: files\n  root: flat-runs\n"
        "trace_clickhouse:default:\n  host: flat-host\n",
    )
    got = _by_sink(project_stores(root, env={}))
    assert got["run_store:default"].spec["root"] == str(root / "flat-runs")
    assert got["trace_clickhouse:default"].spec["host"] == "flat-host"


def test_a_custom_consumer_is_skipped_with_a_reason(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_callbot:default", "local"]\n',
        "trace_callbot:\n  default:\n    root: x\n",
    )
    got = _by_sink(project_stores(root, env={}))
    assert got["local"].readable
    bad = got["trace_callbot:default"]
    assert not bad.readable and bad.spec is None
    assert "trace_callbot" in bad.reason
    with pytest.raises(ValueError, match="trace_callbot"):
        bad.open()


def test_a_sink_missing_from_resources_is_skipped(tmp_path):
    root = _project(tmp_path, '[tracing]\nsinks = ["trace_clickhouse:gone"]\n')
    (s,) = project_stores(root, env={})
    assert not s.readable and "not in" in s.reason and "resources.yaml" in s.reason


def test_the_union_of_project_service_and_job_sinks_each_once(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["local"]\n\n'
        '[tracing.services.call]\nsinks = ["local", "trace_clickhouse:default"]\n\n'
        "[tracing.jobs.backfill]\nsinks = []\n\n"
        '[tracing.jobs.qc]\nsinks = ["trace_clickhouse:default", "trace_local:qc"]\n',
        "trace_clickhouse:\n  default:\n    host: h\ntrace_local:\n  qc:\n    root: qc\n",
    )
    stores = project_stores(root, env={})
    assert [s.sink for s in stores] == ["local", "trace_clickhouse:default", "trace_local:qc"]
    got = _by_sink(stores)
    assert got["local"].levels == ("[tracing]", "[tracing.services.call]")
    assert got["trace_clickhouse:default"].levels == (
        "[tracing.services.call]",
        "[tracing.jobs.qc]",
    )
    assert got["trace_clickhouse:default"].source == (
        "[tracing.services.call], [tracing.jobs.qc] → trace_clickhouse:default"
    )


def test_toml_values_interpolate_from_dotenv_too(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["${SINK}"]\n',
        "trace_clickhouse:\n  a:\n    host: h\n",
        env="SINK=trace_clickhouse:a\n",
    )
    (s,) = project_stores(root, env={})
    assert s.sink == "trace_clickhouse:a"


def test_a_resources_overlay_named_in_the_manifest_is_used(tmp_path):
    root = _project(
        tmp_path,
        '[resources]\noverlay = "conf/res.yaml"\n\n[tracing]\nsinks = ["trace_clickhouse:a"]\n',
    )
    (root / "conf").mkdir()
    (root / "conf" / "res.yaml").write_text("trace_clickhouse:\n  a:\n    host: over\n")
    (s,) = project_stores(root, env={})
    assert s.spec["host"] == "over"


def test_a_bad_tracing_table_raises_naming_it(tmp_path):
    root = _project(tmp_path, '[tracing]\nsink = ["local"]\n')
    with pytest.raises(ManifestError, match="sink"):
        project_stores(root, env={})


def test_a_readable_source_opens_its_store(tmp_path):
    root = _project(tmp_path, '[tracing]\nsinks = ["local"]\n')
    (s,) = project_stores(root, env={})
    store = s.open()
    assert isinstance(store, FilesRunStore)
    assert Path(store.root) == root / ".operonx" / "runs"


def test_it_defaults_to_this_processs_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OX_TEST_CH_HOST", "env-host")
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["trace_clickhouse:a"]\n',
        "trace_clickhouse:\n  a:\n    host: ${OX_TEST_CH_HOST}\n",
    )
    (s,) = project_stores(root)
    assert s.spec["host"] == "env-host"


def test_open_run_store_passes_the_clickhouse_timeout():
    from operonx.telemetry.runs import open_run_store

    store = open_run_store({"backend": "clickhouse", "host": "h", "timeout": 2.5})
    try:
        assert store.timeout == 2.5
    finally:
        store.writer.close(timeout=1)


def test_a_blocks_own_trace_counts_unless_tracing_overrides_it(tmp_path):
    root = _project(
        tmp_path,
        '[[serve]]\nname = "call"\ngraph = "main:flow"\ntrace = ["trace_local:mine"]\n\n'
        '[[serve]]\nname = "chat"\ngraph = "main:flow"\ntrace = ["trace_local:ignored"]\n\n'
        '[tracing]\nsinks = ["local"]\n\n[tracing.services.chat]\nsinks = []\n',
        "trace_local:\n  mine:\n    root: mine\n",
    )
    stores = project_stores(root, env={})
    assert [s.sink for s in stores] == ["local", "trace_local:mine"]
    assert stores[1].levels == ("[[serve]] call",)


def test_a_relative_root_gives_absolute_paths(tmp_path, monkeypatch):
    root = _project(tmp_path, '[tracing]\nsinks = ["local"]\n')
    monkeypatch.chdir(root.parent)
    (s,) = project_stores("proj", env={})
    assert s.spec["root"] == str(root / ".operonx" / "runs")
