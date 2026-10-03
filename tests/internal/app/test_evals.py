"""Evals (`operonx.app.evals`) — P7.

Gates: a dataset is a JSONL file of cases (``input`` / ``expected``; a
bare line is its own input) with stable ids and deduped appends; any
evaluator result becomes a verdict; the built-ins judge what they claim;
an Eval is a Job — the system under test runs per case through the job
runtime (doors or not), the verdict lands on the item record, run.json
carries the pass rate, the run fails when a case does (or when the rate is
under a threshold), and the traces are ``origin=eval``; a broken case or
evaluator fails its case rather than the eval; the LLM judge parses a
structured verdict and keeps its cost; ``[[job]]`` with ``dataset`` builds
an Eval and ``operonx run`` gates on it; a plain job's record is unchanged.
"""

from __future__ import annotations

import json
import sys
import textwrap
import uuid
import warnings
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from operonx.app import Application, Dataset, Eval
from operonx.app.evals import (
    contains,
    dataset_path,
    exact,
    fuzzy,
    json_match,
    llm_judge,
    verdict_of,
)
from operonx.app.jobs import Job
from operonx.core import END, START, graph, op
from operonx.telemetry.runs.files import FilesRunStore

# ── the systems under test ────────────────────────────────────────────────


@op(bound="sync")
def classify(text: str = "") -> dict:
    if text == "boom":
        raise ValueError("cannot classify")
    label = "refund" if "money back" in text else "other"
    return {"label": label, "confidence": 0.9}


@graph
def classify_flow(text: str = ""):
    c = classify(text=text)
    START >> c >> END


