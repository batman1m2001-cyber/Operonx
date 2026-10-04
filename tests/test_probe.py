"""``probe``'s verdicts from the A0 measurements (``docs/AGENTS_V2_PLAN.md``
§2c, recorded by the spike): ``inhouse`` declares ``native``,
``qwen3.7-plus`` ``tool``, and the dead ``qwen-turbo`` nothing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operonx_agents.probe import ProbeReport, requests_for, summarize

A0 = json.loads(Path(__file__).with_name("fixtures").joinpath("probe_a0.json").read_text())


def verdict(name: str) -> ProbeReport:
    raw = dict(A0[name])
    model = raw.pop("model", name)
    return summarize(ProbeReport(resource=name, model=model, results=raw))


def test_inhouse_is_native():
    r = verdict("inhouse")
    assert r.declare == "native"
    assert r.json_schema.startswith("enforced")
    assert r.forced_tool.startswith("unsupported") and "--tool-call-parser" in r.forced_tool
    assert r.logprobs == "returned"


def test_qwen37_plus_is_tool():
    r = verdict("qwen3.7-plus")
    assert r.declare == "tool"
    assert "silently ignored" in r.json_schema
    assert r.forced_tool.startswith("forced, but arguments not schema-checked")
    assert r.logprobs == "returned"


def test_qwen_turbo_is_unavailable():
    r = verdict("qwen-turbo")
    assert r.declare is None
    assert "no vendors found" in r.json_schema


def test_the_controls_are_built_to_fail():
    reqs = requests_for("m")
    assert reqs["bad-schema"]["response_format"]["json_schema"]["schema"] == {
        "type": "no-such-type"
    }
    assert reqs["bad-tool"]["tool_choice"]["function"]["name"] not in {
        t["function"]["name"] for t in reqs["bad-tool"]["tools"]
    }


@pytest.mark.parametrize("kind", ["plain", "schema", "tool", "bad-schema", "bad-tool"])
def test_every_request_targets_the_model(kind):
    assert requests_for("gemma")[kind]["model"] == "gemma"
