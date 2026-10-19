"""The coder that runs headless Claude Code (``claude -p``).

Ported from the studio's assistant (``operonx_studio/chat.py`` and
``assistant.py``): finding the binary, the scrubbed child environment, the
stream-json events, rate limits and sign-in errors, stopping a process
group. What is new here is what an unattended engine needs: the session id
chosen before the process starts (``--session-id``), so a crash can always
resume it; structured output (``--json-schema``); a spend cap
(``--max-budget-usd``); a wall-clock timeout; and one result with a status
that says what the round should do next.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import signal
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

from operonx.core.loggings import LOGGER

from operonx_project.coder.base import CoderRequest, CoderResult, Started
from operonx_project.coder.roles import base_deny, sandbox_settings, tools_of

__all__ = ["ClaudeCodeCoder", "activity_label", "base_env", "find_claude", "kill_group"]

# A stream-json line can carry a whole file a tool read.
_LINE_LIMIT = 64 * 1024 * 1024
_STDERR_TAIL = 40
_STOP_GRACE_S = 3.0

# the CLI's words when it is not signed in, or its sign-in no longer works (from the studio)
_AUTH_ERROR = re.compile(
    r"please run /login|invalid api key|not logged in|oauth token (has )?expired|"
    r"authentication_error|\b401\b|unauthorized|login required",
    re.I,
)
# the CLI's words for a plan limit; older versions append "|<epoch>"
_RATE_ERROR = re.compile(r"usage limit|rate limit|rate_limit|limit reached|429", re.I)
_RATE_EPOCH = re.compile(r"\|(\d{10})\b")
# the sandbox could not start (seen on Ubuntu 24.04 with its user-namespace
# restriction: every command fails before it runs, with apply-seccomp's words)
_SANDBOX_BROKEN = re.compile(
    r"apply-seccomp:|bwrap: |sandbox (is )?(not available|unavailable)|"
    r"failed to (start|create) (the )?sandbox",
    re.I,
)


def find_claude() -> Optional[str]:
    """The claude binary: ``OPERONX_PROJECT_CLAUDE_BIN``, then PATH, then the
    newest VS Code extension's bundled binary (a box that runs Claude Code in
    the IDE but never installed the CLI)."""
    explicit = os.environ.get("OPERONX_PROJECT_CLAUDE_BIN")
    if explicit:
        return explicit if Path(explicit).is_file() else None
    on_path = shutil.which("claude")
    if on_path:
        return on_path

    def version_key(path: str) -> List[int]:
        m = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", path)
        return [int(g) for g in m.groups()] if m else [0, 0, 0]

    pattern = "/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"
    hits = glob.glob(str(Path.home()) + pattern) + glob.glob("/root" + pattern)
    hits = [h for h in hits if os.access(h, os.X_OK)]
    return max(hits, key=version_key) if hits else None


def base_env(*, keep_anthropic: bool = False) -> Dict[str, str]:
    """This process's environment for a Claude child, minus what would confuse
    it or leak into it.

    - ``CLAUDE*``: the engine is often started from inside a Claude Code
      session, and the child would believe it is a nested one;
    - ``ANTHROPIC_*``: the host's own API key (kept only when asked);
    - ``VIRTUAL_ENV`` and its ``bin`` on PATH: the engine's own environment,
      which is not the product's (``uv run`` in the worktree would warn and
      may pick the wrong one);
    - every other credential (:func:`operonx_project.safety.scrub_env`): the
      session's shell runs code the engine did not write.

    HOME survives: the sign-in lives under it.
    """
    from operonx_project.safety import scrub_env

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("CLAUDE")
        and k not in ("CLAUDECODE", "AI_AGENT")
        and (keep_anthropic or not k.startswith("ANTHROPIC"))
    }
    env = scrub_env(env, keep=[k for k in env if keep_anthropic and k.startswith("ANTHROPIC")])
    venv = env.pop("VIRTUAL_ENV", None)
    if venv and env.get("PATH"):
        bin_dir = str(Path(venv) / "bin")
        env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep) if p != bin_dir)
    return env


def kill_group(pgid: int, sig: int = signal.SIGKILL) -> bool:
    """Signal a coder's process group (an orphan found after a crash).
    False when it is already gone."""
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


class ClaudeCodeCoder:
    """Run one Claude Code session turn per :meth:`run`.

    ``state_dirs`` are denied to every role (the engine's own state, the
    held-out tests). ``binary`` and ``env`` default to :func:`find_claude` and
    :func:`base_env`; tests pass a scripted fake binary.
    """

    def __init__(
        self,
        binary: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        state_dirs: Optional[List[str]] = None,
        model: Optional[str] = None,
    ):
        self.binary = binary
        self.env = env
        self.state_dirs = [str(Path(d).expanduser().resolve()) for d in (state_dirs or [])]
        self.model = model

    # ── the command ──

    def command(self, req: CoderRequest, binary: str, session: Optional[str]) -> List[str]:
        tools = tools_of(req.role)
        cmd = [binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
        if req.session:
            cmd += ["--resume", req.session]
            if req.fork:
                cmd.append("--fork-session")
        elif session:
            cmd += ["--session-id", session]
        if req.system:
            cmd += ["--append-system-prompt", req.system]
        model = req.model or self.model
        if model:
            cmd += ["--model", model]
        if req.schema is not None:
            cmd += ["--json-schema", json.dumps(req.schema, separators=(",", ":"))]
        if req.max_usd is not None:
            cmd += ["--max-budget-usd", f"{req.max_usd:.4f}"]
        for d in req.add_dirs:
            cmd += ["--add-dir", str(d)]
        hidden = [str(Path(d).expanduser().resolve()) for d in req.hide]
        deny = base_deny([*self.state_dirs, *hidden]) + list(req.deny)
        if "Bash" in tools:
            places = ["~/.claude", *self.state_dirs, *hidden]
            settings = sandbox_settings(req.allow_write, req.allowed_domains, places) if req.sandbox else {}
            if settings:
                cmd += ["--settings", json.dumps(settings, separators=(",", ":"))]
        # --restricted: file tools confined to the working directories, no
        # host settings, hooks or plugins; --tools names the shell for the
        # implementer only. Nothing can prompt, so what is not allowed is refused.
        cmd += [
            "--restricted",
            "--strict-mcp-config",
            "--permission-mode",
            "acceptEdits",
            "--permission-prompts",
            "none",
            "--tools",
            ",".join(tools),
            "--disallowedTools",
            *deny,
            # --allowedTools takes the rest of the arguments
            "--allowedTools",
            *tools,
        ]
        return cmd

    # ── one turn ──

    async def run(self, req: CoderRequest, on_start: Optional[Callable[[Started], None]] = None) -> CoderResult:
        binary = self.binary or find_claude()
        if binary is None:
            return CoderResult(status="infra", error="no claude binary (set OPERONX_PROJECT_CLAUDE_BIN)")
        # a new session's id is chosen here, so it is known before any crash
        session = None if req.session else str(uuid.uuid4())
        if req.session and not req.fork:
            session = req.session
        cmd = self.command(req, binary, session)
        env = dict(self.env) if self.env is not None else base_env()
        stdin = json.dumps({"type": "user", "message": {"role": "user", "content": req.prompt}}) + "\n"
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(req.cwd),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_LINE_LIMIT,
                start_new_session=True,  # its own process group: a stop reaches every child
            )
        except OSError as e:
            return CoderResult(status="infra", error=f"could not start claude: {e}")
        if on_start is not None:
            on_start(Started(session=session, pgid=proc.pid))
        LOGGER.info(f"coder start role={req.role} session={session} pgid={proc.pid} {req.tags}")
        reader = _Reader(req, session, proc.pid)
        stderr_tail: Deque[str] = deque(maxlen=_STDERR_TAIL)
        try:
            assert proc.stdin is not None
            proc.stdin.write(stdin.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            await asyncio.wait_for(
                asyncio.gather(reader.read(proc.stdout, on_start), _tail(proc.stderr, stderr_tail), proc.wait()),
                timeout=req.timeout_s,
            )
        except asyncio.TimeoutError:
            await _stop(proc)
            return reader.result("timeout", f"stopped after {req.timeout_s:.0f} s", t0, stderr_tail)
        except asyncio.CancelledError:
            await _stop(proc)
            raise
        except (BrokenPipeError, ConnectionResetError) as e:
            await _stop(proc)
            return reader.result("infra", f"claude closed its input: {e}", t0, stderr_tail)
        finally:
            reader.close()
        return reader.finish(proc.returncode, t0, stderr_tail)


async def _tail(stream: Optional[asyncio.StreamReader], tail: Deque[str]) -> None:
    if stream is None:
        return
    async for line in stream:
        tail.append(line.decode(errors="replace").rstrip())


async def _stop(proc: asyncio.subprocess.Process) -> None:
    """SIGINT to the group (the CLI closes its transcript), SIGKILL after a grace."""
    if proc.returncode is not None:
        return
    if not kill_group(proc.pid, signal.SIGINT):
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_S)
    except asyncio.TimeoutError:
        kill_group(proc.pid, signal.SIGKILL)
        await proc.wait()


def activity_label(tool: str, inputs: Dict[str, Any], cwd: Optional[Path] = None) -> str:
    """One short line for a tool call: ``Edit src/x.py``, ``Bash pytest -q``."""
    target = inputs.get("file_path") or inputs.get("path") or inputs.get("notebook_path") or ""
    if target and cwd is not None:
        try:
            target = str(Path(target).resolve().relative_to(Path(cwd).resolve()))
        except ValueError:
            pass
    what = target or inputs.get("command") or inputs.get("pattern") or inputs.get("url") or inputs.get("query") or ""
    what = " ".join(str(what).split())
    return f"{tool} {what[:80]}".strip()


class _Reader:
    """Folds a session's stream-json events into one :class:`CoderResult`."""

    def __init__(self, req: CoderRequest, session: Optional[str], pgid: Optional[int] = None):
        self.req = req
        self.session = session
        self.pgid = pgid
        self.final: Optional[Dict[str, Any]] = None
        self.rate: Optional[Dict[str, Any]] = None  # a rejection: the limit is hit
        self.last_rate: Optional[Dict[str, Any]] = None
        self.tools: Dict[str, int] = {}
        self.pending: Dict[str, str] = {}  # tool_use id → tool name
        self.tool_errors: List[Dict[str, str]] = []
        self.unreadable = 0
        self.file = None
        if req.transcript is not None:
            req.transcript.parent.mkdir(parents=True, exist_ok=True)
            self.file = open(req.transcript, "a", encoding="utf-8")

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None

    async def read(self, stream: Optional[asyncio.StreamReader], on_start) -> None:
        if stream is None:
            return
        async for raw in stream:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            if self.file is not None:
                self.file.write(line + "\n")
                self.file.flush()
            try:
                event = json.loads(line)
            except ValueError:
                self.unreadable += 1
                continue
            if isinstance(event, dict):
                self.event(event, on_start)

    def event(self, event: Dict[str, Any], on_start) -> None:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            sid = event.get("session_id")
            if sid and sid != self.session:
                # a fork's new id is only known here
                self.session = sid
                if on_start is not None:
                    on_start(Started(session=sid, pgid=self.pgid))
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "tool")
                    self.tools[name] = self.tools.get(name, 0) + 1
                    if block.get("id"):
                        self.pending[block["id"]] = name
                    if self.req.on_activity is not None:
                        try:
                            self.req.on_activity(activity_label(name, block.get("input") or {}, self.req.cwd))
                        except Exception:  # noqa: BLE001 — the live line never stops a session
                            LOGGER.debug("on_activity raised", exc_info=True)
        elif kind == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                    content = block.get("content")
                    if isinstance(content, list):
                        content = "\n".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
                    self.tool_errors.append(
                        {
                            "tool": self.pending.get(block.get("tool_use_id") or "", "tool"),
                            "error": str(content or "")[:2000],
                        }
                    )
        elif kind == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            self.last_rate = info
            if str(info.get("status") or "").lower() == "rejected":
                self.rate = info
        elif kind == "result":
            self.final = event
            self.session = event.get("session_id") or self.session

    def result(self, status: str, error: str, t0: float, stderr_tail: Deque[str], **kw: Any) -> CoderResult:
        f = self.final or {}
        usage = f.get("usage") or {}
        tokens = {
            k: int(usage.get(k) or 0)
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            if usage.get(k) is not None
        }
        return CoderResult(
            status=status,
            session=self.session,
            text=str(f.get("result") or "") if isinstance(f.get("result"), str) else "",
            cost_usd=float(f.get("total_cost_usd") or 0.0),
            turns=int(f.get("num_turns") or 0),
            duration_ms=int(f.get("duration_ms") or round((time.monotonic() - t0) * 1000)),
            tokens=tokens,
            error=error,
            tool_errors=self.tool_errors,
            tools=self.tools,
            denials=[d for d in (f.get("permission_denials") or []) if isinstance(d, dict)],
            rate=self.last_rate,
            stderr_tail="\n".join(stderr_tail),
            **kw,
        )

    def finish(self, returncode: Optional[int], t0: float, stderr_tail: Deque[str]) -> CoderResult:
        f = self.final
        said = ""
        if f is not None:
            said = str(f.get("result") or f.get("subtype") or "")
            errors = f.get("errors")
            if isinstance(errors, list) and errors:
                said = (said + " " + " ".join(str(e) for e in errors)).strip()
        tail = "\n".join(stderr_tail)
        if self.rate is not None or (f is not None and f.get("is_error") and _RATE_ERROR.search(said)):
            return self.result("rate_limited", said or "rate limited", t0, stderr_tail, resets_at=self._resets(said))
        if f is None:
            if _AUTH_ERROR.search(tail):
                return self.result("auth", tail[-500:], t0, stderr_tail)
            return self.result("infra", f"claude exited {returncode} without a result", t0, stderr_tail)
        subtype = str(f.get("subtype") or "")
        if f.get("is_error") or subtype.startswith("error"):
            if _AUTH_ERROR.search(said) or _AUTH_ERROR.search(tail):
                return self.result("auth", said, t0, stderr_tail)
            if "budget" in subtype:
                return self.result("budget", said or subtype, t0, stderr_tail)
            return self.result("error", said or subtype or "error", t0, stderr_tail)
        broken = [e for e in self.tool_errors if e["tool"] == "Bash" and _SANDBOX_BROKEN.search(e["error"])]
        if broken:
            # fail closed: the work may look done, but nothing the shell was
            # asked to run (tests included) ran
            return self.result("infra", "sandbox unavailable: " + broken[0]["error"][-300:], t0, stderr_tail)
        output = None
        if self.req.schema is not None:
            output = f.get("structured_output")
            if output is None:
                output = _json_in(f.get("result"))
            if not isinstance(output, dict):
                return self.result("error", "no structured output matching the schema", t0, stderr_tail)
        return self.result("ok", "", t0, stderr_tail, output=output)

    def _resets(self, said: str) -> Optional[float]:
        if self.rate is not None:
            at = self.rate.get("resetsAt")
            if at:
                return float(at)
        m = _RATE_EPOCH.search(said or "")
        return float(m.group(1)) if m else None


def _json_in(text: Any) -> Optional[Dict[str, Any]]:
    """A JSON object in a final message (an older CLI's way of answering a schema)."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("\n") + 1 :] if "\n" in s else s
    try:
        out = json.loads(s)
    except ValueError:
        a, b = s.find("{"), s.rfind("}")
        if a < 0 or b <= a:
            return None
        try:
            out = json.loads(s[a : b + 1])
        except ValueError:
            return None
    return out if isinstance(out, dict) else None