def _cases(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


CASES = [
    {"id": "a", "input": "I want my money back", "expected": {"label": "refund"}},
    {"id": "b", "input": "hello", "expected": {"label": "other"}},
    {
        "id": "c",
        "input": "give money back now",
        "expected": {"label": "other"},
    },  # the system is wrong here
]


# ── datasets ──────────────────────────────────────────────────────────────


def test_a_dataset_is_a_file_of_cases(tmp_path):
    ds = Dataset(_cases(tmp_path / "d.jsonl", [{"id": "x", "input": 1}, {"plain": "row"}]))
    rows = ds.rows()
    assert rows[0] == {"id": "x", "input": 1}
    assert (
        rows[1]["input"] == {"plain": "row"} and len(rows[1]["id"]) == 12
    )  # a bare line is its input
    assert Dataset(ds.path).rows()[1]["id"] == rows[1]["id"]  # ids are stable

    added = ds.add([{"input": "new", "expected": "N", "tags": ["t"]}, {"id": "x", "input": 9}])
    assert len(added) == 1 and len(ds) == 3  # the second shares an id: skipped
    last = json.loads(ds.path.read_text().splitlines()[-1])
    assert list(last) == ["id", "input", "expected", "tags"]
    with pytest.raises(ValueError, match="needs an `input`"):
        ds.add([{"expected": 1}])

    (tmp_path / "bad.jsonl").write_text('{"input": 1}\nnot json\n')
    with pytest.raises(ValueError, match="bad.jsonl:2"):
        Dataset(tmp_path / "bad.jsonl").rows()
    assert dataset_path("dataset:replies", tmp_path) == tmp_path / "datasets" / "replies.jsonl"
    assert Dataset(tmp_path / "missing.jsonl").rows() == []


def test_any_result_becomes_a_verdict():
    assert verdict_of(True) == {"passed": True}
    assert verdict_of(0.7) == {"passed": True, "score": 0.7}
    assert verdict_of(0.2) == {"passed": False, "score": 0.2}
    assert verdict_of({"score": 0.9, "reason": "ok"}) == {
        "score": 0.9,
        "reason": "ok",
        "passed": True,
    }
    assert verdict_of(None)["passed"] is False


def test_the_built_in_evaluators():
    assert exact("label")(output={"label": "a"}, expected={"label": "a"})["passed"]
    miss = exact()(output=1, expected=2)
    assert not miss["passed"] and "expected 2" in miss["reason"]
    assert contains("refund", "sorry")(output="Sorry — your REFUND is on its way")["passed"]
    assert contains()(output="hi there", expected=["hi", "bye"])["reason"] == "missing ['bye']"
    assert fuzzy(0.8)(output="hello world", expected="hello world!")["passed"]
    assert not fuzzy(0.8)(output="abc", expected="xyz")["passed"]
    got = json_match()(output={"a": 1, "b": 2}, expected={"a": 1, "b": 3})
    assert got == {"passed": False, "score": 0.5, "reason": "differs on ['b']"}


# ── an eval is a job ──────────────────────────────────────────────────────


def _eval(tmp_path, evaluators, rows=CASES, **kw):
    return Eval(
        "classify_eval",
        graph=classify_flow,
        item_input="text",
        dataset=_cases(tmp_path / "cases.jsonl", rows),
        evaluators=evaluators,
        record_dir=tmp_path / "evals",
        trace=[_consumer(tmp_path)],
        **kw,
    )


def _consumer(tmp_path):
    from operonx.telemetry.consumers.local import LocalConsumer

    return LocalConsumer({"root": str(tmp_path / "runs")})


def test_each_case_is_judged_and_the_record_says_so(tmp_path):
    run = _eval(tmp_path, [exact("label")]).run_sync()
    assert run.status == "failed"  # case c fails: the eval fails
    ev = run.meta["eval"]
    assert (ev["cases"], ev["passed"], ev["failed"], ev["errored"]) == (3, 2, 1, 0)
    assert ev["pass_rate"] == pytest.approx(2 / 3, abs=1e-4) and ev["checks"] == {
        "exact(label)": {"passed": 2, "cases": 3}
    }
    assert "passed=2/3" in run.summary()

    items = {i.key: i for i in run.items}
    assert items["a"].verdict["passed"] and items["a"].verdict["output"]["label"] == "refund"
    c = items["c"].verdict
    assert not c["passed"] and c["checks"]["exact(label)"]["reason"].startswith("got 'refund'")
    assert c["expected"] == {"label": "other"}
    line = json.loads((run.path / "items.jsonl").read_text().splitlines()[0])
    assert "verdict" in line

    # the runs are the eval's: origin eval, filed under evals/, one per case
    store = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    runs = store.list_runs().items
    assert sorted(r.metadata["key"] for r in runs) == ["a", "b", "c"]
    assert {r.origin for r in runs} == {"eval"} and {r.name for r in runs} == {"classify_eval"}
    assert (tmp_path / "runs" / "evals" / "classify_eval" / run.run_id).is_dir()


def test_a_threshold_gates_on_the_rate(tmp_path):
    assert _eval(tmp_path, [exact("label")], threshold=0.6).run_sync().status == "ok"
    assert _eval(tmp_path, [exact("label")], threshold=0.7).run_sync().status == "failed"
    with pytest.raises(ValueError, match="pass rate"):
        _eval(tmp_path, [], threshold=2)


def test_a_broken_case_or_evaluator_fails_its_case_only(tmp_path):
    def raises(output=None):
        raise RuntimeError("judge down")

    async def slow_ok(output=None, row=None):
        return {"passed": True, "reason": f"row {row['id']}"}

    @op
    def as_op(output=None, expected=None):
        return output["label"] == expected["label"]

    rows = CASES[:2] + [{"id": "boom", "input": "boom", "expected": {"label": "other"}}]
    run = _eval(tmp_path, [slow_ok, as_op], rows=rows).run_sync()
    items = {i.key: i for i in run.items}
    assert (
        items["a"].verdict["passed"]
        and items["a"].verdict["checks"]["slow_ok"]["reason"] == "row a"
    )
    assert items["a"].verdict["checks"]["as_op"]["passed"]  # an @op is called for its body
    boom = items["boom"].verdict
    assert (
        not boom["passed"]
        and "cannot classify" in boom["error"]
        and run.meta["eval"]["errored"] == 1
    )

    run = _eval(tmp_path, [raises], rows=CASES[:1]).run_sync()
    check = run.items[0].verdict["checks"]["raises"]
    assert not check["passed"] and check["error"] == "RuntimeError: judge down"


def test_an_eval_of_a_graph_with_doors(tmp_path):
    """A served graph is evaluated through its doors: the case's input is
    fed to ingress, what egress sends is the output."""
    from operonx.app.serve import egress, ingress

    @graph
    def door_flow():
        src = ingress()
        c = classify(text=src["item"])
        out = egress(item=c["label"])
        START >> src >> c >> out >> END

    run = Eval(
        "door_eval",
        graph=door_flow,
        dataset=_cases(tmp_path / "d.jsonl", CASES),
        evaluators=[lambda output=None, expected=None: output == expected["label"]],
        record_dir=tmp_path / "evals",
        trace=[],
    ).run_sync()
    assert [(i.key, i.verdict["passed"], i.verdict["output"]) for i in run.items] == [
        ("a", True, "refund"),
        ("b", True, "other"),
        ("c", False, "refund"),
    ]


def test_the_llm_judge_parses_a_verdict_and_keeps_its_cost(tmp_path):
    from openai.types.chat.chat_completion import ChatCompletion, Choice
    from openai.types.chat.chat_completion_message import ChatCompletionMessage
    from openai.types.completion_usage import CompletionUsage

    seen = []

    async def generate(messages, **kwargs):
        seen.append(messages)
        return ChatCompletion(
            id="m",
            created=1,
            model="judge",
            object="chat.completion",
            choices=[
                Choice(
                    index=0,
                    finish_reason="stop",
                    message=ChatCompletionMessage(
                        role="assistant",
                        content='{"passed": false, "score": 0.25, "reason": "wrong label"}',
                    ),
                )
            ],
            usage=CompletionUsage(prompt_tokens=40, completion_tokens=12, total_tokens=52),
        )

    llm = Mock()
    llm.generate = generate
    hub = Mock()
    hub.get.return_value = llm
    judge = llm_judge("llm:judge", "Is the label right? {be strict}")
    with patch("operonx.providers.ops._utils.ResourceHub") as cls:
        cls.instance.return_value = hub
        run = _eval(tmp_path, [judge], rows=CASES[:1]).run_sync()
    v = run.items[0].verdict["checks"]["llm_judge"]
    assert v["passed"] is False and v["score"] == 0.25 and v["reason"] == "wrong label"
    system = seen[0][0]["content"]
    assert "{be strict}" in system  # the rubric's braces are text, not template slots
    assert (
        "I want my money back" in seen[0][1]["content"]
        and '"label": "refund"' in seen[0][1]["content"]
    )


# ── declared in operonx.toml ──────────────────────────────────────────────

MOD = """
from operonx.core import END, START, graph, op

@op(bound="sync")
def classify(text: str = "") -> dict:
    return {"label": "refund" if "money back" in text else "other"}

@graph
def flow(text: str = ""):
    c = classify(text=text)
    START >> c >> END

def label_ok(output=None, expected=None):
    return output["label"] == expected["label"]
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    name = f"ev_{uuid.uuid4().hex[:6]}"
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(MOD), encoding="utf-8")
    (tmp_path / "datasets").mkdir()
    _cases(tmp_path / "datasets" / "labels.jsonl", CASES)
    (tmp_path / "operonx.toml").write_text(
        textwrap.dedent(f"""
        [project]
        name = "evdemo"

        [[job]]
        name       = "labels"
        graph      = "{name}:flow"
        item_input = "text"
        dataset    = "dataset:labels"
        evaluators = ["{name}:label_ok"]
        threshold  = 0.5
    """),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    yield name, tmp_path
    sys.modules.pop(name, None)


def test_a_job_block_with_a_dataset_is_an_eval(project):
    name, root = project
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # dataset/evaluators/threshold are read, not typos
        app = Application.find(root)
    described = app.describe()["jobs"][0]
    assert described["kind"] == "eval" and described["dataset"] == "dataset:labels"
    assert described["evaluators"] == [f"{name}:label_ok"]
    ev = app.job("labels")
    assert (
        isinstance(ev, Eval)
        and ev.threshold == 0.5
        and ev.dataset.path == root / "datasets" / "labels.jsonl"
    )
    d = ev.describe()
    assert d["source"] == str(root / "datasets" / "labels.jsonl") and d["sink"] is None
    run = app.run_sync("labels")
    assert run.status == "ok" and run.meta["eval"]["passed"] == 2  # 2/3 ≥ 0.5
    assert run.path.parent == root / "evals" / "labels"


def test_operonx_run_gates_on_an_eval(project):
    import subprocess

    name, root = project
    toml = root / "operonx.toml"
    toml.write_text(toml.read_text().replace("threshold  = 0.5", "threshold  = 0.9"))
    proc = subprocess.run(
        [sys.executable, "-m", "operonx.cli.run", "labels"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 1, proc.stderr
    assert "passed=2/3" in proc.stdout + proc.stderr


def test_a_plain_job_record_is_unchanged(tmp_path):
    run = Job(
        "plain",
        graph=classify_flow,
        item_input="text",
        source=["hello"],
        record_dir=tmp_path / "jobs",
        trace=[],
    ).run_sync()
    line = json.loads((run.path / "items.jsonl").read_text().splitlines()[0])
    assert "verdict" not in line and "eval" not in run.meta
