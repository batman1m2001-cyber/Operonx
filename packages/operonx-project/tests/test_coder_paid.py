"""The real Claude Code, once (paid: about $0.01 on Haiku). Run with ``-m paid``.

It checks the event shapes the adapter parses: a session that writes a
file, is resumed, and answers a JSON schema; a permission denial; the
budget cap. P2 ran it on Claude Code 2.1.301 (plan §17).
"""

import subprocess

import pytest

from operonx_project.coder import ClaudeCodeCoder, CoderRequest, find_claude

pytestmark = [pytest.mark.paid, pytest.mark.skipif(find_claude() is None, reason="no claude binary")]


@pytest.fixture
def wt(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


async def test_session_writes_resumes_and_answers_a_schema(wt):
    c = ClaudeCodeCoder(model="haiku")
    starts = []
    r1 = await c.run(
        CoderRequest(
            prompt="Use the Write tool once to create hello.txt containing the single line: hi. Then stop.",
            cwd=wt,
            sandbox=False,
            max_usd=0.3,
            timeout_s=300,
        ),
        on_start=starts.append,
    )
    assert r1.status == "ok", r1.error
    assert r1.session == starts[0].session and r1.cost_usd > 0 and r1.tools.get("Write") == 1
    assert (wt / "hello.txt").read_text().strip() == "hi"
    schema = {"type": "object", "properties": {"files": {"type": "array", "items": {"type": "string"}}}}
    schema["required"] = ["files"]
    r2 = await c.run(
        CoderRequest(prompt="Which files did you create? Names only.", cwd=wt, session=r1.session, schema=schema,
                     role="reviewer", max_usd=0.3, timeout_s=300)
    )  # fmt: skip
    assert r2.status == "ok" and r2.session == r1.session
    assert any(f.endswith("hello.txt") for f in r2.output["files"])


async def test_a_denied_read_and_the_budget_cap(wt):
    (wt / ".env").write_text("SECRET=1\n")
    c = ClaudeCodeCoder(model="haiku")
    r = await c.run(
        CoderRequest(role="reviewer", prompt="Read .env once with the Read tool, do not retry, then stop.", cwd=wt,
                     max_usd=0.3, timeout_s=300)
    )  # fmt: skip
    assert r.status == "ok" and [d["tool_name"] for d in r.denials] == ["Read"]
    r = await c.run(CoderRequest(role="reviewer", prompt="Write a long poem.", cwd=wt, max_usd=0.0001, timeout_s=300))
    assert r.status == "budget"
