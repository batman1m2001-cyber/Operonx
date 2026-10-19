"""Git, owned by the engine: worktrees, branches, commits, merges, guards.

The coder edits files; everything that moves a ref happens here. Each card
gets its own worktree on its own branch, cut from the milestone's
integration branch, and lives *outside* the repo
(``~/.operonx/worktrees/<project>/<milestone>/<card>``) so the project's own
tools never scan it. A verified card is merged into the integration branch
one at a time under a lock; a conflict aborts the merge cleanly and is
reported with its files, for a rebase round.

Branch names (git refs are paths, so ``milestone/m`` and ``milestone/m/c``
cannot both exist):

- integration: ``milestone/<m>/main``
- a card:      ``milestone/<m>/card/<card>``

A project in a subfolder of a repo (plan D11: the folder with
``operonx.toml``, its own BRD and store) has its own names,
``milestone/<ns>/<m>/main`` with ``<ns>`` the subfolder's path, so two
projects in one repo never share a branch. Paths the engine sees are the
project's (``src/x.py``), and one outside the project is ``../…``, which
the guard refuses whatever the card's paths.

Every function is idempotent where a crash could repeat it: creating a
worktree that exists returns it, a commit with nothing to commit returns
``None``, a merge finds and aborts a half-written one first.
"""

from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import os
import re
import subprocess
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

from operonx.core.loggings import LOGGER

__all__ = [
    "GitError",
    "GitOps",
    "GuardFinding",
    "Merge",
    "card_branch",
    "conflict_markers",
    "guard",
    "integration_branch",
    "sha256_file",
]

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


def _prefix(ns: str) -> str:
    return f"milestone/{ns}/" if ns else "milestone/"


def integration_branch(milestone: str, ns: str = "") -> str:
    return f"{_prefix(ns)}{_check_name('milestone', milestone)}/main"


def card_branch(milestone: str, card: str, ns: str = "") -> str:
    return f"{_prefix(ns)}{_check_name('milestone', milestone)}/card/{_check_name('card', card)}"


@dataclass
class Merge:
    """A card's merge into the integration branch.

    ``status``: ``merged`` (``sha`` is the new tip), ``conflict`` (``files``
    in conflict; the integration branch is unchanged), ``empty`` (the card's
    branch has nothing the integration branch lacks)."""

    status: str
    sha: Optional[str] = None
    files: List[str] = field(default_factory=list)
    detail: str = ""


def _run(cwd: Path, *args: str, check: bool = True, env: Optional[Dict[str, str]] = None) -> str:
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **(env or {})}
    p = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=full_env, stdin=subprocess.DEVNULL
    )
    if check and p.returncode != 0:
        raise GitError(args, p.returncode, p.stderr + p.stdout)
    return p.stdout


