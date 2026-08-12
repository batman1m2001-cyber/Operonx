"""The workspace — what a coding agent is allowed to touch, and what it read.

Two invariants live here, and both exist because the failure they prevent
is silent.

**Path containment.** Every filesystem tool resolves its argument through
:meth:`Workspace.resolve`, which rejects anything outside the root. It
resolves symlinks first: a link inside the root pointing at ``/etc`` is
the whole attack, and a naive ``startswith`` check passes it.

**Read-before-edit.** ``edit`` refuses to touch a file the agent has not
read, and refuses again if the file changed since that read. A model that
edits from memory produces a patch that applies cleanly to the file it
imagined — the edit succeeds, and the damage is discovered later.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

__all__ = ["Workspace", "WorkspaceError", "OutsideWorkspace", "StaleRead"]


class WorkspaceError(Exception):
    """Base for workspace policy violations."""


class OutsideWorkspace(WorkspaceError):
    """A path resolved outside the workspace root."""


class StaleRead(WorkspaceError):
    """An edit was attempted without a current read of the file."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


@dataclass
class Workspace:
    """A rooted view of the filesystem plus the agent's read ledger.

    Args:
        root: Directory the agent may operate in. Resolved once; the
            resolved value is what containment is checked against, so a
            symlinked root behaves as its target throughout.
        max_read_bytes: Files larger than this are truncated on read
            rather than refused — a coding agent asking for a 40 MB log
            wants the head of it, not an error.
    """

    root: Path
    max_read_bytes: int = 400_000
    _seen: Dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(os.path.realpath(str(self.root)))
        if not self.root.is_dir():
            raise WorkspaceError(f"workspace root is not a directory: {self.root}")

    # ---------------------------------------------------------------- paths

    def resolve(self, path: str) -> Path:
        """Resolve ``path`` against the root, refusing to leave it.

        Symlinks are resolved **before** the containment test. Checking a
        lexical path would accept a link inside the root that points
        anywhere at all.

        Raises:
            OutsideWorkspace: the resolved path is not under the root.
        """
        if not str(path).strip():
            raise WorkspaceError("path is empty")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        real = Path(os.path.realpath(str(candidate)))
        if real != self.root and self.root not in real.parents:
            raise OutsideWorkspace(
                f"{path!r} resolves to {real}, outside the workspace root {self.root}"
            )
        return real

    def relative(self, path: Path) -> str:
        """Path as the agent should see it — relative to the root."""
        try:
            return str(path.relative_to(self.root)) or "."
        except ValueError:  # pragma: no cover - resolve() prevents this
            return str(path)

    # ---------------------------------------------------------------- reads

    def record_read(self, path: Path, content: bytes) -> None:
        """Note that the agent has seen ``content`` at ``path``."""
        self._seen[str(path)] = _digest(content)

    def has_read(self, path: Path) -> bool:
        return str(path) in self._seen

    def check_fresh(self, path: Path) -> None:
        """Raise unless the agent read this file and it is unchanged.

        Raises:
            StaleRead: never read, or changed on disk since the read.
        """
        key = str(path)
        known = self._seen.get(key)
        rel = self.relative(path)
        if known is None:
            raise StaleRead(
                f"{rel} has not been read in this session. Read it first — "
                f"editing from memory produces a patch that fits the file you "
                f"imagined, and it applies cleanly."
            )
        try:
            current = _digest(path.read_bytes())
        except FileNotFoundError:
            raise StaleRead(f"{rel} no longer exists") from None
        if current != known:
            raise StaleRead(
                f"{rel} changed on disk since it was read. Read it again before editing."
            )

    def forget(self, path: Path) -> None:
        """Drop a read record — after a write, the old content is gone."""
        self._seen.pop(str(path), None)

    # ---------------------------------------------------------------- misc

    def is_binary(self, data: bytes) -> bool:
        """A NUL byte in the first block is the usual heuristic, and it is
        good enough — the cost of being wrong is a refused read, not a
        corrupted file."""
        return b"\0" in data[:4096]

    def read_text(self, path: Path) -> tuple[str, bool]:
        """Return ``(text, truncated)`` for a file inside the workspace."""
        data = path.read_bytes()
        if self.is_binary(data):
            raise WorkspaceError(f"{self.relative(path)} is a binary file")
        truncated = len(data) > self.max_read_bytes
        if truncated:
            data = data[: self.max_read_bytes]
        self.record_read(path, path.read_bytes())
        return data.decode("utf-8", errors="replace"), truncated


_ACTIVE: Optional[Workspace] = None


def set_workspace(ws: Workspace) -> None:
    """Install the workspace the tools operate against.

    A module-level singleton because ``@tool`` functions are registered at
    import time and take only the arguments the model supplies — there is
    nowhere to thread a workspace through. The CLI sets it once at startup.
    """
    global _ACTIVE
    _ACTIVE = ws


def active_workspace() -> Workspace:
    """The installed workspace.

    Raises:
        WorkspaceError: nothing was installed. Failing here beats
            defaulting to the process CWD, which is how an agent ends up
            editing the directory it happened to be launched from.
    """
    if _ACTIVE is None:
        raise WorkspaceError("no workspace installed — call set_workspace() first")
    return _ACTIVE
