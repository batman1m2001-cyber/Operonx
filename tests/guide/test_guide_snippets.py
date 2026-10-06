"""Every snippet in the agent guide (operonx/guide/*.md) runs.

The guide is read by coding assistants that copy what it shows, so a
snippet that does not run is a bug. Each page is a scratch project:

* a fence tagged ``file=NAME`` (```python file=main.py, ```toml
  file=operonx.toml …) is written into the page's directory, in order;
* a plain ```python fence is run as a script there, in its own process
  (the resource hub and other process state never leak between snippets);
* a ```bash fence tagged ``run`` has each line run there as a command;
* a page marked ``<!-- requires: <module> -->`` runs only where that module
  is installed (the agents page needs ``operonx_agents``).

A page that talks to a model gets a local OpenAI-compatible stand-in, so
nothing here needs a key or the network.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

# One child interpreter per snippet: about a minute. Run with -m slow.
pytestmark = pytest.mark.slow

GUIDE = Path(__file__).resolve().parents[2] / "operonx" / "guide"
FENCE = re.compile(r"^```(\w+)([^\n]*)\n(.*?)^```\s*$", re.S | re.M)
PAGES = sorted(GUIDE.glob("*.md"))


def _blocks(page: Path):
    for m in FENCE.finditer(page.read_text(encoding="utf-8")):
        lang, info, body = m.group(1), m.group(2).strip(), m.group(3)
        line = page.read_text(encoding="utf-8")[: m.start()].count("\n") + 1
        yield (
            lang,
            dict(kv.split("=", 1) if "=" in kv else (kv, True) for kv in info.split()),
            body,
            line,
        )


def _run(cmd, cwd: Path, env: dict, where: str, timeout: float = 120) -> None:
    got = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if got.returncode != 0:
        raise AssertionError(
            f"{where} failed ({got.returncode}):\n--- stdout\n{got.stdout[-3000:]}"
            f"\n--- stderr\n{got.stderr[-3000:]}"
        )


#: ``<!-- requires: operonx_agents -->`` on a page: its snippets import a
#: package operonx does not depend on, and run where it is installed.
REQUIRES = re.compile(r"<!--\s*requires:\s*([\w.]+)\s*-->")


@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
def test_every_snippet_on_the_page_runs(page: Path, tmp_path: Path, fake_llm):
    for module in REQUIRES.findall(page.read_text(encoding="utf-8")):
        pytest.importorskip(module, reason=f"{page.name} needs {module}")
    env = {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "OPERONX_RUNS_DIR": str(tmp_path / "runs"),
        "LLM_BASE_URL": fake_llm,
        "LLM_API_KEY": "sk-local-test",
    }
    env.pop("VIRTUAL_ENV", None)
    ran = 0
    for lang, info, body, line in _blocks(page):
        where = f"{page.name}:{line}"
        if "file" in info:
            target = tmp_path / str(info["file"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        elif lang == "python" and "norun" not in info:
            script = tmp_path / f"_snippet_{line}.py"
            script.write_text(body, encoding="utf-8")
            _run([sys.executable, script.name], tmp_path, env, where)
            ran += 1
        elif lang == "bash" and "run" in info:
            bindir = Path(sys.executable).parent
            for cmd in [
                c for c in body.splitlines() if c.strip() and not c.lstrip().startswith("#")
            ]:
                argv = shlex.split(cmd.split("#", 1)[0])
                if (bindir / argv[0]).exists():
                    argv[0] = str(bindir / argv[0])
                _run(argv, tmp_path, env, f"{where} `{cmd.strip()}`")
                ran += 1
    assert ran or page.name == "README.md", f"{page.name} has no runnable snippet"
    print(f"{page.name}: {ran} snippets ran")