class GitOps:
    """One project's git: its repo, and its milestones' worktrees.

    ``lock_dir`` holds the merge lock (the project's ``.operonx/project``);
    ``root`` the worktrees (default ``~/.operonx/worktrees``).
    """

    def __init__(
        self,
        repo: Path,
        project: Optional[str] = None,
        root: Optional[Path] = None,
        lock_dir: Optional[Path] = None,
    ):
        given = Path(repo).resolve()
        self.top = Path(_run(given, "rev-parse", "--show-toplevel").strip()).resolve()
        rel = os.path.relpath(given, self.top)
        #: the project's folder inside the repo ("" at its top level), and its branch namespace
        self.sub = "" if rel == "." else Path(rel).as_posix()
        self.ns = re.sub(r"[^A-Za-z0-9._-]+", "-", self.sub).strip("-")
        self.repo = given if self.sub else self.top
        self.project = _check_name("project", project or self.repo.name)
        self.root = Path(root or Path.home() / ".operonx" / "worktrees").expanduser() / self.project
        self.lock_dir = Path(lock_dir or self.repo / ".operonx" / "project")
        self._thread_lock = threading.Lock()

    # ── plumbing ──

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

    def has_branch(self, branch: str) -> bool:
        return bool(self.git("branch", "--list", branch).strip())

    def integration_branch(self, milestone: str) -> str:
        return integration_branch(milestone, self.ns)

    def card_branch(self, milestone: str, card: str) -> str:
        return card_branch(milestone, card, self.ns)

    def _proj(self, path: str) -> str:
        """A path from the repo's top level, as the project sees it."""
        return os.path.relpath(path, self.sub).replace(os.sep, "/") if self.sub else path

    def _in(self, root: Path) -> Path:
        return root / self.sub if self.sub else root

    # ── worktrees ──

    def _card_root(self, milestone: str, card: str) -> Path:
        return self.root / _check_name("milestone", milestone) / _check_name("card", card)

    def _integ_root(self, milestone: str) -> Path:
        return self.root / _check_name("milestone", milestone) / "_integration"

    def worktree_path(self, milestone: str, card: str) -> Path:
        """The card's project folder inside its worktree."""
        return self._in(self._card_root(milestone, card))

    def integration_path(self, milestone: str) -> Path:
        return self._in(self._integ_root(milestone))

    def _worktrees(self) -> Dict[str, str]:
        """worktree path → branch (``refs/heads/…`` stripped)."""
        out: Dict[str, str] = {}
        path = None
        for line in self.git("worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree ") :]
            elif line.startswith("branch ") and path:
                out[str(Path(path).resolve())] = line[len("branch refs/heads/") :]
        return out

    def _ensure_worktree(self, path: Path, branch: str, start: str) -> Path:
        path = path.resolve() if path.exists() else path
        listed = self._worktrees()
        if str(path) in listed and not path.is_dir():
            # its directory was deleted: git still lists it until a prune
            self.git("worktree", "prune")
            listed = self._worktrees()
        if str(path) in listed:
            if listed[str(path)] != branch:
                raise GitError(["worktree"], 1, f"{path} is on {listed[str(path)]}, not {branch}")
            return path
        self.git("worktree", "prune")
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.has_branch(branch):
            self.git("worktree", "add", str(path), branch)
        else:
            self.git("worktree", "add", "-b", branch, str(path), start)
        LOGGER.info(f"worktree {path} on {branch}")
        return path

    def ensure_integration(self, milestone: str, base: str = "HEAD") -> Path:
        """The milestone's integration branch (cut from *base* the first time)
        and its worktree, where merges happen."""
        root = self._ensure_worktree(self._integ_root(milestone), self.integration_branch(milestone), self.rev(base))
        return self._in(root)

    def ensure_card(self, milestone: str, card: str, start: Optional[str] = None) -> Path:
        """The card's worktree on its own branch, cut from the integration tip
        (or *start*) the first time; the same worktree after a crash."""
        start = start or self.integration_branch(milestone)
        root = self._ensure_worktree(self._card_root(milestone, card), self.card_branch(milestone, card),
                                     self.rev(start))  # fmt: skip
        return self._in(root)

    def remove_card(self, milestone: str, card: str, delete_branch: bool = False) -> None:
        path = self._card_root(milestone, card)
        if str(path.resolve() if path.exists() else path) in self._worktrees():
            self.git("worktree", "remove", "--force", str(path))
        self.git("worktree", "prune")
        if delete_branch and self.has_branch(self.card_branch(milestone, card)):
            self.git("branch", "-D", self.card_branch(milestone, card))

    # ── commits and diffs ──

    def commit(self, worktree: Path, message: str) -> Optional[str]:
        """Commit everything changed in *worktree* (ignored files stay out).
        ``None`` when there was nothing to commit."""
        self.git("add", "-A", cwd=worktree)
        if not self.git("status", "--porcelain", cwd=worktree).strip():
            return None
        _run(worktree, "commit", "-q", "--no-verify", "-m", message, env=self._commit_env(worktree))
        return self.rev("HEAD", cwd=worktree)

    def dirty(self, worktree: Path) -> List[str]:
        """Paths with uncommitted changes, untracked included."""
        out = self.git("status", "--porcelain", "-uall", cwd=worktree)
        return [self._proj(line[3:].split(" -> ")[-1]) for line in out.splitlines() if line.strip()]

    def changed(self, worktree: Path, base: str) -> List[str]:
        """Paths the worktree's HEAD changed since its merge base with *base*."""
        mb = self.git("merge-base", base, "HEAD", cwd=worktree).strip()
        return [self._proj(p) for p in self.git("diff", "--name-only", mb, "HEAD", cwd=worktree).splitlines() if p]

    def diff(self, worktree: Path, base: str, limit: int = 400_000) -> str:
        """The card's diff since its merge base with *base*, for a reviewer."""
        mb = self.git("merge-base", base, "HEAD", cwd=worktree).strip()
        text = self.git("diff", "--no-color", "--stat", "--patch", mb, "HEAD", cwd=worktree)
        if len(text) > limit:
            text = text[:limit] + f"\n… diff cut at {limit} characters; read the files for the rest\n"
        return text

    # ── merging ──

    @contextmanager
    def merge_lock(self) -> Iterator[None]:
        """One merge at a time per project, across threads and processes."""
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, open(self.lock_dir / "merge.lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _abort_half_merge(self, worktree: Path) -> bool:
        git_dir = Path(self.git("rev-parse", "--git-dir", cwd=worktree).strip())
        if not git_dir.is_absolute():
            git_dir = worktree / git_dir
        if (git_dir / "MERGE_HEAD").exists():
            LOGGER.warning(f"aborting a half-written merge in {worktree}")
            self.git("merge", "--abort", cwd=worktree)
            return True
        return False

    def merge_card(self, milestone: str, card: str, message: Optional[str] = None, lock: bool = True) -> Merge:
        """Merge the card's branch into the integration branch (``--no-ff``).
        A conflict is aborted, so the integration branch is never left
        half-merged; the result names the files. ``lock=False`` when the
        caller holds :meth:`merge_lock` already (merge, check, maybe undo)."""
        integ = self.ensure_integration(milestone)
        branch = self.card_branch(milestone, card)
        with self.merge_lock() if lock else nullcontext():
            self._abort_half_merge(integ)
            if self.dirty(integ):
                raise GitError(["merge"], 1, f"the integration worktree {integ} has uncommitted changes")
            ahead = self.git("rev-list", "--count", f"{self.integration_branch(milestone)}..{branch}").strip()
            if ahead == "0":
                return Merge(status="empty", sha=self.rev("HEAD", cwd=integ))
            p = subprocess.run(
                ["git", "merge", "--no-ff", "--no-edit", "-m", message or f"merge card {card}", branch],
                cwd=str(integ),
                capture_output=True,
                text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **self._commit_env(integ)},
                stdin=subprocess.DEVNULL,
            )
            if p.returncode == 0:
                return Merge(status="merged", sha=self.rev("HEAD", cwd=integ))
            files = [self._proj(f) for f in self.git("diff", "--name-only", "--diff-filter=U", cwd=integ).splitlines()
                     if f]  # fmt: skip
            self.git("merge", "--abort", cwd=integ, check=False)
            if not files:
                raise GitError(["merge", branch], p.returncode, p.stdout + p.stderr)
            return Merge(status="conflict", files=files, detail=(p.stdout + p.stderr).strip()[-2000:])

    def update_card(self, milestone: str, card: str) -> Merge:
        """Bring the integration tip into the card's branch (the start of a
        rebase round). On a conflict the markers are left in the worktree for
        the coder to resolve; ``files`` names them."""
        wt = self.ensure_card(milestone, card)
        self._abort_half_merge(wt)
        p = subprocess.run(
            ["git", "merge", "--no-edit", "-m", f"update {card} from the integration branch",
             self.integration_branch(milestone)],
            cwd=str(wt),
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **self._commit_env(wt)},
            stdin=subprocess.DEVNULL,
        )  # fmt: skip
        if p.returncode == 0:
            return Merge(status="merged", sha=self.rev("HEAD", cwd=wt))
        files = [self._proj(f) for f in self.git("diff", "--name-only", "--diff-filter=U", cwd=wt).splitlines() if f]
        if not files:
            raise GitError(["merge"], p.returncode, p.stdout + p.stderr)
        return Merge(status="conflict", files=files, detail=(p.stdout + p.stderr).strip()[-2000:])

    def conflicted(self, worktree: Path) -> List[str]:
        return [self._proj(f) for f in self.git("diff", "--name-only", "--diff-filter=U", cwd=worktree).splitlines()
                if f]  # fmt: skip

    def reset_integration(self, milestone: str, sha: str) -> None:
        """Move the integration branch back to *sha*: a merge whose checks
        regressed is undone (call it under :meth:`merge_lock`)."""
        integ = self.ensure_integration(milestone)
        self._abort_half_merge(integ)
        self.git("reset", "-q", "--hard", sha, cwd=integ)
        LOGGER.warning(f"integration branch of {milestone} reset to {sha[:10]}")

    def revert(self, worktree: Path, paths: Iterable[str]) -> None:
        """Undo uncommitted changes to *paths*: a tracked file goes back to
        HEAD, a new one is deleted. The guard refuses a change this way, so
        it never reaches a commit."""
        for path in paths:
            # asked per path: from a project's subfolder, ls-tree lists only that folder
            if self.git("ls-tree", "--name-only", "HEAD", "--", path, cwd=worktree).strip():
                self.git("checkout", "-q", "HEAD", "--", path, cwd=worktree)
            else:
                f = worktree / path
                self.git("rm", "-q", "--cached", "--ignore-unmatch", "--", path, cwd=worktree)
                if f.is_file() or f.is_symlink():
                    f.unlink()

    def head(self, worktree: Path) -> str:
        return self.rev("HEAD", cwd=worktree)


