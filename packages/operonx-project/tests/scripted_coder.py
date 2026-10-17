"""A coder that plays a script per role, for task_flow tests.

Each role (implementer, reviewer, test_author, examiner, arbiter, triage)
has a list of steps; call *i* of a role plays step *i* (the last repeats).
A step is a dict:

- ``write``: ``{path: text}`` (relative to the session's cwd, or absolute);
  ``None`` as the text deletes the file;
- ``output``: the structured output (a default per role otherwise);
- ``status``, ``cost``, ``text``, ``resets_at``, ``error``;
- ``die``: SIGKILL this process after logging and writing (a crash mid-round).

or a callable ``(req, i) -> dict``. With ``log``, calls are appended to that
JSONL file and *i* counts the calls there, so a script goes on across a
restart: the call a crash interrupted counts as played.
"""

from __future__ import annotations

import json
import os
import signal
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from operonx_project.coder.base import CoderRequest, CoderResult, Started

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "implementer": {"summary": "done"},
    "reviewer": {"findings": []},
    "test_author": {"files": []},
    "examiner": {"files": []},
    "arbiter": {"verdicts": []},
    "triage": {"action": "escalate", "category": "requirements"},
}


class ScriptedCoder:
    def __init__(self, script: Dict[str, List[Any]], log: Optional[Path] = None):
        self.script = script
        self.log = Path(log) if log else None
        self.calls: List[Dict[str, Any]] = []

    def _index(self, role: str) -> int:
        if self.log is None:
            return sum(1 for c in self.calls if c["role"] == role)
        if not self.log.exists():
            return 0
        return sum(1 for line in self.log.read_text().splitlines() if json.loads(line)["role"] == role)

    def of(self, role: str) -> List[Dict[str, Any]]:
        """This role's calls (from the log when there is one: every process's)."""
        if self.log is not None and self.log.exists():
            rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        else:
            rows = self.calls
        return [c for c in rows if c["role"] == role]

    async def run(self, req: CoderRequest, on_start: Optional[Callable[[Started], None]] = None) -> CoderResult:
        role = req.role
        i = self._index(role)
        steps = self.script.get(role) or [{}]
        step = steps[min(i, len(steps) - 1)]
        if callable(step):
            step = step(req, i)
        session = req.session if (req.session and not req.fork) else f"{role}-{uuid.uuid4().hex[:8]}"
        if on_start is not None:
            on_start(Started(session, None))
        rec = {"role": role, "i": i, "session_in": req.session, "session": session, "prompt": req.prompt,
               "cwd": str(req.cwd), "hide": [str(h) for h in req.hide], "model": req.model}  # fmt: skip
        self.calls.append(rec)
        if self.log is not None:
            with open(self.log, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        for path, text in (step.get("write") or {}).items():
            f = Path(path) if Path(path).is_absolute() else Path(req.cwd) / path
            if text is None:
                if f.exists():
                    f.unlink()
                continue
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        if step.get("die"):
            os.kill(os.getpid(), signal.SIGKILL)
        output = step.get("output", DEFAULTS.get(role))
        return CoderResult(
            status=step.get("status", "ok"),
            session=session,
            text=step.get("text", ""),
            output=output if req.schema is not None else None,
            cost_usd=step.get("cost", 0.01),
            resets_at=step.get("resets_at"),
            error=step.get("error", ""),
        )
