"""The A5 eval: 20 support cases through ``agent_service``'s graph, judged
by the trajectory evaluators. One pytest session is one operonx
experiment (the eval pytest plugin), recorded with its gate::

    PYTHONPATH=. uv run pytest evals -p operonx.app.evals.pytest_plugin \\
        --operonx-eval-name support --operonx-eval-dir results/eval_a5

Not part of ``tests/``: it is an eval, run on purpose, and its record is
the result.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from operonx.app.evals import trajectory
from operonx.app.evals.pytest_plugin import cases
from operonx.core.registry.resource_hub import ResourceHub

from evals.support import SERVICE, by_rule
from operonx_agents.evals import (
    no_tool_errors,
    output_valid,
    result_of,
    tool_called,
    tool_not_called,
    turns_at_most,
)
from tests.fakes import FakeHub, ScriptedLLM

DATASET = Path(__file__).parent / "datasets" / "support.jsonl"


@pytest.fixture(autouse=True)
def rule_model():
    ResourceHub.set_instance(FakeHub(support_model=ScriptedLLM(by_rule)))
    yield
    ResourceHub.reset_instance()


def ended_as_expected(output, expected):
    """The run ended as the case says, and said what it says."""
    got = result_of(output) or {}
    if got.get("status") != expected["status"]:
        return {"passed": False, "reason": f"ended {got.get('status')}: {got.get('error')}"}
    if expected["status"] == "interrupted":
        waits = [i["tool"] for i in got.get("interruptions") or ()]
        return {"passed": waits == [expected["waits_on"]], "reason": f"waits on {waits}"}
    return {"passed": got.get("output") == expected["says"], "reason": repr(got.get("output"))}


def checks_for(case):
    every = [
        trajectory.tool_calls(mode="strict", args="exact"),
        no_tool_errors(),
        turns_at_most(3),
        ended_as_expected,
    ]
    tags = set(case["tags"])
    if "chat" in tags:
        every += [tool_not_called("order_status"), tool_not_called("refund")]
    if "refund" in tags:
        every += [tool_not_called("cancel_order"), tool_called("refund", times=1)]
    if "approval" not in tags:
        every.append(output_valid(str))
    return every


@pytest.mark.parametrize("case", cases(DATASET))
async def test_support(case, run_case):
    got = await run_case(SERVICE.graph, case, evaluators=checks_for(case))
    # the run's own record: one agent op, its turns under it
    assert got.trace.path(collapse=True) == ["src", "support", "out"], got.trace.path()
    assert got.passed, got.why