_MARKER = re.compile(rb"^(<{7} |>{7} |={7}$)", re.M)


def conflict_markers(worktree: Path, paths: Iterable[str]) -> List[str]:
    """The files among *paths* that still hold merge conflict markers."""
    out = []
    for path in paths:
        f = worktree / path
        try:
            if f.is_file() and _MARKER.search(f.read_bytes()):
                out.append(path)
        except OSError:
            continue
    return out


# ── guards ──


@dataclass
class GuardFinding:
    """A reason the round's diff is refused (``severity="blocker"``) or
    flagged (``"warning"``: a declared shared file outside the card's paths)."""

    path: str
    rule: str  # outside_paths | protected | private_source
    severity: str
    message: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _matches(path: str, globs: Iterable[str]) -> bool:
    for g in globs:
        g = g.strip("/")
        if fnmatch.fnmatchcase(path, g) or path == g or path.startswith(g.rstrip("*").rstrip("/") + "/"):
            return True
        if g.endswith("/**") and (path == g[:-3] or path.startswith(g[:-3] + "/")):
            return True
    return False


def guard(
    worktree: Path,
    changed: Sequence[str],
    paths: Sequence[str] = (),
    shared: Sequence[str] = (),
    protected: Sequence[str] = (),
    private: Optional[Dict[str, str]] = None,
) -> List[GuardFinding]:
    """Check a round's changed files (plan §7 step 3, §4.1).

    - a *protected* file (the acceptance tests) changed: blocker;
    - a file outside the card's *paths*: blocker, or a warning when it is
      declared *shared* (``pyproject.toml``); no paths declared = anywhere;
    - a private source (``private``: sha256 → its name in the register) added
      anywhere, under any name: blocker. Private sources are matched by
      content, so a renamed copy is caught too.
    """
    findings: List[GuardFinding] = []
    hashes = private or {}
    for path in changed:
        if path == ".." or path.startswith("../"):
            findings.append(GuardFinding(path, "outside_project", "blocker", f"{path} is outside the project's folder"))
            continue
        if protected and _matches(path, protected):
            findings.append(GuardFinding(path, "protected", "blocker", f"{path} is a protected acceptance file"))
        elif paths and not _matches(path, paths):
            if shared and _matches(path, shared):
                findings.append(GuardFinding(path, "outside_paths", "warning", f"{path} is shared; changed"))
            else:
                findings.append(
                    GuardFinding(path, "outside_paths", "blocker", f"{path} is outside the card's paths {list(paths)}")
                )
        if hashes:
            f = worktree / path
            digest = sha256_file(f) if f.is_file() else None
            if digest in hashes:
                msg = f"{path} is the private source {hashes[digest]!r}; never commit it"
                findings.append(GuardFinding(path, "private_source", "blocker", msg))
    return findings
