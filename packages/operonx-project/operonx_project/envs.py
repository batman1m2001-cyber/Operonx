"""A greenfield project's own environment, made from its toolchain after ``operonx init``.

The environment lives in the engine's state (``<repo>/.operonx/project/venv``,
outside git), and holds what the project declares: its dependencies and
its test extra (or ``dev`` group). The project itself is not installed:
its tests import it from the worktree (``pythonpath`` in its pytest
settings), so every card's worktree tests its own code.

``uv`` makes it with ``uv venv`` + ``uv pip install``; ``pip`` with
``python -m venv`` + ``pip install``. When the engine's own operonx is a
local checkout (an editable install), the project gets that same operonx,
editable: the version the engine was tested with, also before it is on PyPI.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from importlib import metadata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

__all__ = ["env_python", "local_operonx", "make_env", "requirements"]


def env_python(venv: Path) -> Path:
    return Path(venv) / "bin" / "python"


def requirements(pyproject: Path) -> List[str]:
    """The dependencies, the ``test`` extra and the ``dev`` group, without the project itself."""
    data = tomllib.loads(Path(pyproject).read_text(encoding="utf-8"))
    proj = data.get("project") or {}
    name = re.sub(r"[-_.]+", "-", str(proj.get("name") or "")).lower()
    reqs = list(proj.get("dependencies") or [])
    reqs += list((proj.get("optional-dependencies") or {}).get("test") or [])
    reqs += [r for r in (data.get("dependency-groups") or {}).get("dev") or [] if isinstance(r, str)]

    def own(r: str) -> bool:
        head = re.split(r"[\s\[<>=!~;]", r.strip(), maxsplit=1)[0]
        return re.sub(r"[-_.]+", "-", head).lower() == name

    out: List[str] = []
    for r in reqs:
        if not own(r) and r not in out:
            out.append(r)
    return out


def local_operonx() -> Optional[Path]:
    """The engine's operonx source directory, when it is installed from one."""
    try:
        raw = metadata.distribution("operonx").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    url = json.loads(raw).get("url", "")
    if not url.startswith("file://"):
        return None
    path = Path(url[len("file://") :])
    return path if (path / "pyproject.toml").exists() else None


async def _run(argv: List[str], env: Dict[str, str], timeout_s: float) -> Tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, env=env)  # fmt: skip
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, f"timed out after {timeout_s:.0f} s"
    return proc.returncode or 0, out.decode(errors="replace")


async def make_env(venv: Path, project: Path, toolchain: str, base_python: str, env: Dict[str, str],
                   timeout_s: float = 900.0) -> Tuple[bool, str]:  # fmt: skip
    """Make (or bring up to date) *venv* for *project*. Returns ``(ok, log)``."""
    venv = Path(venv)
    reqs = requirements(Path(project) / "pyproject.toml")
    local = local_operonx()
    extra: List[str] = ["-e", str(local)] if local else []
    env = {k: v for k, v in env.items() if k != "VIRTUAL_ENV"}
    uv = shutil.which("uv") if toolchain == "uv" else None
    log: List[str] = []
    if uv:
        steps = [[uv, "venv", "--allow-existing", "--python", base_python, str(venv)]]
        steps.append([uv, "pip", "install", "--python", str(env_python(venv)), *extra, *reqs])
    else:
        if toolchain == "uv":
            log.append("uv is not on PATH: the environment is made with pip")
        steps = [[base_python, "-m", "venv", str(venv)]] if not env_python(venv).exists() else []
        pip = [str(env_python(venv)), "-m", "pip", "install", "-q", "--disable-pip-version-check"]
        if extra:  # pip resolves a requirement against the index even beside an editable: install it first
            steps.append([*pip, *extra])
        steps.append([*pip, *reqs])
    for argv in steps:
        code, out = await _run(argv, env, timeout_s)
        log.append(f"$ {' '.join(argv[:4])} … ({code})\n{out[-2000:]}")
        if code != 0:
            return False, "\n".join(log)
    return True, "\n".join(log)
