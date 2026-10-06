"""Where a run came from — the tags every run carries.

A run is one graph execution, and every run has exactly one **origin**:
a service answering a client, a job working through a source, an eval,
the studio's playground, or anything else (a test, a script — "ad hoc").
The origin travels as trace metadata, which every consumer already
reads: the local consumer files the run under it, Langfuse receives it
as tags, a run store indexes it. Nothing here does I/O except
:func:`code_version`, which asks git once per process.

::

    origin_metadata("service", service="call", transport="websocket")
    # {"origin": "service", "service": "call", "transport": "websocket",
    #  "tags": ["origin:service", "service:call", "transport:websocket"]}

A job of ``steps`` runs its steps inside :func:`in_runbook`; a step's runs
then carry ``runbook`` and ``runbook_run`` (the names run stores keep,
from when that job was a ``Runbook``) beside their own ``job`` fields, so
its run → its steps' runs → their items → their traces is one path.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple, Union

__all__ = [
    "ORIGINS",
    "ORIGIN_ADHOC",
    "ORIGIN_EVAL",
    "ORIGIN_JOB",
    "ORIGIN_PLAYGROUND",
    "ORIGIN_SERVICE",
    "code_version",
    "current_runbook",
    "in_runbook",
    "origin_metadata",
    "stamp_process",
]

ORIGIN_SERVICE = "service"
ORIGIN_JOB = "job"
ORIGIN_EVAL = "eval"
ORIGIN_PLAYGROUND = "playground"
ORIGIN_ADHOC = "adhoc"
#: Every origin a run can have. Anything untagged is ``adhoc``.
ORIGINS = (ORIGIN_SERVICE, ORIGIN_JOB, ORIGIN_EVAL, ORIGIN_PLAYGROUND, ORIGIN_ADHOC)


def origin_metadata(origin: str, **fields: Any) -> Dict[str, Any]:
    """Trace metadata naming a run's origin: ``origin``, the given fields,
    and the same pairs as ``tags`` (Langfuse filters on tags). Fields
    that are ``None`` are left out."""
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin {origin!r}; expected one of {', '.join(ORIGINS)}")
    out: Dict[str, Any] = {"origin": origin}
    out.update({k: v for k, v in fields.items() if v is not None})
    out["tags"] = [f"{k}:{v}" for k, v in out.items() if not isinstance(v, (dict, list))]
    return out


# ── the job of steps a job's runs belong to ────────────────────────────

_RUNBOOK: ContextVar[Optional[Tuple[str, str]]] = ContextVar("operonx_runbook_run", default=None)


@contextmanager
def in_runbook(name: str, run_id: str) -> Iterator[None]:
    """Everything a job of steps starts inside this block — its steps,
    their runs — knows the run it belongs to. Tasks created inside
    inherit it (asyncio copies the context), so it reaches every item."""
    token = _RUNBOOK.set((name, run_id))
    try:
        yield
    finally:
        _RUNBOOK.reset(token)


def current_runbook() -> Optional[Tuple[str, str]]:
    """``(job, job_run)`` of the job of steps, under :func:`in_runbook`."""
    return _RUNBOOK.get()


# ── the code's version ──────────────────────────────────────────────────


def code_version(root: Union[str, Path]) -> Dict[str, Any]:
    """``{"version": <commit>, "version_dirty": bool}`` for the git
    checkout at *root*, or ``{}`` when it is not one (or git is absent).

    Read once per process at bootstrap — never per run. "p95 went up" is
    only actionable as "p95 went up after commit abc123"; a dirty tree
    says the numbers came from code no commit holds."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if head.returncode != 0:
            return {}
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return {
        "version": head.stdout.strip()[:12],
        "version_dirty": bool(status.stdout.strip()) if status.returncode == 0 else False,
    }


def stamp_process(root: Union[str, Path], project: str) -> None:
    """What every run this process makes carries — the project and the
    code's version — and where consumers resolve relative directories.
    Called by `Application.bootstrap()`, once."""
    from operonx.core.workflow_trace import set_project_root, set_run_metadata

    set_project_root(Path(root))
    set_run_metadata(project=project, **code_version(root))
