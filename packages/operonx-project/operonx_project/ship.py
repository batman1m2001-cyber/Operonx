"""Ship a delivered milestone, and merge it where the owner granted that (plan D15).

The engine never pushes on its own: :func:`ship` runs on the owner's tap
(the Board's Ship, ``operonx-project ship``). It pushes the milestone's
branch and opens the PR — with ``gh`` on GitHub; on GitLab, which has no API
token here, as a pre-filled merge-request URL for the owner to open. Any
other remote is pushed, with no PR.

:func:`merge` is offered only where the repo's ``settings.toml`` says
``merge = true``. It reads the PR's checks first and refuses while one is
pending or failing: a merge is never made before its checks are read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from operonx.core.loggings import LOGGER

__all__ = ["Refused", "remote_kind", "ship", "merge"]


class Refused(Exception):
    """Shipping (or merging) does not apply now; the message says why."""


def _gh() -> str:
    return os.environ.get("OPERONX_GH") or "gh"


def _run(cmd: List[str], cwd: Path, timeout: float = 120.0) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd[:3])}: timed out after {timeout:.0f} s"
    return p.returncode, (p.stdout + p.stderr).strip()


def remote_kind(url: str) -> Dict[str, str]:
    """Where *url* lives: ``{"kind": "github" | "gitlab" | "other", "host", "path"}``
    (``path`` is ``owner/repo``, without ``.git``)."""
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)[:/](.+?)(?:\.git)?/?$", url.strip())
    if not m or url.startswith(("/", ".", "file:")):
        return {"kind": "other", "host": "", "path": ""}
    host, path = m.group(1).lower(), m.group(2)
    kind = "github" if host == "github.com" else "gitlab" if "gitlab" in host else "other"
    return {"kind": kind, "host": host, "path": path}


def _base(project: Any, mid: str) -> str:
    """The branch the milestone goes into: the one it started from (recorded at
    intake), else the remote's default branch, else main."""
    intake = project.store.ms_get(mid, "intake") or {}
    if intake.get("base"):
        return str(intake["base"])
    code, out = _run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], project.repo)
    return out.split("/", 1)[1] if code == 0 and "/" in out else "main"


def ship(project: Any, mid: str, actor: str = "owner") -> Dict[str, Any]:
    """Push milestone *mid*'s branch and open its PR. Returns what was shipped
    (``branch``, ``base``, ``remote``, ``pr``: the PR's or the MR form's URL)."""
    from operonx_project.model import MilestoneState as M

    ms = project.store.get_milestone(mid)
    if ms.state != M.DONE:
        raise Refused(f"{mid} is {ms.state.value}: only a delivered milestone ships")
    branch = project.git.integration_branch(mid)
    if not project.git.has_branch(branch):
        raise Refused(f"{mid} has no branch {branch} to ship")
    code, url = _run(["git", "remote", "get-url", "origin"], project.repo)
    if code != 0:
        raise Refused("the repo has no remote 'origin' to push to")
    base = _base(project, mid)
    code, out = _run(["git", "push", "-u", "origin", f"{branch}:{branch}"], project.repo, timeout=300)
    if code != 0:
        raise Refused(f"the push failed: {out[-600:]}")
    where = remote_kind(url)
    title = f"{mid}: {ms.title}"
    pr_md = project.state / "delivery" / mid / "PR.md"
    body = pr_md.read_text(encoding="utf-8") if pr_md.exists() else ms.goal
    pr, how = "", "pushed (no PR: the remote is neither GitHub nor GitLab)"
    if where["kind"] == "github":
        cmd = [_gh(), "pr", "create", "--repo", where["path"], "--base", base, "--head", branch, "--title", title,
               "--body", body]  # fmt: skip
        code, out = _run(cmd, project.repo)
        if code != 0 and "already exists" in out:
            code, out = _run([_gh(), "pr", "view", branch, "--repo", where["path"], "--json", "url", "-q", ".url"],
                             project.repo)  # fmt: skip
        if code != 0:
            raise Refused(f"pushed {branch}, but the PR was not opened: {out[-600:]}")
        pr = out.strip().splitlines()[-1]
        how = "pushed; the PR is open"
    elif where["kind"] == "gitlab":
        q = urllib.parse.urlencode(
            {
                "merge_request[source_branch]": branch,
                "merge_request[target_branch]": base,
                "merge_request[title]": title,
                "merge_request[description]": body[:4000],
            }
        )
        pr = f"https://{where['host']}/{where['path']}/-/merge_requests/new?{q}"
        how = "pushed; open the link to create the merge request"
    shipped = {"branch": branch, "base": base, "remote": where["kind"], "pr": pr, "at": time.time(), "by": actor}
    project.store.ms_put(mid, "shipped", shipped)
    project.store.note(mid, "shipped", shipped, actor=actor)
    LOGGER.info("shipped %s: %s %s", mid, branch, pr)
    return {**shipped, "message": f"{mid} {how}" + (f": {pr}" if pr and where["kind"] == "github" else "")}


