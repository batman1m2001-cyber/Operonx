"""What a project needs from jobs beyond "score a JSONL file".

A repo whose every script becomes a job needs: a job that runs once
(create a table), a directory in and one file per item out (a batch
scorer), re-running without doubling the output, failing fast when an
endpoint is down, and each job as its own command line.
"""

from __future__ import annotations

import json

import pytest

from operonx.app.jobs import (
    ITEM_OK,
    ITEM_SKIPPED,
    RUN_FAILED,
    RUN_OK,
    DirSink,
    DirSource,
    Job,
    JsonlSink,
    Runbook,
)
from operonx.app.serve import egress, ingress
from operonx.core import END, PARENT, START, graph, op

CALLS: list = []


@op
def shout(item: dict = None, suffix: str = "!") -> dict:
    CALLS.append(item)
    name = item.get("name", "once") if isinstance(item, dict) else str(item)
    return {"out": {"name": name, "text": name.upper() + suffix}}


@graph
def shout_flow(suffix: str = "!"):
    src = ingress()
    s = shout(item=src["item"], suffix=PARENT["suffix"])
    out = egress(item=s["out"])
    START >> src >> s >> out >> END


@pytest.fixture(autouse=True)
def _clear():
    CALLS.clear()


def _calls_dir(tmp_path, names=("a", "b", "c")):
    d = tmp_path / "in"
    d.mkdir()
    for n in names:
        (d / f"{n}.json").write_text(json.dumps({"n": n}), encoding="utf-8")
    (d / "notes.txt").write_text("not an input", encoding="utf-8")
    return d


# -- a job that runs once -----------------------------------------------------


def test_no_source_runs_the_graph_once(tmp_path):
    job = Job("once", graph=shout_flow, record_dir=tmp_path / "runs")
    run = job.run_sync()
    assert run.status == RUN_OK
    assert run.counts.get(ITEM_OK) == 1 and len(CALLS) == 1


# -- a directory in, one file per item out ------------------------------------


async def test_dir_source_yields_matching_files_in_name_order(tmp_path):
    d = _calls_dir(tmp_path, names=("b", "a"))
    items = [i async for i in DirSource(d, pattern="*.json").items()]
    assert [i["name"] for i in items] == ["a", "b"]
    assert all(i["path"].endswith(".json") for i in items)


async def test_dir_source_refuses_a_missing_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        [i async for i in DirSource(tmp_path / "nope").items()]


def test_dir_to_dir_skips_what_is_already_written(tmp_path):
    d = _calls_dir(tmp_path)
    out = tmp_path / "out"
    job = Job(
        "batch",
        graph=shout_flow,
        source=DirSource(d, pattern="*.json"),
        sink=DirSink(out, skip_existing=True),
        key="name",
        record_dir=tmp_path / "runs",
    )
    first = job.run_sync()
    assert first.status == RUN_OK and first.counts.get(ITEM_OK) == 3
    assert json.loads((out / "a.json").read_text(encoding="utf-8"))["text"] == "A!"
    assert not list(out.glob("*.part"))  # written whole, then renamed

    (out / "b.json").unlink()
    CALLS.clear()
    second = job.run_sync()  # no --resume: the files decide
    assert second.counts.get(ITEM_SKIPPED) == 2 and second.counts.get(ITEM_OK) == 1
    assert [c["name"] for c in CALLS] == ["b"]


def test_dir_sink_without_skip_existing_rewrites(tmp_path):
    d = _calls_dir(tmp_path, names=("a",))
    out = tmp_path / "out"
    job = Job(
        "batch",
        graph=shout_flow,
        source=DirSource(d, pattern="*.json"),
        sink=DirSink(out),
        key="name",
        record_dir=tmp_path / "runs",
    )
    job.run_sync()
    job.run_sync()
    assert len(CALLS) == 2


# -- re-running does not double the output -------------------------------------


def test_fresh_run_starts_the_file_over_and_resume_adds(tmp_path):
    out = tmp_path / "out.jsonl"
    items = [{"name": "a"}, {"name": "b"}]
    job = Job(
        "j",
        graph=shout_flow,
        source=items,
        sink=JsonlSink(out),
        key="name",
        record_dir=tmp_path / "runs",
    )
    job.run_sync()
    job.run_sync()
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2  # was 4

    job.source = items + [{"name": "c"}]
    job.run_sync(resume=True)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and json.loads(lines[-1])["name"] == "c"


async def test_a_sink_used_directly_still_appends(tmp_path):
    out = tmp_path / "out.jsonl"
    for key in ("a", "b"):
        sink = JsonlSink(out)
        await sink.write(key, {})
        await sink.close()
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2


# -- preflight ----------------------------------------------------------------


