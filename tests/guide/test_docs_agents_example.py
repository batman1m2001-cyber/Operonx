"""The "A working agent" example in docs/guide/05-agents.md runs and answers.

The human guide is not a scratch project the way ``operonx/guide`` is —
its snippets lean on context from the prose — but its first agent
example is the one people copy whole, and it used to hand raw ``LLMOp``
output to ``build_react_agent``. The loop reads ``assistant_message`` and
``done``, which ``LLMOp`` does not produce, so the run ended with no
answer and the example's ``answer["final"]["content"]`` raised.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "guide" / "05-agents.md"


def _working_agent_snippet() -> str:
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## A working agent", 1)[1]
    return re.search(r"^```python\n(.*?)^```\s*$", section, re.S | re.M).group(1)


def test_the_working_agent_example_answers(tmp_path: Path, fake_llm):
    (tmp_path / "resources.yaml").write_text(
        "llm:gpt-4o:\n"
        "  api_type: openai\n"
        "  api_key: sk-local-test\n"
        f"  base_url: {fake_llm}\n"
        "  model: gpt-4o\n",
        encoding="utf-8",
    )
    (tmp_path / "example.py").write_text(_working_agent_snippet(), encoding="utf-8")
    env = {**os.environ, "OPERONX_RUNS_DIR": str(tmp_path / "runs")}
    got = subprocess.run(
        [sys.executable, "example.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert got.returncode == 0, got.stderr[-3000:]
    # The stand-in echoes the last user turn: the answer reached `final`.
    assert "Echo: Weather in Hanoi?" in got.stdout
    assert "1 turns, stopped_early=False" in got.stdout
