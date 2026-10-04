"""`project_score_store(root)` — where a project's experiments and scores live.

Read from ``operonx.toml`` (``[evals]``, ``[tracing]``) and
``resources.yaml`` with ``${VAR}`` from the project's ``.env``, never by
importing the project — the same files ``project_stores`` reads (D38).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operonx.app.manifest import ManifestError
from operonx.telemetry.scores import project_score_store
from operonx.telemetry.scores.files import FilesScoreStore

CLICKHOUSE = (
    "trace_clickhouse:\n"
    "  team:\n"
    "    host: ${CH_HOST}\n"
    "    port: 8123\n"
    "    user: writer\n"
    "    password: ${CH_PASSWORD}\n"
    "    database: callbot\n"
)


def _project(tmp_path: Path, toml: str = "", resources: str = "", env: str = "") -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "operonx.toml").write_text('[project]\nname = "p"\n\n' + toml, encoding="utf-8")
    if resources:
        (root / "resources.yaml").write_text(resources, encoding="utf-8")
    if env:
        (root / ".env").write_text(env, encoding="utf-8")
    return root


def test_nothing_configured_is_files_under_the_runs_root(tmp_path):
    root = _project(tmp_path)
    src = project_score_store(root, env={})
    assert src.spec == {"backend": "files", "root": str(root / ".operonx" / "runs" / "scores")}
    assert src.source == "default: files under the runs root"
    store = src.open()
    try:
        assert isinstance(store, FilesScoreStore)
        assert store.root == root / ".operonx" / "runs" / "scores"
    finally:
        store.close()


def test_no_manifest_is_the_default_too(tmp_path):
    src = project_score_store(tmp_path, env={})
    assert src.spec["root"] == str(tmp_path.absolute() / ".operonx" / "runs" / "scores")


def test_the_runs_root_follows_operonx_runs_dir(tmp_path):
    root = _project(tmp_path, env="OPERONX_RUNS_DIR=traces\n")
    assert project_score_store(root, env={}).spec["root"] == str(root / "traces" / "scores")
    # the process environment wins over .env, as bootstrap() has it
    got = project_score_store(root, env={"OPERONX_RUNS_DIR": "/srv/runs"})
    assert got.spec["root"] == "/srv/runs/scores"


def test_local_tracing_is_files_under_the_runs_root(tmp_path):
    root = _project(tmp_path, '[tracing]\nsinks = ["local"]\n')
    src = project_score_store(root, env={})
    assert src.spec == {"backend": "files", "root": str(root / ".operonx" / "runs" / "scores")}


def test_a_clickhouse_sink_is_the_same_database(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["local", "trace_clickhouse:team"]\n',
        CLICKHOUSE,
        env="CH_HOST=10.0.0.5\nCH_PASSWORD=s3cret\n",
    )
    src = project_score_store(root, env={})
    assert src.spec == {
        "backend": "clickhouse",
        "host": "10.0.0.5",
        "port": 8123,
        "user": "writer",
        "password": "s3cret",
        "database": "callbot",
    }
    # ClickHouse wins over local although listed second: it is the shared one
    assert src.source == "[tracing] → trace_clickhouse:team"
    assert "s3cret" not in src.describe() and "10.0.0.5" in src.describe()


def test_a_clickhouse_run_store_sink_counts_too(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["run_store:ch"]\n',
        "run_store:\n  ch:\n    backend: clickhouse\n    host: h\n    database: d\n",
    )
    src = project_score_store(root, env={})
    assert src.spec == {"backend": "clickhouse", "host": "h", "database": "d"}
    assert src.source == "[tracing] → run_store:ch"


def test_a_per_service_sink_does_not_choose_the_project_store(tmp_path):
    root = _project(
        tmp_path,
        '[tracing]\nsinks = ["local"]\n\n[tracing.services.call]\nsinks = ["trace_clickhouse:team"]\n'
        '\n[[serve]]\nname = "call"\ngraph = "m:g"\nport = 9000\n',
        CLICKHOUSE,
        env="CH_HOST=h\nCH_PASSWORD=p\n",
    )
    assert project_score_store(root, env={}).spec["backend"] == "files"


def test_evals_scores_overrides_tracing(tmp_path):
    root = _project(
        tmp_path,
        '[evals]\nscores = "score_store:mine"\n\n[tracing]\nsinks = ["trace_clickhouse:team"]\n',
        CLICKHOUSE + "score_store:\n  mine:\n    backend: files\n    root: exp\n",
        env="CH_HOST=h\nCH_PASSWORD=p\n",
    )
    src = project_score_store(root, env={})
    # a relative files root sits under the runs root, as FilesScoreStore anchors it
    assert src.spec == {"backend": "files", "root": str(root / ".operonx" / "runs" / "exp")}
    assert src.source == "[evals] scores → score_store:mine"


def test_evals_scores_sqlite_path_is_under_the_runs_root(tmp_path):
    root = _project(
        tmp_path,
        '[evals]\nscores = "score_store:lite"\n',
        "score_store:\n  lite:\n    backend: sqlite\n    path: scores.sqlite\n",
    )
    src = project_score_store(root, env={})
    assert src.spec == {
        "backend": "sqlite",
        "path": str(root / ".operonx" / "runs" / "scores.sqlite"),
    }


def test_an_unknown_evals_key_is_an_error_naming_it(tmp_path):
    root = _project(tmp_path, '[evals]\nstore = "score_store:x"\n')
    with pytest.raises(ManifestError, match=r"\[evals\].*'store'.*scores"):
        project_score_store(root, env={})


def test_evals_scores_must_be_a_score_store_key(tmp_path):
    root = _project(tmp_path, '[evals]\nscores = "trace_clickhouse:team"\n')
    with pytest.raises(ManifestError, match="score_store:<name>"):
        project_score_store(root, env={})


def test_a_missing_resource_is_unopenable_with_the_reason(tmp_path):
    root = _project(tmp_path, '[evals]\nscores = "score_store:nope"\n')
    src = project_score_store(root, env={})
    assert src.spec is None and "score_store:nope is not in resources.yaml" in src.reason
    with pytest.raises(ValueError, match="not in resources.yaml"):
        src.open()


def test_an_unset_variable_is_unopenable_naming_it(tmp_path):
    root = _project(tmp_path, '[tracing]\nsinks = ["trace_clickhouse:team"]\n', CLICKHOUSE)
    src = project_score_store(root, env={})
    assert src.spec is None
    assert "${CH_HOST}" in src.reason and "${CH_PASSWORD}" in src.reason
    assert src.source == "[tracing] → trace_clickhouse:team"
