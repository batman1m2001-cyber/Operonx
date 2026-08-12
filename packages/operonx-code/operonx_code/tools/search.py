"""Search tools — glob and grep.

Both shell out to fast binaries when they exist (``rg``, ``fd``) and fall
back to Python. The fallback matters more than it looks: a coding agent
whose search silently returns nothing on a machine without ripgrep will
conclude the code is not there and write it again.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
from pathlib import Path
from typing import List

from operonx.agents import tool
from operonx_code.workspace import WorkspaceError, active_workspace

__all__ = ["glob_files", "grep_files"]

# Directories no coding agent ever wants in its results, and which
# dominate them by count when included.
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
    "target",
}

_GLOB_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Glob such as '**/*.py' or 'src/**/test_*.py'.",
        },
        "path": {
            "type": "string",
            "description": "Directory to search under. Defaults to the workspace root.",
        },
        "limit": {"type": "integer", "description": "Maximum paths to return (default 200)."},
    },
    "required": ["pattern"],
}

_GREP_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Regular expression to search for."},
        "path": {"type": "string", "description": "Directory or file to search."},
        "glob": {
            "type": "string",
            "description": "Only search files matching this glob, e.g. '*.py'.",
        },
        "limit": {"type": "integer", "description": "Maximum matching lines (default 100)."},
    },
    "required": ["pattern"],
}


def _skipped(path: Path, base: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.relative_to(base).parts)


def _match(base: Path, pattern: str) -> List[Path]:
    """Files under ``base`` matching ``pattern``.

    Uses :meth:`pathlib.Path.glob`, which understands ``**``. An earlier
    version ran ``fnmatch`` over the relative path, and ``fnmatch`` has no
    concept of a path separator — so ``**/calc.py`` required a literal
    ``/`` and could never match a file at the root. A live agent asked for
    ``**/*.py`` in a directory full of Python, got ``{"count": 0}`` with
    no error, and concluded the files did not exist. Two wasted turns
    before it fell back to ``ls``.

    A bare pattern (no separator) is also tried recursively, because a
    model writing ``*.py`` almost always means "anywhere in the project"
    rather than "in the root only".
    """
    seen: dict[Path, None] = {}
    patterns = [pattern]
    if "/" not in pattern and not pattern.startswith("**"):
        patterns.append(f"**/{pattern}")
    for pat in patterns:
        try:
            found = base.glob(pat)
        except (ValueError, NotImplementedError):
            # An invalid pattern is the caller's error, not an empty result.
            raise WorkspaceError(f"invalid glob pattern: {pattern!r}") from None
        for path in found:
            if path.is_file() and not _skipped(path, base):
                seen[path] = None
    return list(seen)


def _walk(root: Path) -> List[Path]:
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".venv")]
        for name in filenames:
            out.append(Path(dirpath) / name)
    return out


@tool(
    name="glob",
    description=(
        "Find files by glob pattern, newest first. Use this to locate files "
        "by name; use grep to find them by content."
    ),
    schema=_GLOB_SCHEMA,
    readonly=True,
)
async def glob_files(pattern: str, path: str = "", limit: int = 200) -> dict:
    ws = active_workspace()
    base = ws.resolve(path) if path else ws.root
    if not base.is_dir():
        raise WorkspaceError(f"{ws.relative(base)} is not a directory")

    matches = _match(base, pattern)
    # Newest first: in a repo you are working in, recency is the best
    # available proxy for relevance.
    matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    limit = int(limit) or 200
    return {
        "files": [ws.relative(p) for p in matches[:limit]],
        "count": len(matches),
        "truncated": len(matches) > limit,
    }


async def _ripgrep(
    pattern: str, base: Path, glob: str, limit: int
) -> tuple[List[str], bool] | None:
    """Try ``rg``; return None when it is unavailable so the caller falls back."""
    rg = shutil.which("rg")
    if not rg:
        return None
    cmd = [rg, "--line-number", "--no-heading", "--color", "never", "-m", str(limit)]
    if glob:
        cmd += ["--glob", glob]
    cmd += ["-e", pattern, str(base)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    # rg exits 1 for "no matches", which is an answer, not a failure.
    if proc.returncode not in (0, 1):
        raise WorkspaceError(f"grep failed: {stderr.decode(errors='replace').strip()}")
    lines = [ln for ln in stdout.decode(errors="replace").splitlines() if ln]
    return lines[:limit], len(lines) > limit


def _python_grep(pattern: str, base: Path, glob: str, limit: int) -> tuple[List[str], bool]:
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise WorkspaceError(f"invalid regular expression: {e}") from None

    hits: List[str] = []
    files = [base] if base.is_file() else _walk(base)
    for file in files:
        if glob and not fnmatch.fnmatch(file.name, glob):
            continue
        try:
            data = file.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{file}:{n}:{line}")
                if len(hits) > limit:
                    return hits[:limit], True
    return hits, False


@tool(
    name="grep",
    description=(
        "Search file contents by regular expression. Returns 'path:line:text'. "
        "Use glob to find files by name instead."
    ),
    schema=_GREP_SCHEMA,
    readonly=True,
    max_result_chars=40_000,
)
async def grep_files(pattern: str, path: str = "", glob: str = "", limit: int = 100) -> dict:
    ws = active_workspace()
    base = ws.resolve(path) if path else ws.root
    limit = int(limit) or 100

    found = await _ripgrep(pattern, base, glob, limit)
    if found is None:
        found = _python_grep(pattern, base, glob, limit)
    lines, truncated = found

    root = str(ws.root)
    trimmed = [ln[len(root) + 1 :] if ln.startswith(root + "/") else ln for ln in lines]
    return {"matches": trimmed, "count": len(trimmed), "truncated": truncated}
