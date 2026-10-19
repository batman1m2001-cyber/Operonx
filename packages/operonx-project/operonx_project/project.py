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

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from operonx.core.loggings import LOGGER

from operonx_project.brd.board import rebuild
from operonx_project.brd.folder import init_brd
from operonx_project.gitops import GitOps
from operonx_project.model import Milestone, Policy
from operonx_project.store import Store

__all__ = [
    "STATE_DIR",
    "Project",
    "board_seed",
    "create_milestone",
    "load_settings",
    "new_project",
    "save_settings",
    "valid_model",
]

STATE_DIR = ".operonx/project"
RUNS_DIR = ".operonx/runs"  # the flows' traces: the studio's run pages
BRD_DIR = "docs/brd"  # the BRD folder, the board inside it (``board/``)


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
        at = f"/{git.sub}" if git.sub else ""  # a project in a subfolder (D11): its own state, there
        _exclude(git.repo, f"{at}/{STATE_DIR}/")
        _exclude(git.repo, f"{at}/{RUNS_DIR}/")
        fresh = not (state / "project.db").exists()
        store = Store(state / "project.db")
        if fresh and (git.repo / BRD_DIR / "board").is_dir():
            # a clone, or a lost store: the board comes back from the BRD (plan D5)
            made = rebuild(store, git.repo / BRD_DIR, str(git.repo))
            if made:
                LOGGER.info(f"{git.repo}: the board rebuilt from {BRD_DIR}/board ({', '.join(made)})")
        return cls(repo=git.repo, state=state, store=store, git=git)

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
    seed: Optional[Dict[str, Any]] = None,
) -> Milestone:
    """Intake: the milestone, its policy and sources, stored; nothing runs yet.

    Each source is ``{"path", "role", "private", "title", "name"}``; it is
    registered and extracted in the requirements stage, into the BRD folder
    on the milestone's integration branch.

    *seed* (``start --from-board``, plan D6) is another milestone's board
    (``{"from": ..., "cards": [...]}``): its cards become this milestone's plan,
    all not started, with no planning stage; the owner still approves it."""
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
                                         "brd": brd, "branch": project.git.integration_branch(mid)})  # fmt: skip
    if seed:
        project.store.ms_put(mid, "seeded", seed)
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


#: Plan fields a card keeps when it seeds another milestone (D6): its plan, never its outcome.
_SEED_FIELDS = ("id", "title", "intent", "rows", "criteria", "deps", "paths", "kind", "risk", "priority", "epic")


def board_seed(source: Path, mid: str) -> Dict[str, Any]:
    """Milestone *mid*'s cards from a board folder (a BRD folder, or its
    ``board/``), as a plan for a new milestone: ``start --from-board``."""
    from operonx_project.brd.board import BOARD_DIR, read_board

    source = Path(source)
    brd = source.parent if source.name == BOARD_DIR else source
    for bf in read_board(brd):
        if bf.milestone.id == mid:
            cards = [{k: getattr(c, k) for k in _SEED_FIELDS} for c in bf.cards if c.state.value != "cancelled"]
            return {"from": f"{brd / BOARD_DIR / (mid + '.md')}", "title": bf.milestone.title, "cards": cards}
    raise KeyError(f"no milestone {mid!r} in {brd / BOARD_DIR}")


def load_settings(state: Path) -> Dict[str, Any]:
    """``.operonx/project/settings.toml``: this machine's settings (plan D8),
    outside git: ``sandbox = false`` where bubblewrap cannot run; ``model =
    "sonnet"``, every session's model; ``[models]``, one role's (``reviewer =
    "opus"``). The models are read at each session's start, so a change
    applies to the next session without restarting the daemon."""
    path = Path(state) / "settings.toml"
    if not path.is_file():
        return {}
    try:
        import tomllib
    except ImportError:  # pragma: no cover - 3.10
        import tomli as tomllib
    return tomllib.loads(path.read_text(encoding="utf-8"))


#: Claude Code's model aliases; a full id (``claude-…``) is accepted too.
MODEL_ALIASES = ("sonnet", "opus", "haiku", "fable")


def valid_model(name: str) -> bool:
    return name in MODEL_ALIASES or bool(re.fullmatch(r"claude-[a-z0-9.-]+", name))


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v))  # a JSON string is a TOML basic string


def save_settings(state: Path, settings: Dict[str, Any]) -> Path:
    """Write ``settings.toml``: top-level values, then one-level tables."""
    path = Path(state) / "settings.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# operonx-project: this machine's settings for this project (outside git)"]
    lines += [f"{k} = {_toml_value(v)}" for k, v in settings.items() if not isinstance(v, dict)]
    for k, table in settings.items():
        if isinstance(table, dict) and table:
            lines += ["", f"[{k}]", *(f"{rk} = {_toml_value(rv)}" for rk, rv in table.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
