"""Git, as the engine uses it: the repo, its project namespace, a commit's identity.

A project in a subfolder of a repo (the folder with ``operonx.toml``, its own
BRD and store) is ``sub``, its path from the repo's top level.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional, Sequence

__all__ = ["GitError", "GitOps"]

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_IDENTITY = ("operonx-project", "operonx-project@localhost")


class GitError(RuntimeError):
    def __init__(self, args: Sequence[str], code: int, out: str):
        super().__init__(f"git {' '.join(args)} exited {code}: {out.strip()[-800:]}")
        self.code = code
        self.out = out


def _check_name(kind: str, name: str) -> str:
    if not _SAFE.match(name):
        raise ValueError(f"{kind} id {name!r} must be letters, digits, '.', '_' or '-' (≤ 64)")
    return name


def _run(cwd: Path, *args: str, check: bool = True, env: Optional[Dict[str, str]] = None) -> str:
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **(env or {})}
    p = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=full_env, stdin=subprocess.DEVNULL
    )
    if check and p.returncode != 0:
        raise GitError(args, p.returncode, p.stderr + p.stdout)
    return p.stdout


class GitOps:
    """One project's git: its repo, and the project's folder in it."""

    def __init__(self, repo: Path, project: Optional[str] = None):
        given = Path(repo).resolve()
        self.top = Path(_run(given, "rev-parse", "--show-toplevel").strip()).resolve()
        rel = os.path.relpath(given, self.top)
        #: the project's folder inside the repo ("" at its top level)
        self.sub = "" if rel == "." else Path(rel).as_posix()
        self.repo = given if self.sub else self.top
        self.project = _check_name("project", project or self.repo.name)

    def git(self, *args: str, cwd: Optional[Path] = None, check: bool = True) -> str:
        return _run(cwd or self.repo, *args, check=check)

    def _commit_env(self, cwd: Path) -> Dict[str, str]:
        """The repo's own identity when it has one, else the engine's."""
        name = _run(cwd, "config", "user.name", check=False).strip()
        email = _run(cwd, "config", "user.email", check=False).strip()
        if name and email:
            return {}
        n, e = _IDENTITY
        return {"GIT_AUTHOR_NAME": n, "GIT_AUTHOR_EMAIL": e, "GIT_COMMITTER_NAME": n, "GIT_COMMITTER_EMAIL": e}

    def rev(self, ref: str, cwd: Optional[Path] = None) -> str:
        return self.git("rev-parse", "--verify", f"{ref}^{{commit}}", cwd=cwd).strip()
