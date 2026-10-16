"""What each role may do: its tools, what is pre-approved, what is denied.

Headless sessions cannot ask anyone, so the flags are the approval: a tool
call that is not allowed here is refused, and the refusal is in the
transcript. Every role runs ``--restricted``, which confines the file tools
to the working directories and ignores the host's own settings, hooks and
plugins; only the roles that write (implementer, test author, examiner) get a shell,
and that shell is sandboxed.
"""

from __future__ import annotations

from typing import Dict, List

__all__ = ["ROLES", "base_deny", "sandbox_settings", "tools_of"]

_READ = ["Read", "Grep", "Glob"]

#: role → the built-in tools it has (``--tools``) and pre-approves.
ROLES: Dict[str, List[str]] = {
    "implementer": [*_READ, "Edit", "Write", "Bash"],
    "reviewer": list(_READ),  # reads the worktree on demand; never edits
    "planner": list(_READ),
    "triage": list(_READ),
    "arbiter": list(_READ),  # a third reading of a disputed finding
    # write tests before the code exists; run them to see them fail
    "test_author": [*_READ, "Edit", "Write", "Bash"],
    "examiner": [*_READ, "Edit", "Write", "Bash"],  # held-out tests, outside the repo
}

# The engine owns git: branches, commits and merges are its, never the coder's.
_GIT_DENY = [
    "Bash(git push:*)",
    "Bash(git remote:*)",
    "Bash(git config:*)",
    "Bash(git reset --hard:*)",
    "Bash(git checkout:*)",
    "Bash(git switch:*)",
    "Bash(git rebase:*)",
    "Bash(git merge:*)",
    "Bash(git commit:*)",
    "Bash(git worktree:*)",
]


def tools_of(role: str) -> List[str]:
    try:
        return list(ROLES[role])
    except KeyError:
        raise ValueError(f"unknown coder role {role!r}; one of {sorted(ROLES)}") from None


def base_deny(state_dirs: List[str]) -> List[str]:
    """Deny rules every role carries: no ``.env`` file, no Claude sign-in, no
    engine state; and the git commands only the engine runs. Claude Code's
    rules: ``//`` starts an absolute path, ``~/`` the home directory."""
    places = ["**/.env", "**/.env.*", "~/.claude/**", *[f"/{d.rstrip('/')}/**" for d in state_dirs]]
    out = [f"{tool}({where})" for tool in ("Read", "Grep", "Glob", "Edit", "Write") for where in places]
    return out + _GIT_DENY


def sandbox_settings(allow_write: List[str], allowed_domains: List[str], deny_read: List[str]) -> Dict:
    """Claude Code's ``sandbox`` settings for the implementer's shell.

    The shell runs under bubblewrap: writes only in the working directory
    (and *allow_write*), network only to *allowed_domains* (none by default),
    and never unsandboxed: a command the sandbox cannot run fails rather than
    asking to run outside it. ``failIfUnavailable`` makes a missing bubblewrap
    an error, not a silent fallback to no boundary.
    """
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {"allowWrite": list(allow_write), "denyRead": list(deny_read)},
            "network": {"allowedDomains": list(allowed_domains)},
        }
    }
