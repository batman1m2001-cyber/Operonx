"""An eval run's fingerprint (`operonx.app.evals.fingerprint`).

Gates: the same code gives the same fingerprint in two processes; an op
body edit, a literal param or an inline prompt changes ``graph_hash`` and
nothing else; a model swap in resources.yaml changes ``config_hash`` and
nothing else; a rotated API key changes nothing, and no secret is in what
is hashed; the root graph's name (the engine's variable) is not identity;
a dirty tree is flagged; an edited case changes ``dataset_version`` and
that case's ``case_hash`` only; evaluator versions follow their source and
closure; a graph that cannot serialize says why; the fingerprint lands on
the run record.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from operonx.app.evals import Eval, contains, exact, llm_judge
from operonx.app.evals.fingerprint import (
    case_hash,
    config_spec,
    dataset_version,
    digest,
    evaluator_version,
    fingerprint,
    graph_spec,
)
from operonx.core import END, PARENT, START, GraphOp, Operon, graph, op

# ── same code, two processes ──────────────────────────────────────────────

MOD = """
from operonx.core import END, START, graph, op

@op(bound="sync")
def classify(text: str = "", mode: str = "x") -> dict:
    return {"label": "refund" if "money back" in text else "other"}

@graph
def flow(text: str = ""):
    c = classify(text=text, mode="strict")
    START >> c >> END

def label_ok(output=None, expected=None):
    return output["label"] == expected["label"]
"""

PRINT = """
import json, sys
from operonx.app.evals import Eval
from operonx.app.evals.fingerprint import fingerprint
import mod

ev = Eval("e", graph=mod.flow, dataset="cases.jsonl", evaluators=[mod.label_ok], input="text")
rows = ev.dataset.rows()
fp = fingerprint(graph=ev.engine().graph, rows=rows, evaluators={"label_ok": mod.label_ok})
print(json.dumps(fp))
"""


def _fp_in_a_process(root: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(root)}
    got = subprocess.run(
        [sys.executable, "-c", PRINT], cwd=root, env=env, capture_output=True, text=True, timeout=60
    )
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout.strip().splitlines()[-1])


def test_the_same_code_gives_the_same_fingerprint_in_two_processes(tmp_path):
    (tmp_path / "mod.py").write_text(textwrap.dedent(MOD), encoding="utf-8")
    (tmp_path / "cases.jsonl").write_text('{"id": "a", "input": "hi"}\n', encoding="utf-8")
    one, two = _fp_in_a_process(tmp_path), _fp_in_a_process(tmp_path)
    assert one == two
    assert all(len(one[k]) == 12 for k in ("graph_hash", "config_hash", "dataset_version"))

    # an op body edit is a new graph, even with no commit to show it
    (tmp_path / "mod.py").write_text(
        textwrap.dedent(MOD).replace('"other"}', '"other", "v": 2}'), encoding="utf-8"
    )
    body = _fp_in_a_process(tmp_path)
    assert body["graph_hash"] != one["graph_hash"]
    assert {k: v for k, v in body.items() if k != "graph_hash"} == {
        k: v for k, v in one.items() if k != "graph_hash"
    }

    # so is a literal param
    (tmp_path / "mod.py").write_text(
        textwrap.dedent(MOD).replace('mode="strict"', 'mode="loose"'), encoding="utf-8"
    )
    assert _fp_in_a_process(tmp_path)["graph_hash"] not in (one["graph_hash"], body["graph_hash"])


@op(bound="sync")
def step(x: int = 0) -> dict:
    return {"y": x}


@graph
def g(x: int = 0):
    s = step(x=x)
    START >> s >> END


def test_the_engines_variable_name_is_not_identity():
    first = Operon(g, params={"x": None})
    another_name = Operon(g, params={"x": None})
    assert first.graph.name != another_name.graph.name  # precondition
    assert digest(graph_spec(first.graph.serialize())) == digest(
        graph_spec(another_name.graph.serialize())
    )


# ── resources: config, secrets ────────────────────────────────────────────

RESOURCES = """
llm:judge:
  api_type: openai
  api_key: {key}
  base_url: https://bob:hunter2@llm.example.com/v1?token=qs-secret
  model: {model}
