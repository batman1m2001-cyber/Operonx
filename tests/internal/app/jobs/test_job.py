"""`Job`: items in, one run per item, every result kept; `reduce`; `steps`.

The gates of docs/JOBS_AND_GUIDES_PLAN.md phase A1: each form of
``items``, the binding rules (and their errors naming the field), results
kept so ``--resume`` and ``reduce`` see every key, ``output``, the failure
policy, and a job of steps.
"""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from operonx.app.jobs import (
    ITEM_EMPTY,
    ITEM_FAILED,
    ITEM_OK,
    ITEM_SKIPPED,
    ITEM_TIMEOUT,
    RUN_FAILED,
    RUN_OK,
    RUN_STOPPED,
    Job,
)
from operonx.app.serve import egress, ingress
from operonx.core import END, START, graph, op
from operonx.core.policy import Retry

#: Item values `square` refuses; set per test.
FAIL = set()
#: Every value `square` was called with.
CALLS = []


@pytest.fixture(autouse=True)
def _fresh():
    FAIL.clear()
    CALLS.clear()


@op
def square(n: int) -> dict:
    CALLS.append(n)
    if n in FAIL:
        raise ValueError(f"no {n}")
    return {"sq": n * n}


@graph
def sq(n):
    s = square(n=n)
    START >> s >> END


@op
def power(n: int, p: int) -> dict:
    return {"value": n**p}


@graph
def pw(n, p):
    s = power(n=n, p=p)
    START >> s >> END


@op
def nothing(n: int) -> dict:
    return {}


@graph
def quiet(n):
    s = nothing(n=n)
    START >> s >> END


@op
async def slow(n: int) -> dict:
    await asyncio.sleep(1.0)
    return {"sq": n}


@graph
def slow_flow(n):
    s = slow(n=n)
    START >> s >> END


@op
def total(results: list) -> dict:
    return {"sum": sum(r["sq"] for r in results), "order": [r["sq"] for r in results]}


@graph
def add_up(results):
    t = total(results=results)
    START >> t >> END


@op
def boom(results: list) -> dict:
    raise RuntimeError("cannot add")


@graph
def broken_reduce(results):
    b = boom(results=results)
    START >> b >> END


@graph
def no_results(n):
    s = square(n=n)
    START >> s >> END


@op
def shout(item: dict) -> dict:
    return {"out": item["text"].upper()}


@graph
def door_flow():
    src = ingress()
    s = shout(item=src["item"])
    out = egress(item=s["out"])
    START >> src >> s >> out >> END


@op
async def twice(item: dict):
    yield {"out": item["text"]}
    yield {"out": item["text"] + "!"}


@graph
def echo_twice():
    src = ingress()
    t = twice(item=src["item"])
    out = egress(item=t["out"])
    START >> src >> t >> out >> END


def _run(job, tmp_path, **kw):
    return job.run_sync(record_dir=tmp_path / "rec", **kw)


# -- items --------------------------------------------------------------------


def test_a_list_runs_each_item_and_keeps_its_result(tmp_path):
    run = _run(Job("j", graph=sq, items=[{"n": 2}, {"n": 3}], key="n"), tmp_path)
    assert run.status == RUN_OK and run.counts[ITEM_OK] == 2
    assert run.results == {"2": {"sq": 4}, "3": {"sq": 9}}
    assert (run.path / "results.jsonl").exists()


def test_a_function_is_called_on_every_run(tmp_path):
    loads = []

    def load():
        loads.append(1)
        yield {"n": len(loads)}

    job = Job("j", graph=sq, items=load, key="n")
    assert _run(job, tmp_path).results == {"1": {"sq": 1}}
    assert _run(job, tmp_path).results == {"2": {"sq": 4}}  # read again


def test_an_async_generator_feeds_the_job(tmp_path):
    async def load():
        for n in (1, 2):
            yield {"n": n}

    assert _run(Job("j", graph=sq, items=load, key="n"), tmp_path).counts[ITEM_OK] == 2


