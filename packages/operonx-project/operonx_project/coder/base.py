"""What a coder is asked, what it answers, and the protocol between them.

A coder is one agent session working in one directory: the implementer in a
card's worktree, a reviewer reading a diff, the planner reading the BRD. The
flows never see a CLI; they see a :class:`CoderRequest` going in and a
:class:`CoderResult` coming out, so a scripted coder can stand in for the
real one in every test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

__all__ = ["Coder", "CoderRequest", "CoderResult", "RESULT_STATUSES", "Started"]

#: Why a session ended. Only ``ok`` carries an answer; the others say what
#: the round should do instead (a rate limit pauses the milestone, an auth
#: error asks the owner to sign in, an infra error fails the card).
RESULT_STATUSES = (
    "ok",  # the session finished its turn
    "error",  # it ended with an error the model could see (bad output, max turns)
    "budget",  # --max-budget-usd was reached
    "rate_limited",  # the plan's limit; ``resets_at`` says when it lifts
    "auth",  # not signed in, or the sign-in no longer works
    "timeout",  # ran past ``timeout_s`` and was stopped
    "cancelled",  # stopped by the engine (pause, cancel, shutdown)
    "infra",  # the binary is missing, it crashed, its output was unreadable
)


@dataclass
class CoderRequest:
    """One session turn.

    ``session`` resumes that session (later rounds of the same implementer);
    ``fork`` resumes it under a new id (a revision that starts from what the
    session knew without changing it). ``schema`` asks for structured output,
    validated by the CLI; it comes back as :attr:`CoderResult.output`.
    """

    prompt: str
    cwd: Path
    role: str = "implementer"  # implementer | reviewer | planner | triage (coder.roles)
    session: Optional[str] = None
    fork: bool = False
    schema: Optional[Dict[str, Any]] = None
    system: str = ""  # appended to Claude Code's own system prompt
    model: Optional[str] = None
    max_usd: Optional[float] = None
    timeout_s: float = 1800.0
    transcript: Optional[Path] = None  # every stream-json line, for the studio and the audit
    sandbox: bool = True  # the shell runs in the OS sandbox (bubblewrap); see coder.roles
    allow_write: List[str] = field(default_factory=list)  # extra writable paths for the sandboxed shell
    allowed_domains: List[str] = field(default_factory=list)  # network the sandboxed shell may reach
    deny: List[str] = field(default_factory=list)  # extra permission deny rules
    hide: List[Path] = field(default_factory=list)  # directories this session may not read (held-out tests)
    add_dirs: List[Path] = field(default_factory=list)  # more directories the file tools may use
    tags: Dict[str, str] = field(default_factory=dict)  # milestone, card, round: for logs only
    #: told each tool call as it starts ("Edit src/x.py"): the board's live line (plan D10)
    on_activity: Optional[Callable[[str], None]] = field(default=None, repr=False, compare=False)


@dataclass
class CoderResult:
    status: str
    session: Optional[str] = None
    text: str = ""  # the final message
    output: Optional[Dict[str, Any]] = None  # the structured output, when a schema was given
    cost_usd: float = 0.0  # the CLI's total_cost_usd (on a Max plan: the notional API cost)
    turns: int = 0
    duration_ms: int = 0
    tokens: Dict[str, int] = field(default_factory=dict)
    error: str = ""
    resets_at: Optional[float] = None  # epoch seconds, for rate_limited
    tool_errors: List[Dict[str, str]] = field(default_factory=list)  # refused or failed tool calls
    tools: Dict[str, int] = field(default_factory=dict)  # tool name → calls
    denials: List[Dict[str, Any]] = field(default_factory=list)  # tool calls the permission rules refused
    rate: Optional[Dict[str, Any]] = None  # the plan's last rate_limit_info (windows used, resets)
    stderr_tail: str = ""

    def __post_init__(self) -> None:
        if self.status not in RESULT_STATUSES:
            raise ValueError(f"unknown coder status {self.status!r}")

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class Started:
    """Told to the caller as soon as it is known, before the session ends:
    the session id (to resume after a crash) and the process group (to kill
    an orphan after one)."""

    session: Optional[str]
    pgid: Optional[int]


class Coder(Protocol):
    async def run(self, req: CoderRequest, on_start: Optional[Callable[[Started], None]] = None) -> CoderResult:
        """Run one session turn. Cancelling the task stops the session."""
        ...