"""


def _llm_graph(tmp_path, *, key="sk-AAA-secret", model="model-a", system="Be brief."):
    from operonx.core.registry import ResourceHub
    from operonx.providers.ops import LLMOp

    path = tmp_path / f"res-{key}-{model}.yaml"
    path.write_text(RESOURCES.format(key=key, model=model), encoding="utf-8")
    ResourceHub.set_instance(ResourceHub.from_yaml(path))
    with GraphOp(name="answer") as g:
        llm = LLMOp.of(resource="judge", prompt={"system": system, "user": "{q}"}, q=PARENT["q"])
        START >> llm >> END
    return g.serialize()


@pytest.fixture
def hub_reset():
    from operonx.core.registry import ResourceHub

    yield
    ResourceHub.reset_instance()


def test_models_are_the_resolved_configs_models(tmp_path, hub_reset):
    from operonx.app.evals.fingerprint import models_of

    assert models_of(config_spec(_llm_graph(tmp_path))) == ["model-a"]
    assert models_of(config_spec(_llm_graph(tmp_path, model="model-b"))) == ["model-b"]


def test_config_and_graph_hashes_split_what_changed(tmp_path, hub_reset):
    base = _llm_graph(tmp_path)
    h = lambda spec: (digest(graph_spec(spec)), digest(config_spec(spec)))  # noqa: E731

    g0, c0 = h(base)
    g1, c1 = h(_llm_graph(tmp_path, system="Be thorough."))
    assert g1 != g0 and c1 == c0  # an inline prompt is the graph's
    g2, c2 = h(_llm_graph(tmp_path, model="model-b"))
    assert g2 == g0 and c2 != c0  # a model swap in resources.yaml is config
    g3, c3 = h(_llm_graph(tmp_path, key="sk-BBB-rotated"))
    assert (g3, c3) == (g0, c0)  # a rotated key is the same experiment


def test_no_secret_reaches_what_is_hashed(tmp_path, hub_reset):
    spec = _llm_graph(tmp_path)
    assert "sk-AAA-secret" in json.dumps(spec, default=str)  # precondition: serialize has it
    hashed = json.dumps(config_spec(spec)) + json.dumps(graph_spec(spec))
    for secret in ("sk-AAA-secret", "hunter2", "bob", "qs-secret"):
        assert secret not in hashed
    assert "https://llm.example.com/v1" in hashed and "model-a" in hashed


# ── code version ──────────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_a_dirty_tree_is_flagged(tmp_path):
    (tmp_path / "flow.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "flow.py")
    _git(tmp_path, "commit", "-q", "-m", "one")
    with GraphOp(name="empty") as g:
        pass
    clean = fingerprint(graph=g, rows=[], evaluators={}, root=tmp_path)
    assert len(clean["code_version"]) == 12 and clean["version_dirty"] is False
    (tmp_path / "flow.py").write_text("x = 2\n", encoding="utf-8")
    dirty = fingerprint(graph=g, rows=[], evaluators={}, root=tmp_path)
    assert dirty["code_version"] == clean["code_version"] and dirty["version_dirty"] is True
    outside = fingerprint(graph=g, rows=[], evaluators={}, root=tmp_path / "..")
    assert outside["code_version"] is None or outside["code_version"] != clean["code_version"]

    # an eval asks git at its root (once per process: the tree is dirty now)
    data = tmp_path / "cases.jsonl"
    data.write_text('{"id": "a", "input": "money back", "expected": {"label": "refund"}}\n')
    run = Eval(
        "fp",
        graph=classify_flow,
        input="text",
        dataset=data,
        evaluators=[exact("label")],
        record_dir=tmp_path / "evals",
        trace=[],
        root=tmp_path,
    ).run_sync()
    on_record = run.meta["eval"]["fingerprint"]
    assert (on_record["code_version"], on_record["version_dirty"]) == (clean["code_version"], True)


# ── cases and evaluators ──────────────────────────────────────────────────


def test_an_edited_case_changes_the_dataset_and_only_its_own_hash():
    rows = [
        {"id": "a", "input": "x", "expected": 1},
        {"id": "b", "input": "y", "expected": 2},
    ]
    edited = [rows[0], dict(rows[1], expected=3)]
    assert dataset_version(rows) != dataset_version(edited)
    assert dataset_version(rows) == dataset_version(list(reversed(rows)))  # sorted by id
    assert case_hash(rows[0]) == case_hash(edited[0])
    assert case_hash(rows[1]) != case_hash(edited[1])
    assert case_hash(dict(rows[0], note="why")) == case_hash(rows[0])  # a note is not the case


def test_evaluator_versions_follow_source_and_closure():
    assert evaluator_version(contains("a")) == evaluator_version(contains("a"))
    assert evaluator_version(contains("a")) != evaluator_version(contains("b"))
    assert evaluator_version(exact("label")) != evaluator_version(exact("intent"))
    polite = llm_judge("llm:judge", "Is it polite?")
    assert evaluator_version(polite) == evaluator_version(llm_judge("llm:judge", "Is it polite?"))
    assert evaluator_version(polite) != evaluator_version(llm_judge("llm:judge", "Is it kind?"))
    assert evaluator_version(polite) != evaluator_version(llm_judge("llm:other", "Is it polite?"))

    def mine(output=None):
        return bool(output)

    mine.eval_version = "2"
    assert evaluator_version(mine) == "2"

    @op
    def as_op(output=None):
        return bool(output)

    @op
    def as_op_v2(output=None):
        return output is not None

    assert evaluator_version(as_op) != evaluator_version(as_op_v2)  # an @op is its body


def test_a_graph_that_cannot_serialize_says_why():
    @op
    def s(x=None):
        return {"x": 1}

    with GraphOp(name="g") as g:
        a = s(name="a")
        b = s(name="b")
        START >> a >> b >> END
        b >> a  # a back-edge: rewritten into a synthetic loop
    g.build()
    fp = fingerprint(graph=g, rows=[], evaluators={})
    assert fp["graph_hash"] is None and fp["config_hash"] is None
    assert "synthetic" in fp["graph_hash_error"]
    assert fp["dataset_version"] and fp["operonx_version"]


# ── on the record ─────────────────────────────────────────────────────────


@op(bound="sync")
def classify(text: str = "") -> dict:
    return {"label": "refund" if "money back" in text else "other"}


@graph
def classify_flow(text: str = ""):
    c = classify(text=text)
    START >> c >> END


def test_the_fingerprint_is_on_the_run_record(tmp_path):
    import operonx

    data = tmp_path / "cases.jsonl"
    data.write_text('{"id": "a", "input": "money back", "expected": {"label": "refund"}}\n')

    def run():
        return Eval(
            "fp",
            graph=classify_flow,
            input="text",
            dataset=data,
            evaluators=[exact("label")],
            record_dir=tmp_path / "evals",
            trace=[],
        ).run_sync()

    first = run().meta["eval"]["fingerprint"]
    assert set(first) == {
        "code_version",
        "version_dirty",
        "graph_hash",
        "config_hash",
        "dataset_version",
        "evaluators",
        "evaluators_hash",
        "operonx_version",
        "models",
    }
    assert first["models"] == []  # no LLM in the system: nothing a judge could favour
    assert first["evaluators"] == {"exact(label)": evaluator_version(exact("label"))}
    assert first["operonx_version"] == operonx.__version__
    assert run().meta["eval"]["fingerprint"] == first
    data.write_text('{"id": "a", "input": "money back", "expected": {"label": "other"}}\n')
    edited = run().meta["eval"]["fingerprint"]
    assert edited["dataset_version"] != first["dataset_version"]
    assert {k: v for k, v in edited.items() if k != "dataset_version"} == {
        k: v for k, v in first.items() if k != "dataset_version"
    }


# ── a Python transform on a Ref ────────────────────────────────────────────

APPLY_MOD = """
from operonx.core import END, START, graph, op
from operonx.core.ops import if_