def test_a_jsonl_path_is_one_item_per_line(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text('{"n": 4}\n\n{"n": 5}\n', encoding="utf-8")
    assert _run(Job("j", graph=sq, items=path, key="n"), tmp_path).results == {
        "4": {"sq": 16},
        "5": {"sq": 25},
    }


def test_no_items_runs_the_graph_once(tmp_path):
    run = _run(Job("j", graph=pw, inputs={"n": 2, "p": 3}), tmp_path)
    assert run.status == RUN_OK and list(run.results.values()) == [{"value": 8}]


@pytest.mark.parametrize(
    "items,match",
    [
        ("items.csv", "must be a .jsonl file"),
        ({"n": 1}, "pass a list of items"),
        (42, "pass a list, an iterable"),
    ],
)
def test_what_items_cannot_be_is_refused_at_declaration(items, match):
    with pytest.raises((TypeError, ValueError), match=match):
        Job("j", graph=sq, items=items)


# -- binding ------------------------------------------------------------------


def test_a_dict_item_fills_the_parameters_by_name(tmp_path):
    run = _run(Job("j", graph=pw, items=[{"n": 2, "p": 5}], key="n"), tmp_path)
    assert run.results == {"2": {"value": 32}}


def test_an_unknown_field_fails_the_item_naming_it(tmp_path):
    run = _run(Job("j", graph=sq, items=[{"m": 1}]), tmp_path)
    (error,) = run.errors.values()
    assert "['m']" in error and "input=" in error and run.status == RUN_FAILED


def test_input_hands_the_whole_item_to_one_parameter(tmp_path):
    run = _run(Job("j", graph=sq, items=[7], input="n", key=lambda x: x), tmp_path)
    assert run.results == {"7": {"sq": 49}}


def test_a_plain_item_goes_to_the_only_free_parameter(tmp_path):
    run = _run(Job("j", graph=pw, items=[3], inputs={"p": 2}, key=lambda x: x), tmp_path)
    assert run.results == {"3": {"value": 9}}


def test_a_plain_item_with_two_free_parameters_asks_for_input(tmp_path):
    run = _run(Job("j", graph=pw, items=[3]), tmp_path)
    assert "pass input=" in next(iter(run.errors.values()))


def test_a_graph_with_doors_takes_the_item_through_ingress(tmp_path):
    run = _run(Job("j", graph=door_flow, items=[{"text": "hi"}], key="text"), tmp_path)
    assert run.results == {"hi": "HI"}


def test_several_sends_are_a_list(tmp_path):
    run = _run(Job("j", graph=echo_twice, items=[{"text": "a"}], key="text"), tmp_path)
    assert run.results == {"a": ["a", "a!"]} and run.items[0].sent == 2


def test_a_run_that_produces_nothing_is_empty(tmp_path):
    run = _run(Job("j", graph=quiet, items=[{"n": 1}], key="n"), tmp_path)
    assert run.items[0].status == ITEM_EMPTY and run.results == {}


# -- output -------------------------------------------------------------------


def test_output_jsonl_is_fresh_per_run_and_appended_on_resume(tmp_path):
    out = tmp_path / "out" / "scores.jsonl"
    FAIL.add(2)
    job = Job("j", graph=sq, items=[{"n": 1}, {"n": 2}], key="n", output=out, concurrency=1)
    _run(job, tmp_path)
    assert [json.loads(line)["key"] for line in out.read_text().splitlines()] == ["1"]
    FAIL.clear()
    _run(job, tmp_path, resume=True)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines == [{"key": "1", "result": {"sq": 1}}, {"key": "2", "result": {"sq": 4}}]
    _run(job, tmp_path)  # a fresh run starts the file over
    assert len(out.read_text().splitlines()) == 2


def test_output_function_sees_each_success(tmp_path):
    got = {}

    async def save(key, result):
        got[key] = result

    FAIL.add(2)
    _run(Job("j", graph=sq, items=[{"n": 1}, {"n": 2}], key="n", output=save), tmp_path)
    assert got == {"1": {"sq": 1}}


def test_an_output_that_raises_fails_its_item(tmp_path):
    def save(key, result):
        raise OSError("disk full")

    run = _run(Job("j", graph=sq, items=[{"n": 1}], key="n", output=save), tmp_path)
    assert run.items[0].status == ITEM_FAILED and "disk full" in run.items[0].error


def test_an_output_path_must_be_jsonl():
    with pytest.raises(ValueError, match=".jsonl"):
        Job("j", graph=sq, output="out.csv")


# -- reduce -------------------------------------------------------------------


def test_reduce_runs_once_over_every_result_in_key_order(tmp_path):
    job = Job("j", graph=sq, items=[{"n": 3}, {"n": 1}, {"n": 2}], key="n", reduce=add_up)
    run = _run(job, tmp_path)
    assert run.reduced == {"sum": 14, "order": [1, 4, 9]}


def test_a_resumed_run_reduces_over_every_key(tmp_path):
    FAIL.add(2)
    job = Job("j", graph=sq, items=[{"n": i} for i in range(4)], key="n", reduce=add_up)
    first = _run(job, tmp_path)
    assert first.status == RUN_FAILED and first.reduced == {"sum": 10, "order": [0, 1, 9]}
    FAIL.clear()
    CALLS.clear()
    again = _run(job, tmp_path, resume=True)
    assert CALLS == [2]  # only the failed key ran again
    assert again.counts[ITEM_SKIPPED] == 3 and again.status == RUN_OK
    assert again.reduced == {"sum": 14, "order": [0, 1, 4, 9]}
    assert set(again.results) == {"0", "1", "2", "3"}


def test_a_failing_reduce_fails_the_run(tmp_path):
    run = _run(Job("j", graph=sq, items=[{"n": 1}], key="n", reduce=broken_reduce), tmp_path)
    assert run.status == RUN_FAILED and "cannot add" in run.meta["error"]


def test_a_reduce_needs_a_results_parameter(tmp_path):
    with pytest.raises(TypeError, match="results"):
        _run(Job("j", graph=sq, items=[{"n": 1}], reduce=no_results), tmp_path)


def test_reduce_needs_the_results_kept():
    with pytest.raises(ValueError, match="keep_results"):
        Job("j", graph=sq, reduce=add_up, keep_results=False)


def test_without_kept_results_nothing_is_written(tmp_path):
    run = _run(Job("j", graph=sq, items=[{"n": 1}], key="n", keep_results=False), tmp_path)
    assert run.status == RUN_OK and run.results == {}
    assert not (run.path / "results.jsonl").exists()


# -- failures -----------------------------------------------------------------


def test_stop_starts_nothing_new_and_does_not_reduce(tmp_path):
    FAIL.add(1)
    job = Job(
        "j",
        graph=sq,
        items=[{"n": i} for i in range(4)],
        key="n",
        on_error="stop",
        concurrency=1,
        reduce=add_up,
    )
    run = _run(job, tmp_path)
    assert run.status == RUN_STOPPED and CALLS == [0, 1] and run.reduced is None


def test_retry_runs_a_failed_item_again(tmp_path):
    FAIL.add(1)
    retry = Retry(max_attempts=3, initial=0.01, jitter=False)
    run = _run(Job("j", graph=sq, items=[{"n": 1}], key="n", retry=retry), tmp_path)
    assert CALLS == [1, 1, 1] and run.items[0].attempts == 3


def test_timeout_cancels_an_item(tmp_path):
    run = _run(Job("j", graph=slow_flow, items=[{"n": 1}], key="n", timeout=0.1), tmp_path)
    assert run.items[0].status == ITEM_TIMEOUT and run.status == RUN_FAILED


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"on_error": "record"}, "'skip' or 'stop'"),
        ({"retry": 3}, "Retry"),
        ({"timeout": 0}, "positive"),
        ({"concurrency": 0}, "at least 1"),
    ],
)
def test_settings_are_checked_at_declaration(kwargs, match):
    with pytest.raises((TypeError, ValueError), match=match):
        Job("j", graph=sq, **kwargs)


