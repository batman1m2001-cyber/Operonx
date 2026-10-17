"""ClaudeCodeCoder against a scripted fake ``claude`` (tests/fake_claude.py)."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from operonx_project.coder import ClaudeCodeCoder, CoderRequest, base_env
from operonx_project.coder.roles import base_deny

FAKE = Path(__file__).with_name("fake_claude.py")


def init():
    return {"type": "system", "subtype": "init", "session_id": "$SESSION", "model": "claude-haiku", "tools": []}


def result(**kw):
    ev = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "session_id": "$SESSION",
        "num_turns": 3,
        "duration_ms": 1200,
        "total_cost_usd": 0.0123,
        "permission_denials": [],
        "usage": {"input_tokens": 4, "output_tokens": 50, "cache_read_input_tokens": 100},
    }
    ev.update(kw)
    return ev


def tool_use(tid, name, **inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def tool_result(tid, error):
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": True, "content": error}]},
    }


def rate(status, resets=None):
    info = {"status": status, "rateLimitType": "five_hour", "overageStatus": "rejected", "isUsingOverage": False}
    if resets:
        info["resetsAt"] = resets
    return {"type": "rate_limit_event", "rate_limit_info": info, "session_id": "$SESSION"}


class Fake:
    def __init__(self, tmp: Path):
        self.dir = tmp
        self.bin = tmp / "claude"
        self.bin.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
        self.bin.chmod(0o755)
        self.log = tmp / "calls.jsonl"
        self.scenarios = tmp / "scenarios.json"
        self.cwd = tmp / "wt"
        self.cwd.mkdir()

    def script(self, *scenarios):
        self.scenarios.write_text(json.dumps(list(scenarios)))

    def coder(self, **kw):
        env = {**base_env(), "FAKE_CLAUDE_LOG": str(self.log), "FAKE_CLAUDE_SCENARIOS": str(self.scenarios)}
        return ClaudeCodeCoder(binary=str(self.bin), env=env, **kw)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def req(self, **kw):
        kw.setdefault("prompt", "do it")
        kw.setdefault("cwd", self.cwd)
        return CoderRequest(**kw)


@pytest.fixture
def fake(tmp_path):
    return Fake(tmp_path)


async def test_new_session_id_is_chosen_before_start_and_reported(fake):
    fake.script({"events": [init(), tool_use("t1", "Write", file_path="a.py"), result()]})
    starts = []
    r = await fake.coder().run(fake.req(transcript=fake.dir / "t.jsonl"), on_start=starts.append)
    assert r.status == "ok" and r.text == "done"
    argv = fake.calls()[0]["argv"]
    sid = argv[argv.index("--session-id") + 1]
    assert r.session == sid and starts[0].session == sid and starts[0].pgid
    assert r.cost_usd == pytest.approx(0.0123) and r.turns == 3 and r.tokens["output_tokens"] == 50
    assert r.tools == {"Write": 1}
    # the prompt goes in on stdin as one stream-json user message
    msg = json.loads(fake.calls()[0]["stdin"])
    assert msg == {"type": "user", "message": {"role": "user", "content": "do it"}}
    assert len((fake.dir / "t.jsonl").read_text().splitlines()) == 3


async def test_resume_keeps_the_session_and_fork_reports_the_new_one(fake):
    fake.script({"events": [init(), result()]})
    r = await fake.coder().run(fake.req(session="abc"))
    argv = fake.calls()[0]["argv"]
    assert argv[argv.index("--resume") + 1] == "abc" and "--session-id" not in argv and "--fork-session" not in argv
    assert r.session == "abc"
    starts = []
    r = await fake.coder().run(fake.req(session="abc", fork=True), on_start=starts.append)
    assert "--fork-session" in fake.calls()[1]["argv"]
    assert r.session not in (None, "abc")
    assert [s.session for s in starts] == [None, r.session] and starts[1].pgid == starts[0].pgid


async def test_structured_output_comes_from_the_result(fake):
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    fake.script(
        {"events": [init(), result(structured_output={"x": 1}, result="")]},
        {"events": [init(), result(result='```json\n{"x": 2}\n```')]},
        {"events": [init(), result(result="no json here")]},
    )
    c = fake.coder()
    r = await c.run(fake.req(schema=schema))
    argv = fake.calls()[0]["argv"]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema
    assert r.status == "ok" and r.output == {"x": 1}
    assert (await c.run(fake.req(schema=schema))).output == {"x": 2}
    r = await c.run(fake.req(schema=schema))
    assert r.status == "error" and "structured output" in r.error


async def test_a_rejected_rate_limit_carries_its_reset_time(fake):
    fake.script(
        {"events": [init(), rate("rejected", 1792182600), result(is_error=True, subtype="error_during_execution")]},
        # the real shape when allowed: overageStatus "rejected" must not read as a limit
        {"events": [init(), rate("allowed", 1792182600), result()]},
        {"events": [init(), result(is_error=True, result="Claude AI usage limit reached|1792190000")]},
    )
    c = fake.coder()
    r = await c.run(fake.req())
    assert r.status == "rate_limited" and r.resets_at == 1792182600
    r = await c.run(fake.req())
    assert r.status == "ok" and r.rate["status"] == "allowed"
    r = await c.run(fake.req())
    assert r.status == "rate_limited" and r.resets_at == 1792190000


async def test_auth_budget_error_and_infra_are_told_apart(fake):
    fake.script(
        {"events": [], "stderr": "Invalid API key · Please run /login", "exit": 1},
        {
            "events": [
                init(),
                result(
                    is_error=True,
                    subtype="error_max_budget_usd",
                    result=None,
                    errors=["Reached maximum budget ($0.0001)"],
                ),
            ]
        },
        {"events": [init(), result(is_error=True, subtype="error_max_turns", result=None)]},
        {"events": [init()], "exit": 3},
    )
    c = fake.coder()
    assert (await c.run(fake.req())).status == "auth"
    r = await c.run(fake.req(max_usd=0.0001))
    assert r.status == "budget" and "maximum budget" in r.error
    assert "--max-budget-usd" in fake.calls()[1]["argv"]
    assert (await c.run(fake.req())).status == "error"
    r = await c.run(fake.req())
    assert r.status == "infra" and "exited 3" in r.error


async def test_a_missing_binary_is_infra(fake):
    r = await ClaudeCodeCoder(binary=str(fake.dir / "nope")).run(fake.req())
    assert r.status == "infra"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie is gone for our purposes
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


async def _gone(pid: int, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


async def test_timeout_stops_the_whole_process_group(fake):
    fake.script({"events": [init()], "hang": 60, "child": True})
    t0 = time.monotonic()
    r = await fake.coder().run(fake.req(timeout_s=1.5))
    assert r.status == "timeout" and time.monotonic() - t0 < 10
    child = int((fake.dir / "scenarios.json.child").read_text())
    assert await _gone(child), "the coder's own children must die with it"


async def test_cancelling_the_task_stops_the_session(fake):
    fake.script({"events": [init()], "hang": 60, "child": True})
    starts = []
    task = asyncio.create_task(fake.coder().run(fake.req(), on_start=starts.append))
    child_file = fake.dir / "scenarios.json.child"
    for _ in range(200):
        if child_file.exists() and child_file.read_text():
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _gone(int(child_file.read_text()))
    assert await _gone(starts[0].pgid)


async def test_tool_errors_and_denials_are_recorded(fake):
    denial = {"tool_name": "Read", "tool_use_id": "t2", "tool_input": {"file_path": "/x/.env"}}
    fake.script(
        {
            "events": [
                init(),
                tool_use("t1", "Bash", command="pytest"),
                tool_result("t1", "Exit code 1\nboom"),
                tool_use("t2", "Read", file_path="/x/.env"),
                tool_result("t2", "<tool_use_error>File is in a directory that is denied</tool_use_error>"),
                result(permission_denials=[denial]),
            ]
        }
    )
    r = await fake.coder().run(fake.req())
    assert r.status == "ok"
    assert [e["tool"] for e in r.tool_errors] == ["Bash", "Read"]
    assert r.denials == [denial]


async def test_a_sandbox_that_cannot_start_fails_closed(fake):
    # the real words from Ubuntu 24.04 (P2 smoke): every command fails before it runs
    err = (
        "Exit code 1\napply-seccomp: write /proc/self/setgroups (nested userns is capability-restricted; "
        "caller must provide CAP_SYS_ADMIN): Permission denied"
    )
    fake.script({"events": [init(), tool_use("t1", "Bash", command="ls"), tool_result("t1", err), result()]})
    r = await fake.coder().run(fake.req())
    assert r.status == "infra" and r.error.startswith("sandbox unavailable")


async def test_unreadable_lines_are_skipped(fake):
    fake.script({"events": [init(), "raw:not json {", "[1, 2]", result()]})
    r = await fake.coder().run(fake.req())
    assert r.status == "ok"


def test_the_child_environment_is_scrubbed(monkeypatch, tmp_path):
    venv = tmp_path / "venv"
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.setenv("PATH", f"{venv}/bin{os.pathsep}/usr/bin")
    env = base_env()
    assert not any(k.startswith(("CLAUDE", "ANTHROPIC")) for k in env)
    assert "VIRTUAL_ENV" not in env and env["PATH"] == "/usr/bin" and "HOME" in env
    assert base_env(keep_anthropic=True)["ANTHROPIC_API_KEY"] == "sk-test"


def test_roles_decide_tools_sandbox_and_denials(tmp_path):
    state = tmp_path / "state"
    c = ClaudeCodeCoder(binary="claude", state_dirs=[str(state)])
    impl = c.command(CoderRequest(prompt="", cwd=tmp_path), "claude", "sid")
    rev = c.command(CoderRequest(prompt="", cwd=tmp_path, role="reviewer"), "claude", "sid")
    assert impl[impl.index("--tools") + 1] == "Read,Grep,Glob,Edit,Write,Bash"
    assert rev[rev.index("--tools") + 1] == "Read,Grep,Glob"
    assert "--restricted" in impl and "--restricted" in rev
    settings = json.loads(impl[impl.index("--settings") + 1])["sandbox"]
    assert settings["enabled"] and settings["failIfUnavailable"] and settings["allowUnsandboxedCommands"] is False
    assert settings["network"]["allowedDomains"] == []
    assert "--settings" not in rev  # no shell, no sandbox to set
    # --allowedTools is last: it takes the rest of the arguments
    assert impl[impl.index("--allowedTools") :] == ["--allowedTools", "Read", "Grep", "Glob", "Edit", "Write", "Bash"]
    deny = impl[impl.index("--disallowedTools") + 1 : impl.index("--allowedTools")]
    assert deny == base_deny([str(state.resolve())])
    assert "Read(**/.env)" in deny and f"Read(/{state.resolve()}/**)" in deny and "Bash(git push:*)" in deny
    off = c.command(CoderRequest(prompt="", cwd=tmp_path, sandbox=False), "claude", "sid")
    assert "--settings" not in off


def test_an_unknown_role_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown coder role"):
        ClaudeCodeCoder().command(CoderRequest(prompt="", cwd=tmp_path, role="boss"), "claude", None)
