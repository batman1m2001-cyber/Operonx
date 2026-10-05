"""DX_PLAN X4: operonx's own log level, logs on stderr, a slow-op threshold
that is set, and ops whose slowness is not news left alone."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap

import pytest

import operonx.core.ops.base as base
from operonx import END, START, Operon, graph, op
from operonx.core.loggings.config import LogConfig

pytestmark = pytest.mark.unit


def test_operonx_log_level_wins_over_log_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("OPERONX_LOG_LEVEL", "ERROR")
    assert LogConfig().level == "ERROR"
    monkeypatch.delenv("OPERONX_LOG_LEVEL")
    assert LogConfig().level == "INFO"
    monkeypatch.delenv("LOG_LEVEL")
    assert LogConfig().level == "WARNING"


def test_logs_go_to_stderr_and_stdout_stays_the_programs(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    script = tmp_path / "child.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import asyncio, sys
            sys.path.insert(0, {root!r})
            from operonx import END, START, Operon, graph, op

            @op
            def one(x: int) -> dict:
                return {{"y": x + 1}}

            @graph
            def g(x):
                o = one(x=x)
                START >> o >> END

            print("OUT", asyncio.run(Operon(g, params={{"x": None}}).run({{"x": 1}}))["y"])
            """
        )
    )
    env = {**os.environ, "OPERONX_LOG_LEVEL": "INFO"}
    done = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "OUT 2"
    assert "Running workflow g" in done.stderr


@op
async def plain(x: int) -> dict:
    await asyncio.sleep(0.02)
    return {"y": x}


@op
async def gen(y: int):
    for i in range(2):
        await asyncio.sleep(0.02)
        yield {"z": y + i}


@graph
def flow(x):
    p = plain(x=x)
    g = gen(y=p["y"])
    START >> p >> g >> END


def test_the_slow_op_threshold_is_set_and_streams_are_not_timed(monkeypatch, caplog):
    monkeypatch.setattr(base, "_SLOW_OP_MS", 10.0)
    asyncio.run(Operon(flow, params={"x": None}).run({"x": 1}))
    slow = [r.getMessage() for r in caplog.records if "Slow op" in r.getMessage()]
    assert any(".p:" in m for m in slow)  # 20 ms > 10 ms
    assert not any(".g:" in m for m in slow)  # a generator's time is its stream's
    assert not any(m.split("Slow op ")[1].startswith("flow:") for m in slow)

    caplog.clear()
    monkeypatch.setattr(base, "_SLOW_OP_MS", 10_000.0)
    asyncio.run(Operon(flow, params={"x": None}).run({"x": 1}))
    assert not [r for r in caplog.records if "Slow op" in r.getMessage()]