def test_unreachable_preflight_fails_before_any_item(tmp_path, monkeypatch):
    from operonx.app.jobs import runner

    monkeypatch.setattr(runner, "preflight_error", lambda keys, timeout=2.0: "preflight: down")
    job = Job(
        "pf",
        graph=shout_flow,
        source=[{"name": "a"}],
        key="name",
        preflight=["llm:x"],
        record_dir=tmp_path / "runs",
    )
    run = job.run_sync()
    assert run.status == RUN_FAILED and run.meta.get("error") == "preflight: down"
    assert CALLS == []


def test_no_preflight_checks_nothing():
    from operonx.app.jobs.runner import preflight_error

    assert preflight_error([]) is None and preflight_error(None) is None


# -- each job is its own command line -----------------------------------------


def test_main_sets_inputs_and_overrides_the_sink(tmp_path, capsys):
    out = tmp_path / "o.jsonl"
    job = Job(
        "cli", graph=shout_flow, source=[{"name": "a"}], key="name", record_dir=tmp_path / "runs"
    )
    code = job.main(["--set", "suffix=?", "--sink", str(out)])
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["text"] == "A?"


def test_main_show_prints_and_runs_nothing(tmp_path, capsys):
    job = Job("cli", graph=shout_flow, source=[{"name": "a"}], record_dir=tmp_path / "runs")
    assert job.main(["--show", "--set", "suffix=1"]) == 0
    printed = capsys.readouterr().out
    assert "cli" in printed and "suffix" in printed and CALLS == []


def test_main_exit_status_follows_the_run(tmp_path, monkeypatch):
    from operonx.app.jobs import runner

    monkeypatch.setattr(runner, "preflight_error", lambda keys, timeout=2.0: "down")
    job = Job("cli", graph=shout_flow, preflight=["llm:x"], record_dir=tmp_path / "runs")
    assert job.main([]) == 1


def test_main_rejects_a_malformed_set(tmp_path):
    job = Job("cli", graph=shout_flow, record_dir=tmp_path / "runs")
    assert job.main(["--set", "nokey"]) == 2


def test_runbook_main_sets_every_job(tmp_path):
    a = Job("a", graph=shout_flow, record_dir=tmp_path / "runs")
    b = Job("b", graph=shout_flow, record_dir=tmp_path / "runs")
    book = Runbook("both", a >> b, record_dir=tmp_path / "runs")
    assert book.main(["--set", "suffix=#"]) == 0
    assert a.inputs == {"suffix": "#"} and b.inputs == {"suffix": "#"}
    assert book.main(["--sink", "x.jsonl"]) == 2  # names one job's data


def test_main_help_prints_the_module_doc(tmp_path, capsys):
    job = Job("cli", graph=shout_flow, record_dir=tmp_path / "runs")
    with pytest.raises(SystemExit):
        job.main(["--help"], doc="Shouts every name.")
    assert "Shouts every name." in capsys.readouterr().out


# -- operonx-run reads .env before the manifest --------------------------------


def test_cli_loads_dotenv_before_resolving_the_manifest(tmp_path, monkeypatch):
    from operonx.cli import run as cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPX_TEST_SINK", raising=False)
    (tmp_path / ".env").write_text("OPX_TEST_SINK=from_dotenv\n", encoding="utf-8")
    cli._load_dotenv()
    import os

    assert os.environ.get("OPX_TEST_SINK") == "from_dotenv"
    monkeypatch.delenv("OPX_TEST_SINK", raising=False)


# -- a failed item still leaves a file, and a host sees every outcome ----------

BOOM: set = set()


@op
def maybe_boom(item: dict = None) -> dict:
    if item["name"] in BOOM:
        raise ValueError(f"cannot {item['name']}")
    return {"out": {"name": item["name"]}}


@graph
def boom_flow():
    src = ingress()
    b = maybe_boom(item=src["item"])
    out = egress(item=b["out"])
    START >> src >> b >> out >> END


def test_dir_sink_writes_an_error_file_for_a_failed_item(tmp_path):
    BOOM.clear()
    BOOM.add("b")
    out = tmp_path / "out"
    seen = []
    job = Job(
        "errs",
        graph=boom_flow,
        source=[{"name": "a"}, {"name": "b"}],
        sink=DirSink(out, write_errors=True),
        key="name",
        on_item=lambda r: seen.append((r.key, r.status)),
        record_dir=tmp_path / "runs",
    )
    run = job.run_sync()
    assert run.status == RUN_FAILED
    assert json.loads((out / "a.json").read_text(encoding="utf-8")) == {"name": "a"}
    err = json.loads((out / "b.json").read_text(encoding="utf-8"))
    assert list(err) == ["error"] and "cannot b" in err["error"]
    assert sorted(seen) == [("a", "ok"), ("b", "failed")]