_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def _checks(rollup: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """The PR's checks that still run, and those that failed (by name)."""
    pending, failing = [], []
    for c in rollup or []:
        name = c.get("name") or c.get("context") or "a check"
        status = str(c.get("status") or "").upper()
        result = str(c.get("conclusion") or c.get("state") or "").upper()
        if status and status != "COMPLETED" or result in ("PENDING", "EXPECTED", ""):
            pending.append(name)
        elif result not in _GREEN:
            failing.append(f"{name} ({result.lower()})")
    return pending, failing


def merge(project: Any, mid: str, actor: str = "owner", settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge milestone *mid*'s PR, where ``settings.toml`` grants it and its checks pass."""
    from operonx_project.project import load_settings

    settings = load_settings(project.state) if settings is None else settings
    if not settings.get("merge"):
        raise Refused("merging is not granted for this repo (settings.toml: merge = true); merge the PR yourself")
    shipped = project.store.ms_get(mid, "shipped") or {}
    if not shipped:
        raise Refused(f"{mid} is not shipped yet: Ship it first")
    if shipped.get("merged"):
        raise Refused(f"{mid} is already merged")
    if shipped.get("remote") != "github" or not shipped.get("pr"):
        raise Refused("only a GitHub PR is merged from here; merge the merge request on its page")
    pr = str(shipped["pr"])
    code, out = _run([_gh(), "pr", "view", pr, "--json", "state,mergeable,statusCheckRollup"], project.repo)
    if code != 0:
        raise Refused(f"the PR could not be read: {out[-400:]}")
    got = json.loads(out)
    if got.get("state") != "OPEN":
        raise Refused(f"the PR is {str(got.get('state')).lower()}, not open")
    pending, failing = _checks(got.get("statusCheckRollup") or [])
    if failing:
        raise Refused(f"the PR's checks fail: {', '.join(failing)}")
    if pending:
        raise Refused(f"the PR's checks still run: {', '.join(pending)}; merge when they pass")
    if got.get("mergeable") == "CONFLICTING":
        raise Refused(f"the PR conflicts with {shipped.get('base')}: replan or resolve it first")
    code, out = _run([_gh(), "pr", "merge", pr, "--merge"], project.repo)
    if code != 0:
        raise Refused(f"the merge failed: {out[-400:]}")
    shipped = {**shipped, "merged": time.time(), "merged_by": actor}
    project.store.ms_put(mid, "shipped", shipped)
    project.store.note(mid, "merged", {"pr": pr}, actor=actor)
    synced = _sync_base(project, str(shipped.get("base") or "main"))
    return {**shipped, "synced": synced, "message": f"{mid} merged into {shipped.get('base')}: {pr}"
            + ("" if synced else f". Pull {shipped.get('base')} here before the next milestone")}  # fmt: skip


def _sync_base(project: Any, base: str) -> bool:
    """The checkout's *base* fast-forwarded to the merge, so the next milestone starts
    from it: only when *base* is checked out and nothing tracked is changed."""
    code, head = _run(["git", "symbolic-ref", "-q", "--short", "HEAD"], project.repo)
    _, dirty = _run(["git", "status", "--porcelain", "--untracked-files=no"], project.repo)
    if code != 0 or head != base or dirty:
        return False
    if _run(["git", "fetch", "-q", "origin", base], project.repo, timeout=300)[0] != 0:
        return False
    return _run(["git", "merge", "-q", "--ff-only", f"origin/{base}"], project.repo)[0] == 0
