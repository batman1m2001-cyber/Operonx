"""End-to-end against a real tool-calling model.

Everything else in this suite is a unit test, and unit tests on an agent
prove the code is self-consistent — never that a model can actually drive
it. Four defects in the framework's own agent layer survived a full unit
suite and were found by the first live run.

Run with::

    export OPERONX_TEST_LLM_URL=... OPERONX_TEST_LLM_KEY=... OPERONX_TEST_LLM_MODEL=...
    uv run pytest packages/operonx-code/tests/test_live_coding.py -m integration
"""

from __future__ import annotations

import asyncio
import os
import textwrap
from pathlib import Path

import pytest
from operonx_code.agent import build_coding_agent

from operonx.agents import AgentSession
from operonx.core.registry.resource_hub import ResourceHub

pytestmark = [pytest.mark.integration]

URL = os.getenv("OPERONX_TEST_LLM_URL", "")
KEY = os.getenv("OPERONX_TEST_LLM_KEY", "")
MODEL = os.getenv("OPERONX_TEST_LLM_MODEL", "")

requires_llm = pytest.mark.skipif(
    not (URL and MODEL),
    reason="set OPERONX_TEST_LLM_URL / _KEY / _MODEL to run live coding tests",
)

RESOURCE = "codetest"


@pytest.fixture(scope="module", autouse=True)
def _hub(tmp_path_factory):
    if not (URL and MODEL):
        yield
        return
    path = tmp_path_factory.mktemp("live") / "resources.yaml"
    path.write_text(
        textwrap.dedent(f"""
            llm:
              {RESOURCE}:
                api_type: openai
                api_key: {KEY or "unused"}
                base_url: {URL}
                model: {MODEL}
        """).strip(),
        encoding="utf-8",
    )
    try:
        previous = ResourceHub.instance()
    except RuntimeError:
        previous = None
    ResourceHub.set_instance(ResourceHub.from_yaml(str(path)))
    yield
    if previous is not None:
        ResourceHub.set_instance(previous)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
    )
    (tmp_path / "notes.md").write_text("# notes\nThe magic number is 4712.\n")
    return tmp_path


def approve_everything(session):
    """Auto-approve. A live test that leaves an approval unanswered does
    not fail — it waits out `approval_timeout` and then reports a denial,
    which reads as a model problem."""

    def sink(event):
        session.approve(event, True, "auto-approved in tests")

    return sink


@requires_llm
@pytest.mark.asyncio
async def test_it_finds_an_answer_in_the_files(project):
    """The minimum bar: the model uses a tool rather than guessing."""
    async with build_coding_agent(
        root=project, resource=RESOURCE, max_turns=12, temperature=0
    ) as agent:
        session = AgentSession(agent.graph, timeout=300)
        result = await asyncio.wait_for(
            session.send("What is the magic number mentioned in the notes?"),
            timeout=300,
        )
    answer = (result["final"] or {}).get("content", "")
    assert "4712" in answer, f"expected the number from notes.md, got: {answer!r}"


@requires_llm
@pytest.mark.asyncio
async def test_it_reads_before_editing(project):
    """The read-before-edit invariant has to be satisfiable by a model, not
    just enforceable by us. If it cannot recover from the refusal, the
    invariant is a wall rather than a guard rail."""
    async with build_coding_agent(
        root=project, resource=RESOURCE, max_turns=20, approval_timeout=20, temperature=0
    ) as agent:
        session = AgentSession(agent.graph, timeout=400)
        result = await asyncio.wait_for(
            session.send(
                "add() in calc.py subtracts instead of adding. Fix it with the edit tool.",
                on_approval=approve_everything(session),
            ),
            timeout=400,
        )

    source = (project / "calc.py").read_text()
    assert "a + b" in source, f"add() was not fixed; file is:\n{source}"
    assert "a * b" in source, "mul() must be left alone"

    used = [m for m in result["messages"] if m.get("role") == "tool"]
    assert used, "the model answered without calling a tool"


@requires_llm
@pytest.mark.asyncio
async def test_a_denied_tool_does_not_end_the_run(project):
    """Denial produces a tool message the model can read, so it keeps its
    turn and can explain or try something else."""
    async with build_coding_agent(
        root=project, resource=RESOURCE, max_turns=10, approval_timeout=20, temperature=0
    ) as agent:
        session = AgentSession(agent.graph, timeout=300)

        def deny(event):
            session.approve(event, False, "denied by the test")

        result = await asyncio.wait_for(
            session.send("Delete calc.py using bash.", on_approval=deny),
            timeout=300,
        )

    assert (project / "calc.py").exists(), "a denied call must not run"
    assert (result["final"] or {}).get("role") == "assistant", (
        "the agent must still answer after a denial"
    )


@requires_llm
@pytest.mark.asyncio
async def test_the_shell_keeps_state_across_tool_calls(project):
    """The reason the shell is persistent at all — verified through the
    model rather than through the class."""
    async with build_coding_agent(
        root=project, resource=RESOURCE, max_turns=16, approval_timeout=20, temperature=0
    ) as agent:
        (project / "deep").mkdir()
        (project / "deep" / "marker.txt").write_text("found-me\n")
        session = AgentSession(agent.graph, timeout=400)
        result = await asyncio.wait_for(
            session.send(
                "Using two separate bash calls: first cd into the deep directory, "
                "then cat marker.txt without using a path. Tell me what it says.",
                on_approval=approve_everything(session),
            ),
            timeout=400,
        )
    assert "found-me" in (result["final"] or {}).get("content", "")
