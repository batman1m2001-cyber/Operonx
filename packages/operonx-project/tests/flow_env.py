"""A small project and a card for task_flow tests: ``calc.add`` to be built.

The repo starts with ``add`` wrong (it subtracts) and a passing base test.
The scripted test author writes ``tests/acceptance/C1/test_c1_add.py``
(``add(2, 3) == 5``), the examiner a held-out test on negatives.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from operonx_project.checks import Check
from operonx_project.gitops import GitOps
from operonx_project.model import BudgetPolicy, Card, Milestone, Policy
from operonx_project.runner import FlowRunner
from operonx_project.runtime import Runtime
from operonx_project.store import Store
from tests.scripted_coder import ScriptedCoder

PY = sys.executable
WRONG = "def add(a, b):\n    return a - b\n"
RIGHT = "def add(a, b):\n    return a + b\n"
ABS = "def add(a, b):\n    return abs(a) + abs(b)\n"  # passes the visible test, fails the held-out one
ACCEPTANCE = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
HELDOUT = (
    "from calc import add\n\n\ndef test_negative():\n    assert add(-1, -1) == -2, 'add handles negative numbers'\n"
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_repo(root: Path) -> Path:
    repo = root / "proj"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(WRONG)
    (repo / "tests" / "test_base.py").write_text("def test_base():\n    assert True\n")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def author_step(card: str = "C1") -> Dict[str, Any]:
    path = f"tests/acceptance/{card}/test_{card.lower()}_add.py"
    return {"write": {path: ACCEPTANCE}, "output": {"files": [path]}}


def examiner_step(hdir_of) -> Any:
    def step(req, i):
        hdir = Path(req.add_dirs[0])
        return {"write": {str(hdir / "test_held.py"): HELDOUT}, "output": {"files": ["test_held.py"]}}

    return step


def impl(text: str, **extra: Any) -> Dict[str, Any]:
    return {"write": {"calc.py": text}, **extra}


class Env:
    def __init__(
        self,
        root: Path,
        script: Dict[str, List[Any]],
        heldout: bool = True,
        max_rounds: int = 6,
        card_max_usd: float = 5.0,
        cards: Optional[List[Card]] = None,
        log: Optional[Path] = None,
        private: Optional[Dict[str, str]] = None,
        repo: Optional[Path] = None,
    ):
        self.root = root
        self.repo = repo or make_repo(root)
        self.state = root / "state"
        script = dict(script)
        script.setdefault("test_author", [author_step()])
        script.setdefault("examiner", [examiner_step(None)])
        self.coder = ScriptedCoder(script, log=log)
        self.store = Store(self.state / "project.db")
        if not any(m.id == "m1" for m in self.store.milestones()):
            policy = Policy(heldout=heldout, budget=BudgetPolicy(max_rounds=max_rounds, card_max_usd=card_max_usd))
            self.store.add_milestone(Milestone(id="m1", project=str(self.repo), title="calc", policy=policy))
            self.store.add_cards(cards or [card()])
        self.git = GitOps(self.repo, project="proj", root=root / "worktrees", lock_dir=self.state)
        self.rt = Runtime(
            store=self.store,
            git=self.git,
            coder=self.coder,
            checks=[Check("tests", f"{PY} -m pytest -q -p no:cacheprovider tests", timeout_s=120)],
            acceptance_cmd=f"{PY} -m pytest -q -p no:cacheprovider {{dir}}",
            heldout_cmd=f"{PY} -m pytest -q --tb=no -rfE --assert=plain -p no:cacheprovider {{dir}}",
            heldout_root=root / "heldout",
            data_dir=self.state,
            sandbox=False,
            private=private or {},
        )
        self.runner = FlowRunner(self.rt, self.state / "runs.db")
        self.stages: List[str] = []

    def stage(self, s) -> None:
        self.stages.append(s.value)

    def card(self, cid: str = "C1") -> Card:
        return self.store.get_card("m1", cid)

    def wt(self, cid: str = "C1") -> Path:
        return self.git.worktree_path("m1", cid)

    def prompts(self, role: str = "implementer") -> List[str]:
        return [c["prompt"] for c in self.coder.of(role)]

    def rounds(self, cid: str = "C1") -> List[Dict[str, Any]]:
        return [e["data"] for e in self.store.events("m1") if e["kind"] == "round" and e["card"] == cid]


def card(cid: str = "C1", **kw: Any) -> Card:
    base = dict(
        id=cid,
        milestone="m1",
        title="add numbers",
        intent="calc.add adds two numbers",
        rows=["R-01"],
        criteria=["WHEN add(a, b) is called THE SYSTEM SHALL return a + b"],
        paths=["calc.py"],
    )
    base.update(kw)
    return Card(**base)