def shout(text):
    return text.upper()

@op(bound="sync")
def echo(text: str = "") -> dict:
    return {"text": text}

@op(bound="sync")
def loud(text: str = "") -> dict:
    return {"label": "loud"}

@op(bound="sync")
def calm(text: str = "") -> dict:
    return {"label": "calm"}

@graph
def flow(text: str = ""):
    e = echo(text=text)
    a, b = loud(text=e["text"].apply(shout)), calm(text=text)
    route = if_(e["text"].apply(shout) == "HI", a).else_(b)
    START >> e >> route
    a >> END
    b >> END

def label_ok(output=None, expected=None):
    return True
"""


def test_a_python_transform_is_hashed_by_its_name_and_body(tmp_path):
    """`Ref.apply(fn)` used to make serialize() raise a ValueError (an
    operonx-rs FFI rule), so the fingerprint of such a graph crashed. The
    callable is part of the graph: hashed like an op's body."""
    (tmp_path / "mod.py").write_text(textwrap.dedent(APPLY_MOD), encoding="utf-8")
    (tmp_path / "cases.jsonl").write_text('{"id": "a", "input": "hi"}\n', encoding="utf-8")
    one, two = _fp_in_a_process(tmp_path), _fp_in_a_process(tmp_path)
    assert one == two and one["graph_hash"] and "graph_hash_error" not in one

    # the transform's body is the graph's: a change to it is a new graph
    (tmp_path / "mod.py").write_text(
        textwrap.dedent(APPLY_MOD).replace("text.upper()", "text.lower()"), encoding="utf-8"
    )
    edited = _fp_in_a_process(tmp_path)
    assert edited["graph_hash"] != one["graph_hash"]
    assert edited["config_hash"] == one["config_hash"]


def test_a_python_transform_serializes_as_a_callable_ref():
    from operonx.core.states.ref import Ref

    def norm(text):
        return text.strip()

    spec = Ref("src", "text").apply(norm).serialize()
    ((name, args),) = spec["transforms"]
    assert name == "apply" and args[0] == {"python_callable": norm}