def test_unreachable_preflight_fails_before_any_item(tmp_path, monkeypatch):
    from operonx.app.jobs import runner

    monkeypatch.setattr(runner, "preflight_error", lambda keys: "preflight: llm:x is down")
    run = _run(Job("j", graph=sq, items=[{"n": 1}], preflight=["llm:x"]), tmp_path)
    assert run.status == RUN_FAILED and CALLS == [] and "llm:x" in run.meta["error"]


# -- steps --------------------------------------------------------------------


def test_steps_run_in_order_and_each_keeps_its_record(tmp_path):
    a = Job("a", graph=sq, items=[{"n": 2}], key="n")
    b = Job("b", graph=sq, items=[{"n": 3}], key="n")
    run = _run(Job("ab", steps=[a, b]), tmp_path)
    assert run.status == RUN_OK and CALLS == [2, 3]
    assert [s["name"] for s in run.meta["steps"]] == ["a", "b"]
    assert all((tmp_path / "rec" / s / "").is_dir() for s in ("a", "b", "ab"))


def test_a_failed_step_stops_the_rest(tmp_path):
    FAIL.add(2)
    a = Job("a", graph=sq, items=[{"n": 2}], key="n")
    b = Job("b", graph=sq, items=[{"n": 3}], key="n")
    run = _run(Job("ab", steps=[a, b]), tmp_path)
    assert run.status == RUN_FAILED and CALLS == [2]
    assert [(i.key, i.status) for i in run.items] == [("a", ITEM_FAILED), ("b", ITEM_SKIPPED)]


