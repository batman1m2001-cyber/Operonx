"""A project: a git repo and its state.

The state lives in the repo but outside git (``.operonx/project/``, listed in
``.git/info/exclude``): the extraction's journals and transcripts. The board
is in git (``docs/brd/board.md``). The flows' traces go to ``.operonx/runs/`` (excluded too), the
project's local run store, where the studio reads them.

:func:`new_project` is ``operonx project new``: an empty repository with the
BRD folder.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from operonx_project.brd.folder import init_brd
from operonx_project.gitops import GitOps

__all__ = ["STATE_DIR", "Project", "load_settings", "new_project"]

STATE_DIR = ".operonx/project"
RUNS_DIR = ".operonx/runs"  # the flows' traces: the studio's run pages
BRD_DIR = "docs/brd"  # the BRD folder, the board inside it (``board.md``)


def _exclude(repo: Path, line: str) -> None:
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=repo, capture_output=True, text=True,
                            check=True).stdout.strip()  # fmt: skip
    ex = (repo / common if not Path(common).is_absolute() else Path(common)) / "info" / "exclude"
    ex.parent.mkdir(parents=True, exist_ok=True)
    lines = ex.read_text().splitlines() if ex.exists() else []
    if line not in lines:
        ex.write_text("\n".join([*lines, line]) + "\n")


@dataclass
class Project:
    repo: Path
    state: Path
    git: GitOps

    @classmethod
    def open(cls, repo: Path, name: Optional[str] = None) -> "Project":
        git = GitOps(Path(repo), project=name)
        state = git.repo / STATE_DIR
        at = f"/{git.sub}" if git.sub else ""  # a project in a subfolder (D11): its own state, there
        _exclude(git.repo, f"{at}/{STATE_DIR}/")
        _exclude(git.repo, f"{at}/{RUNS_DIR}/")
        state.mkdir(parents=True, exist_ok=True)
        return cls(repo=git.repo, state=state, git=git)

    @property
    def runs(self) -> Path:
        """Where the flows' traces go (``RUNS_DIR``): the project's local run store."""
        return self.repo / RUNS_DIR


def new_project(directory: Path, name: Optional[str] = None) -> Project:
    """``operonx project new``: a git repo with the BRD folder."""
    d = Path(directory).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    if not (d / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True)
    init_brd(d, "docs/brd", project=name or d.name)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=d, capture_output=True, text=True).stdout
    if status.strip():
        env_id = ["-c", "user.name=operonx-project", "-c", "user.email=operonx-project@localhost"]
        own = subprocess.run(["git", "config", "user.email"], cwd=d, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "add", "docs/brd"], cwd=d, check=True)
        subprocess.run(["git", *([] if own else env_id), "commit", "-q", "-m", "docs: the living BRD folder"], cwd=d,
                       check=True)  # fmt: skip
    return Project.open(d, name=name)


def load_settings(state: Path) -> Dict[str, Any]:
    """``.operonx/project/settings.toml``: this machine's settings, outside git: ``model = "sonnet"``, every
    session's model; ``[models]``, one role's (``extractor = "opus"``). Read at each session's start."""
    path = Path(state) / "settings.toml"
    if not path.is_file():
        return {}
    try:
        import tomllib
    except ImportError:  # pragma: no cover - 3.10
        import tomli as tomllib
    return tomllib.loads(path.read_text(encoding="utf-8"))
