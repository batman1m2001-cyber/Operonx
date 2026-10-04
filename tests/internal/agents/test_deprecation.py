"""`operonx.agents` is deprecated (AGENTS_V2_PLAN D3): agents moved to the
`operonx-agents` distribution. The package keeps working for one release
after operonx-agents 1.0, and says so — once, when it is imported, naming
where to go and what to read.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


def _import_in_a_fresh_process(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-W", "always::DeprecationWarning", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_importing_it_warns_once_and_says_where_agents_went():
    got = _import_in_a_fresh_process(
        "import operonx.agents, operonx.agents.tool; "
        "from operonx.agents import build_react_agent, tool"
    )
    assert got.returncode == 0, got.stderr
    warned = [line for line in got.stderr.splitlines() if "DeprecationWarning" in line]
    assert len(warned) == 1, got.stderr
    (line,) = warned
    assert "operonx.agents is deprecated" in line
    assert "operonx-agents" in line and "operonx_agents" in line
    assert "MIGRATION.md" in line


def test_it_still_works():
    with pytest.warns(DeprecationWarning):
        import importlib

        import operonx.agents

        importlib.reload(operonx.agents)
    from operonx.agents import ToolPolicy, build_react_agent, tool

    assert callable(build_react_agent) and callable(tool) and ToolPolicy


def test_importing_operonx_alone_does_not_warn():
    got = _import_in_a_fresh_process("import operonx, operonx.app, operonx.app.serve")
    assert got.returncode == 0 and "DeprecationWarning" not in got.stderr, got.stderr
