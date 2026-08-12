"""A bash session that survives between tool calls.

Spawning a fresh shell per command is simpler and wrong: ``cd src`` then
``ls`` lists the original directory, ``export TOKEN=…`` is gone by the next
call, and an activated virtualenv never applies. The model has no way to
see that, so it writes a correct sequence and reads a confusing result.

The mechanism is a long-lived ``bash`` reading from a pipe. After each
command a marker carrying the exit status is echoed, and the reader stops
at the marker. That is what makes "the command finished" observable on a
stream that never closes.

Timeouts kill the shell rather than trying to interrupt it. A shell whose
command was abandoned mid-flight has unknown state — an unterminated
heredoc, a half-typed pipeline — and reusing it would corrupt every later
command with an error pointing at the wrong line.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

__all__ = ["PersistentShell", "ShellResult", "ShellTimeout"]


class ShellTimeout(Exception):
    """A command exceeded its wall clock and the shell was replaced."""


@dataclass
class ShellResult:
    """One command's outcome.

    ``output`` interleaves stdout and stderr, because that is what a
    terminal shows and what the model needs in order to read a traceback
    next to the command that produced it.
    """

    output: str
    exit_code: int
    truncated: bool = False
    cwd: str = ""


class PersistentShell:
    """A single ``bash`` process, reused across commands.

    Args:
        cwd: Starting directory.
        max_output: Per-command output cap, in characters. Output is kept
            head-and-tail: a build log's first lines say what ran and its
            last lines say why it failed, and the middle is the part
            nobody reads.
        env: Extra environment. ``PS1`` is cleared and the shell runs
            non-interactively, so prompts never contaminate output.
    """

    def __init__(
        self,
        cwd: str | Path,
        *,
        max_output: int = 30_000,
        env: Optional[dict] = None,
    ) -> None:
        self._cwd = str(cwd)
        self._max_output = max_output
        self._env = {**os.environ, "PS1": "", "TERM": "dumb", **(env or {})}
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()
        self._bash = shutil.which("bash")
        if not self._bash:
            raise RuntimeError("bash not found on PATH")

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            self._bash,
            "--noprofile",
            "--norc",
            "-s",
            cwd=self._cwd,
            env=self._env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (asyncio.TimeoutError, ProcessLookupError, BrokenPipeError):
            try:
                proc.kill()
            except ProcessLookupError:  # pragma: no cover - already gone
                pass
            await asyncio.gather(proc.wait(), return_exceptions=True)

    # --------------------------------------------------------------- running

    async def run(self, command: str, *, timeout: float = 120.0) -> ShellResult:
        """Run ``command`` in the session and wait for it to finish.

        Serialised: two concurrent calls would interleave their output on
        one pipe with no way to tell whose is whose.

        Raises:
            ShellTimeout: the command outran ``timeout``. The shell is
                replaced, so state set by earlier commands is lost — the
                message says so, because silently starting fresh is how an
                agent ends up confused about its own ``cd``.
        """
        async with self._lock:
            await self.start()
            marker = f"__OPX_{uuid.uuid4().hex}__"
            proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None

            # `printf` after the command so the status is captured whether
            # the command succeeded, failed, or was never a command at all.
            payload = f"{command}\nprintf '\\n{marker}%s\\n' \"$?\"\n"
            try:
                proc.stdin.write(payload.encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                await self._replace()
                raise ShellTimeout(
                    "the shell died before the command could run; a fresh one was started"
                ) from None

            try:
                raw, code = await asyncio.wait_for(
                    self._read_until(proc.stdout, marker), timeout=timeout
                )
            except asyncio.TimeoutError:
                await self._replace()
                raise ShellTimeout(
                    f"command exceeded {timeout:.0f}s and was killed. The shell was "
                    f"replaced, so any cd/export from earlier commands is gone. "
                    f"Re-establish what you need, and consider running long work "
                    f"in the background with output to a file."
                ) from None

            text, truncated = self._clamp(raw)
            if code == -1:
                # EOF instead of a marker: the command was `exit`, or bash
                # died. Replace it now rather than letting the next command
                # hit a closed pipe and report a confusing "shell died
                # before the command could run".
                await self._replace()
                return ShellResult(
                    output=(
                        f"{text}\n\n[the shell exited; a fresh one was started, so any "
                        f"cd/export from earlier commands is gone]"
                    ).strip(),
                    exit_code=-1,
                    truncated=truncated,
                    cwd=self._cwd,
                )
            cwd = await self._pwd()
            return ShellResult(output=text, exit_code=code, truncated=truncated, cwd=cwd)

    async def _read_until(self, stream: asyncio.StreamReader, marker: str) -> tuple[str, int]:
        """Accumulate output up to the marker line; return (text, exit code)."""
        chunks: list[str] = []
        needle = marker.encode()
        while True:
            line = await stream.readline()
            if not line:
                # EOF: the shell exited on its own (`exit`, or it crashed).
                # -1 rather than 0 — "we never saw a status" is not success.
                return "".join(chunks), -1
            if needle in line:
                head, _, tail = line.partition(needle)
                if head:
                    chunks.append(head.decode(errors="replace"))
                try:
                    code = int(tail.strip() or -1)
                except ValueError:  # pragma: no cover - printf always writes an int
                    code = -1
                return "".join(chunks), code
            chunks.append(line.decode(errors="replace"))

    async def _pwd(self) -> str:
        """Where the shell is now — so the model can see its own ``cd``."""
        marker = f"__OPX_PWD_{uuid.uuid4().hex}__"
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:  # pragma: no cover
            return self._cwd
        try:
            proc.stdin.write(f"pwd\nprintf '\\n{marker}%s\\n' \"$?\"\n".encode())
            await proc.stdin.drain()
            out, _ = await asyncio.wait_for(self._read_until(proc.stdout, marker), timeout=5.0)
            return out.strip() or self._cwd
        except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
            return self._cwd

    async def _replace(self) -> None:
        await self.close()
        await self.start()

    def _clamp(self, text: str) -> tuple[str, bool]:
        if len(text) <= self._max_output:
            return text.rstrip("\n"), False
        head = self._max_output // 2
        tail = self._max_output - head
        omitted = len(text) - self._max_output
        return (
            f"{text[:head]}\n\n… {omitted} characters omitted …\n\n{text[-tail:]}".rstrip("\n"),
            True,
        )


_ACTIVE: Optional[PersistentShell] = None


def set_shell(shell: PersistentShell) -> None:
    """Install the shell the ``bash`` tool runs against."""
    global _ACTIVE
    _ACTIVE = shell


def active_shell() -> PersistentShell:
    """The installed shell, started lazily by the first command."""
    if _ACTIVE is None:
        raise RuntimeError("no shell installed — call set_shell() first")
    return _ACTIVE
