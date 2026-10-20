"""What a card's run works with, outside the graph's inputs.

``task_flow`` is journalled, so its inputs are plain JSON: a runtime key, the
milestone and the card. The ops look the rest up here: the store, the
project's git, the coder, the check commands. A process that resumes a run
registers a runtime under the same key first (the key is the project's
name by default), and the run goes on with that process's objects.
"""

from __future__ import annotations

import os
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from operonx.core.loggings import LOGGER

from operonx_project.checks import Check
from operonx_project.coder.base import Coder
from operonx_project.gitops import GitOps
from operonx_project.model import Card, Stage
from operonx_project.store import Store

__all__ = ["DEFAULT_MODELS", "DEFAULT_SHARED", "Runtime", "get_runtime", "register", "role_model", "unregister"]

#: Files any card may change, with a warning (plan §7, step 3).
DEFAULT_SHARED = [
    "pyproject.toml",
    "uv.lock",
    "requirements*.txt",
    "package.json",
    "package-lock.json",
    ".env.example",
    "CHANGELOG.md",
]

#: Different sessions and different models, by construction (plan §2, decision 5).
DEFAULT_MODELS: Dict[str, Optional[str]] = {
    "implementer": "opus",
    "reviewer": "fable",
    "arbiter": "sonnet",
    "triage": "sonnet",
    "test_author": "sonnet",
    "examiner": "fable",
    # the milestone's stages
    "surveyor": "sonnet",
    "planner": "opus",
    "plan_reviewer": "fable",
    "replenisher": "opus",
    "milestone_reviewer": "fable",
    "retrospective": "sonnet",  # the lessons a delivered milestone teaches (retro.py)
    # the requirements, from a source (brd_flow)
    "extractor": "opus",
    "hunter": "fable",
}


def role_model(state: Path, role: str, fallback: Optional[str]) -> Optional[str]:
    """The model *role*'s session runs on: ``settings.toml``'s ``[models]`` for
    that role, else its ``model`` (every role), else *fallback*. Read at each
    session's start, so the owner's change applies to the next session without
    restarting the daemon; a file that does not parse is ignored, with a warning."""
    from operonx_project.project import load_settings

    try:
        got = load_settings(state)
    except ValueError:
        LOGGER.warning(f"operonx-project: {Path(state) / 'settings.toml'} does not parse; its models are ignored")
        return fallback
    return (got.get("models") or {}).get(role) or got.get("model") or fallback


