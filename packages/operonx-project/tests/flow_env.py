"""A small git repo for tests: ``calc.add`` (wrong: it subtracts) and a passing base test."""

from __future__ import annotations

import subprocess
from pathlib import Path

WRONG = "def add(a, b):\n    return a - b\n"


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