def test_resume_reaches_every_step(tmp_path):
    FAIL.add(2)
    a = Job("a", graph=sq, items=[{"n": 1}, {"n": 2}], key="n")
    _run(Job("ab", steps=[a]), tmp_path)
    FAIL.clear()
    CALLS.clear()
    assert _run(Job("ab", steps=[a]), tmp_path, resume=True).status == RUN_OK
    assert CALLS == [2]


def test_a_job_of_steps_takes_nothing_of_its_own():
    a = Job("a", graph=sq)
    with pytest.raises(ValueError, match="no graph"):
        Job("x", steps=[a], graph=sq)
    with pytest.raises(ValueError, match="at least one"):
        Job("x", steps=[])
    with pytest.raises(TypeError, match="a step is a Job"):
        Job("x", steps=[sq])
    with pytest.raises(ValueError, match="needs a graph"):
        Job("x")


# -- where runs go ------------------------------------------------------------


def test_runs_go_under_the_project_by_default(tmp_path, monkeypatch):
    (tmp_path / "operonx.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
    sub = tmp_path / "deep"
    sub.mkdir()
    monkeypatch.chdir(sub)
    run = Job("j", graph=sq, items=[{"n": 1}]).run_sync()
    assert run.path.parent == tmp_path / ".operonx" / "jobs" / "j"


def test_run_overrides_are_for_that_run_only(tmp_path):
    seen = []
    job = Job("j", graph=sq, items=[{"n": 1}], key="n")
    job.run_sync(record_dir=tmp_path / "once", on_item=seen.append)
    assert job.record_dir is None and job.on_item is None and len(seen) == 1


def test_a_copy_reads_its_own_items(tmp_path):
    class Fed(Job):
        def __init__(self):
            super().__init__("fed", graph=sq, items=self._mine, key="n")
            self.n = 1

        def _mine(self):
            yield {"n": self.n}

    original = Fed()
    clone = copy.copy(original)
    clone.n = 5
    assert _run(clone, tmp_path).results == {"5": {"sq": 25}}