@dataclass
class Runtime:
    store: Store
    git: GitOps
    coder: Coder
    checks: List[Check] = field(default_factory=list)  # the repo's own commands: tests, lint, types
    shared: List[str] = field(default_factory=lambda: list(DEFAULT_SHARED))
    private: Dict[str, str] = field(default_factory=dict)  # sha256 → name: sources never to commit
    brief: str = ""  # the repo brief, written once, handed to every session
    models: Dict[str, Optional[str]] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    acceptance_root: str = "tests/acceptance"
    acceptance_cmd: str = "python -m pytest -q -p no:cacheprovider {dir}"
    heldout_cmd: str = "python -m pytest -q --tb=no -rfE --assert=plain -p no:cacheprovider {dir}"
    heldout_root: Path = field(default_factory=lambda: Path.home() / ".operonx" / "heldout")
    data_dir: Optional[Path] = None  # transcripts; default <repo>/.operonx/project
    check_mode: str = "host"  # how the acceptance and held-out tests run: host | docker
    check_image: Optional[str] = None
    check_timeout_s: float = 600.0
    call_max_usd: float = 2.0  # --max-budget-usd per coder call (capped by what the card has left)
    call_timeout_s: float = 1800.0
    sandbox: bool = True
    env: Optional[Dict[str, str]] = None  # for the checks; default the engine's minus credentials, no bytecode
    key: str = ""
    python: str = sys.executable  # runs the operonx checks when the target has no .venv of its own
    stages: Dict[Tuple[str, str], Callable[[Stage], None]] = field(default_factory=dict)
    _activity_at: Dict[Tuple[str, Optional[str]], float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.key = self.key or self.git.project
        self.heldout_root = Path(self.heldout_root).expanduser()
        self.data_dir = Path(self.data_dir or self.git.lock_dir)
        if self.env is None:
            from operonx_project.safety import scrub_env

            self.env = {**scrub_env(os.environ), "PYTHONDONTWRITEBYTECODE": "1"}

    # ── helpers the ops share ──

    def card(self, mid: str, cid: str) -> Card:
        return self.store.get_card(mid, cid)

    def model_for(self, role: str) -> Optional[str]:
        """The model a *role*'s session runs on (:func:`role_model`)."""
        return role_model(Path(self.data_dir), role, self.models.get(role))

    def activity_for(self, mid: str, cid: Optional[str], role: str, every_s: float = 2.0):
        """A callback that records a session's tool calls as ``activity`` events,
        at most one per card every *every_s* seconds (plan D10): the Board's
        live line, and the assistant's view of a running card."""
        key = (mid, cid)

        def note(text: str) -> None:
            now = time.time()
            if now - self._activity_at.get(key, 0.0) < every_s:
                return
            self._activity_at[key] = now
            self.store.note(mid, "activity", {"text": text[:120], "role": role}, card=cid)

        return note

    def set_detail(self, mid: str, cid: str, **values) -> Card:
        """Merge *values* into the card's ``detail`` (``None`` removes a key)."""
        detail = dict(self.store.get_card(mid, cid).detail)
        for k, v in values.items():
            if v is None:
                detail.pop(k, None)
            else:
                detail[k] = v
        return self.store.update_card(mid, cid, detail=detail)

    def stage(self, mid: str, cid: str, stage: Stage) -> None:
        cb = self.stages.get((mid, cid))
        if cb is None:
            return
        try:
            cb(stage)
        except Exception as exc:  # noqa: BLE001 — the board column is display; the run goes on
            LOGGER.warning(f"card {cid}: could not show stage {stage.value}: {exc}")

    @property
    def venv(self) -> Path:
        """A greenfield project's own environment (made after ``operonx init``), outside git."""
        return Path(self.data_dir) / "venv"

    def target_python(self) -> Path:
        """The project's environment: the one the engine made, else the repo's ``.venv``, else the engine's."""
        for own in (self.venv / "bin" / "python", Path(self.git.repo) / ".venv" / "bin" / "python"):
            if own.exists():
                return own
        return Path(self.python)

    def operonx_argv(self) -> List[str]:
        """How to run ``operonx`` for the target (``python -m operonx`` does not exist)."""
        py = self.target_python()
        exe = py.parent / "operonx"
        if exe.exists():
            return [str(exe)]
        return [str(py), "-c", "from operonx.cli.main import main; raise SystemExit(main())"]

    def heldout_dir(self, mid: str, cid: str) -> Path:
        return self.heldout_root / self.git.project / mid / cid

    def transcript(self, mid: str, cid: str, name: str) -> Path:
        d = Path(self.data_dir) / "transcripts" / mid / cid
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{name}.jsonl"

    def heldout_check(self, name: str, hdir: Path, wt: Path) -> Tuple[Check, str]:
        """The held-out run of *hdir* in the worktree *wt*, and the directory as the run sees it.

        The tests live outside the repo, so pytest would take its rootdir from
        them and never read the project's config: a ``src/`` layout's
        ``pythonpath = ["src"]`` is lost and every import fails. The project's
        pytest config is named, and the worktree is the rootdir."""
        docker = self.check_mode == "docker"
        inside = "/heldout" if docker else str(hdir)
        cmd = self.heldout_cmd.format(dir=inside)
        ini = pytest_config(wt)
        if ini and "pytest" in cmd and " -c " not in cmd:
            cmd = cmd.replace(" -m pytest", f" -m pytest -c {shlex.quote(ini)} --rootdir .", 1)
        return self.test_check(name, cmd, mounts={str(hdir): "/heldout"} if docker else None), inside

    def test_check(self, name: str, cmd: str, mounts: Optional[Dict[str, str]] = None) -> Check:
        if self.check_mode == "host" and cmd.startswith("python "):
            # on the host, the project's interpreter: a bare `python` is whatever PATH has, or nothing
            cmd = f"{shlex.quote(str(self.target_python()))} {cmd[len('python ') :]}"
        return Check(
            name=name,
            cmd=cmd,
            timeout_s=self.check_timeout_s,
            mode=self.check_mode,
            image=self.check_image,
            mounts=mounts or {},
            env={"COLUMNS": "1000"},  # pytest cuts its summary lines to the terminal's width
        )


def pytest_config(wt: Path) -> Optional[str]:
    """The worktree's pytest config file, in pytest's own order of preference, or None."""
    marks = {"pytest.ini": "", ".pytest.ini": "", "pyproject.toml": "[tool.pytest", "tox.ini": "[pytest]",
             "setup.cfg": "[tool:pytest]"}  # fmt: skip
    for name, mark in marks.items():
        f = Path(wt) / name
        if f.is_file() and (not mark or mark in f.read_text(errors="replace")):
            return name
    return None


_REGISTRY: Dict[str, Runtime] = {}


def register(rt: Runtime) -> Runtime:
    _REGISTRY[rt.key] = rt
    return rt


def unregister(key: str) -> None:
    _REGISTRY.pop(key, None)


def get_runtime(key: str) -> Runtime:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"no runtime {key!r} in this process: register one (operonx_project.runtime.register) "
            "before running or resuming a card's run"
        ) from None
