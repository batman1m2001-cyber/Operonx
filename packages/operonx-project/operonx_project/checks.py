"""Run the project's own commands: tests, lint, types, the build check.

A check is a shell command the milestone policy declares (or the engine
derives for an operonx target). It runs in the card's worktree, on the host
or in a throwaway container with no network, under a timeout that kills its
whole process group. What comes back is an exit code and the tail of its
output: enough for the implementer to fix, never the whole log.

The **baseline** is the same checks on the integration tip before the card
starts. A card is judged on *new* failures, so a test that was already red
is not its fault and does not cost it a round.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from operonx.core.loggings import LOGGER

__all__ = ["Check", "CheckResult", "Comparison", "compare", "failure_signature", "run_check", "run_checks"]

_TAIL_LINES = 80
_TAIL_CHARS = 8000


@dataclass
class Check:
    """``mode="docker"`` runs ``cmd`` with ``sh -c`` in ``image``, the
    worktree mounted at ``/work`` and ``--network none``."""

    name: str
    cmd: str
    timeout_s: float = 600.0
    mode: str = "host"  # host | docker
    image: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in ("host", "docker"):
            raise ValueError(f"check mode must be 'host' or 'docker', not {self.mode!r}")
        if self.mode == "docker" and not self.image:
            raise ValueError(f"check {self.name!r}: docker mode needs an image")


@dataclass
class CheckResult:
    name: str
    ok: bool
    exit_code: Optional[int]  # None: it never ran to an exit (timeout, could not start)
    duration_s: float
    tail: str
    timed_out: bool = False
    failures: List[str] = field(default_factory=list)  # failing test ids, when the output names them

    @property
    def signature(self) -> str:
        return failure_signature(self)


# pytest's summary lines; unittest's; a generic "FAIL: name"
_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?)(?:\s+-\s.*)?$|^(?:FAIL|ERROR):\s+(\S+)", re.M)


def _failures(text: str) -> List[str]:
    out: List[str] = []
    for m in _FAILED.finditer(text):
        name = m.group(1) or m.group(2)
        if name and name not in out:
            out.append(name)
    return out


def _tail(text: str) -> str:
    lines = text.splitlines()[-_TAIL_LINES:]
    s = "\n".join(lines)
    return s[-_TAIL_CHARS:]


def _command(check: Check, cwd: Path) -> List[str]:
    if check.mode == "host":
        return ["/bin/sh", "-c", check.cmd]
    args = ["docker", "run", "--rm", "--network", "none", "-v", f"{cwd.resolve()}:/work", "-w", "/work"]
    uid = os.getuid() if hasattr(os, "getuid") else None
    if uid is not None:
        args += ["--user", f"{uid}:{os.getgid()}"]
    for k, v in check.env.items():
        args += ["-e", f"{k}={v}"]
    return args + [check.image or "", "sh", "-c", check.cmd]


async def run_check(check: Check, cwd: Path, env: Optional[Dict[str, str]] = None) -> CheckResult:
    """Run one check in *cwd*; stdout and stderr together, the tail kept."""
    t0 = time.monotonic()
    full_env = {**(env if env is not None else os.environ), **(check.env if check.mode == "host" else {})}
    try:
        proc = await asyncio.create_subprocess_exec(
            *_command(check, cwd),
            cwd=str(cwd),
            env=full_env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as e:
        return CheckResult(check.name, False, None, time.monotonic() - t0, f"could not start: {e}")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=check.timeout_s)
    except asyncio.TimeoutError:
        _kill(proc)
        out = b""
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        except (asyncio.TimeoutError, ValueError):
            pass
        text = out.decode(errors="replace")
        return CheckResult(
            check.name,
            False,
            None,
            time.monotonic() - t0,
            _tail(text + f"\n[stopped after {check.timeout_s:.0f} s]"),
            timed_out=True,
            failures=_failures(text),
        )
    except asyncio.CancelledError:
        _kill(proc)
        raise
    text = out.decode(errors="replace")
    ok = proc.returncode == 0
    LOGGER.info(f"check {check.name} exit={proc.returncode} in {time.monotonic() - t0:.1f}s")
    return CheckResult(
        check.name, ok, proc.returncode, time.monotonic() - t0, _tail(text), failures=[] if ok else _failures(text)
    )


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run_checks(checks: Sequence[Check], cwd: Path, env: Optional[Dict[str, str]] = None) -> List[CheckResult]:
    """Run *checks* one after another (they may share files, such as a cache)."""
    return [await run_check(c, cwd, env) for c in checks]


@dataclass
class Comparison:
    """A round's checks against the baseline: ``new`` failed now but passed
    on the integration tip; ``old`` failed on both (not the card's doing);
    ``fixed`` failed on the tip and pass now."""

    new: List[str]
    old: List[str]
    fixed: List[str]

    @property
    def ok(self) -> bool:
        return not self.new


def compare(baseline: Sequence[CheckResult], now: Sequence[CheckResult]) -> Comparison:
    """Judge *now* against *baseline*, by check and, where the output names
    them, by failing test: a check already red on the tip counts as new only
    for tests that were not already failing."""
    base = {r.name: r for r in baseline}
    new: List[str] = []
    old: List[str] = []
    fixed: List[str] = []
    for r in now:
        b = base.get(r.name)
        if r.ok:
            if b is not None and not b.ok:
                fixed.append(r.name)
            continue
        if b is None or b.ok:
            new.append(r.name)
            continue
        if r.failures and b.failures:
            extra = [f for f in r.failures if f not in b.failures]
            (new if extra else old).append(r.name if not extra else f"{r.name}: {', '.join(extra)}")
        elif r.exit_code != b.exit_code or r.timed_out != b.timed_out:
            new.append(r.name)
        else:
            old.append(r.name)
    return Comparison(new=new, old=old, fixed=fixed)


_NOISE = [
    (re.compile(r"0x[0-9a-fA-F]+"), "0x"),
    (re.compile(r"\b\d+(\.\d+)?\s*(s|ms|sec|seconds)\b"), "<t>"),
    (re.compile(r"\d+"), "<n>"),
    (re.compile(r"/[^\s:]*/(?=[^/\s:]+\.\w+)"), "…/"),
]


def failure_signature(result: CheckResult) -> str:
    """A short hash naming *how* a check failed, stable across rounds: the
    failing test ids when the output names them, else its last lines with
    times, numbers and directory prefixes removed. Three rounds with the same
    signature mean the approach is not working (plan §7)."""
    if result.ok:
        return ""
    if result.failures:
        basis = "\n".join(sorted(result.failures))
    else:
        basis = "\n".join(result.tail.splitlines()[-8:])
        for pattern, repl in _NOISE:
            basis = pattern.sub(repl, basis)
    basis = f"{result.name}\n{'timeout' if result.timed_out else ''}\n{basis}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]