def test_dir_sink_without_write_errors_leaves_no_file(tmp_path):
    BOOM.clear()
    BOOM.add("a")
    out = tmp_path / "out"
    Job(
        "errs",
        graph=boom_flow,
        source=[{"name": "a"}],
        sink=DirSink(out),
        key="name",
        record_dir=tmp_path / "runs",
    ).run_sync()
    assert not (out / "a.json").exists()


def test_on_item_sees_skips_and_a_raising_hook_breaks_nothing(tmp_path):
    BOOM.clear()
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.json").write_text("{}", encoding="utf-8")
    seen = []

    def hook(r):
        seen.append(r.status)
        raise RuntimeError("hook bug")

    run = Job(
        "hook",
        graph=boom_flow,
        source=[{"name": "a"}, {"name": "b"}],
        sink=DirSink(out, skip_existing=True),
        key="name",
        on_item=hook,
        record_dir=tmp_path / "runs",
    ).run_sync()
    assert run.status == RUN_OK and sorted(seen) == ["ok", "skipped"]


# -- a graph with no doors is a function: inputs in, result out ---------------


@op
def report(dsn: str = "", item: dict = None) -> dict:
    CALLS.append(item)
    return {"report": {"dsn": dsn, "got": item}}


@graph
def plain(dsn: str = ""):
    r = report(dsn=PARENT["dsn"])
    START >> r >> END


@graph
def plain_item(item: dict = None):
    r = report(item=PARENT["item"])
    START >> r >> END


@op
def nothing() -> dict:
    return {}


@graph
def returns_nothing():
    n = nothing()
    START >> n >> END


def test_a_graph_without_ingress_runs_once_and_its_result_is_the_output(tmp_path):
    out = []
    job = Job("plain", graph=plain, sink=out, inputs={"dsn": "pg://x"}, record_dir=tmp_path)
    assert job.has_doors() is False
    run = job.run_sync()
    assert run.status == RUN_OK and run.counts.get(ITEM_OK) == 1
    assert out == [{"report": {"dsn": "pg://x", "got": None}}]


def test_item_input_binds_the_item_on_a_doorless_graph(tmp_path):
    out = []
    job = Job(
        "per",
        graph=plain_item,
        source=[{"name": "a"}, {"name": "b"}],
        sink=out,
        key="name",
        item_input="item",
        record_dir=tmp_path,
    )
    assert job.run_sync().counts.get(ITEM_OK) == 2
    assert [o["report"]["got"]["name"] for o in out] == ["a", "b"]


def test_a_doorless_run_that_returns_nothing_is_empty_not_ok(tmp_path):
    run = Job("none", graph=returns_nothing, record_dir=tmp_path).run_sync()
    assert run.counts.get("empty") == 1 and run.counts.get(ITEM_OK) == 0


def test_a_graph_with_ingress_keeps_its_doors(tmp_path):
    assert Job("doors", graph=shout_flow, record_dir=tmp_path).has_doors() is True


def test_a_stream_job_needs_doors(tmp_path):
    job = Job("s", graph=plain, session="stream", record_dir=tmp_path)
    with pytest.raises(ValueError, match="ingress"):
        job.run_sync()


# -- on_error="record": a failed item is data, not a failed run ----------------


def test_record_keeps_the_run_ok_and_every_outcome(tmp_path):
    BOOM.clear()
    BOOM.add("b")
    out = tmp_path / "out"
    run = Job(
        "rec",
        graph=boom_flow,
        source=[{"name": "a"}, {"name": "b"}],
        sink=DirSink(out, write_errors=True),
        key="name",
        on_error="record",
        record_dir=tmp_path / "runs",
    ).run_sync()
    assert run.status == RUN_OK
    assert run.counts.get("failed") == 1 and run.counts.get(ITEM_OK) == 1
    assert "error" in json.loads((out / "b.json").read_text(encoding="utf-8"))


def test_skip_still_fails_the_run(tmp_path):
    BOOM.clear()
    BOOM.add("a")
    run = Job(
        "skp", graph=boom_flow, source=[{"name": "a"}], key="name", record_dir=tmp_path
    ).run_sync()
    assert run.status == RUN_FAILED


def test_record_is_a_known_policy():
    from operonx.app.jobs import parse_on_error

    assert parse_on_error("record").mode == "record"
    with pytest.raises(ValueError, match="record"):
        parse_on_error("ignore")


def _named_source():
    yield {"id": "x"}


def test_a_function_source_is_described_by_its_name(tmp_path):
    job = Job(
        "named",
        graph=plain_item,
        source=_named_source,
        item_input="item",
        sink=[],
        record_dir=tmp_path,
    )
    assert job.describe()["source"] == f"{__name__}:_named_source"
    assert (
        Job("listed", graph=plain_item, source=[1], sink=[], record_dir=tmp_path).describe()[
            "source"
        ]
        == "[1]"
    )
