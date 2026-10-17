"""A project: a git repo, its engine state, and how a milestone is created in it.

The engine's state lives in the repo but outside git
(``.operonx/project/``, listed in ``.git/info/exclude``): the store, the
journals, the transcripts. The flows' traces go to ``.operonx/runs/``
(excluded too), the project's local run store, where the studio reads
them. Worktrees live in ``~/.operonx/worktrees`` and held-out tests in
``~/.operonx/heldout``, outside the repo, where no card's session looks.

:func:`new_project` is ``operonx project new``: an empty repository with the
BRD folder, whose first milestone's card ``C0`` runs ``operonx init`` (the
planner chooses the template from the requirements). :func:`create_milestone`
is intake (plan §6, step 1): it records the milestone and its sources;
nothing runs until ``start``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from operonx_project.brd.folder import init_brd
from operonx_project.gitops import GitOps
from operonx_project.model import Milestone, Policy
from operonx_project.store import Store

__all__ = ["STATE_DIR", "Project", "create_milestone", "new_project"]

STATE_DIR = ".operonx/project"
RUNS_DIR = ".operonx/runs"  # the flows' traces: the studio's run pages


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
    store: Store
    git: GitOps

    @classmethod
    def open(cls, repo: Path, worktrees: Optional[Path] = None, name: Optional[str] = None) -> "Project":
        git = GitOps(Path(repo), project=name, root=worktrees, lock_dir=None)
        state = git.repo / STATE_DIR
        git.lock_dir = state
        _exclude(git.repo, f"/{STATE_DIR}/")
        _exclude(git.repo, f"/{RUNS_DIR}/")
        return cls(repo=git.repo, state=state, store=Store(state / "project.db"), git=git)

    @property
    def runs(self) -> Path:
        """Where the flows' traces go (``RUNS_DIR``): the project's local run store."""
        return self.repo / RUNS_DIR

    def next_milestone_id(self) -> str:
        n = [int(m.id[1:]) for m in self.store.milestones() if re.fullmatch(r"m\d+", m.id)]
        return f"m{max(n, default=0) + 1}"


def create_milestone(
    project: Project,
    title: str,
    goal: str = "",
    mode: str = "w2",
    sources: Sequence[Dict[str, Any]] = (),
    rows: Sequence[str] = (),
    policy: Optional[Policy] = None,
    prefix: str = "",
    constraints: str = "",
    brd: str = "docs/brd",
    mid: Optional[str] = None,
) -> Milestone:
    """Intake: the milestone, its policy and sources, stored; nothing runs yet.

    Each source is ``{"path", "role", "private", "title", "name"}``; it is
    registered and extracted in the requirements stage, into the BRD folder
    on the milestone's integration branch."""
    if mode not in ("w1", "w2"):
        raise ValueError(f"mode must be w1 or w2, not {mode!r}")
    policy = policy or (Policy.w1() if mode == "w1" else Policy.w2())
    mid = mid or project.next_milestone_id()
    ms = Milestone(id=mid, project=str(project.repo), title=title, goal=goal, rows=list(rows), policy=policy)
    project.store.add_milestone(ms)
    prefix = prefix or (re.sub(r"[^A-Z0-9]", "", project.git.project.upper())[:8] or "REQ")
    srcs = []
    for s in sources:
        p = Path(s["path"]).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"source {p} does not exist")
        srcs.append({"path": str(p), "role": s.get("role", "authoritative"), "private": bool(s.get("private")),
                     "title": s.get("title") or p.stem, "name": s.get("name") or ""})  # fmt: skip
    project.store.ms_put(mid, "intake", {"mode": mode, "sources": srcs, "prefix": prefix, "constraints": constraints,
                                         "brd": brd})  # fmt: skip
    return ms


def new_project(directory: Path, name: Optional[str] = None, worktrees: Optional[Path] = None) -> Project:
    """``operonx project new``: a git repo with the BRD folder and an empty board.

    The repo stays empty of code: the first milestone's card ``C0`` runs
    ``operonx init`` with the template its planner chose."""
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
    return Project.open(d, worktrees=worktrees, name=name)
