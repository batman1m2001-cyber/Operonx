"""Review findings, checked mechanically; held-out failures, cut to names and messages.

A reviewer's blocker or major finding blocks only when it points at the diff
(plan §2, decision 5): it cites a file the card changed and a line that file
has. Otherwise it is kept as *unverified* and does not block.

A finding is fingerprinted by its file and its title, so the same complaint
after a fix is recognised (and goes to arbitration). The reviewer is shown
the previous round's open findings and may name one it repeats
(``same_as``); that id wins over the hash, since a fresh session words the
same issue differently.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

__all__ = [
    "BLOCKING",
    "check_findings",
    "fingerprint",
    "heldout_failures",
]

BLOCKING = ("blocker", "major")


def fingerprint(file: str, title: str) -> str:
    basis = f"{file.strip().lstrip('./')}\n{' '.join(re.sub(r'[^a-z0-9 ]', ' ', title.lower()).split())}"
    return hashlib.sha256(basis.encode()).hexdigest()[:12]


def _line_count(path: Path) -> int:
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def check_findings(
    findings: Iterable[Dict[str, Any]], worktree: Path, changed: Sequence[str], previous: Sequence[str] = ()
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split a reviewer's findings into ``(blocking, other)``.

    Each comes back with ``fingerprint`` and ``verified`` set; a blocker or
    major that does not cite a changed file and an existing line gets
    ``verified=False`` and an ``unverified`` reason, and does not block.
    ``previous`` holds the fingerprints the reviewer was shown.
    """
    changed_set = {c.strip().lstrip("./") for c in changed}
    blocking: List[Dict[str, Any]] = []
    other: List[Dict[str, Any]] = []
    for raw in findings:
        f = dict(raw)
        sev = str(f.get("severity", "minor")).lower()
        f["severity"] = sev
        file = str(f.get("file") or "").strip().lstrip("./")
        f["file"] = file
        same = str(f.get("same_as") or "")
        f["fingerprint"] = same if same in previous else fingerprint(file, str(f.get("title", "")))
        if sev not in BLOCKING:
            f["verified"] = False
            other.append(f)
            continue
        why = ""
        try:
            line = int(f.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        if file not in changed_set:
            why = f"{file or '(no file)'} is not in the diff"
        elif line < 1 or line > _line_count(worktree / file):
            why = f"{file} has no line {f.get('line')}"
        if why:
            f["verified"] = False
            f["unverified"] = why
            other.append(f)
        else:
            f["verified"] = True
            blocking.append(f)
    return blocking, other


# pytest's short summary with -rf: "FAILED path::test - message"
_SUMMARY = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$", re.M)


def heldout_failures(output: str, heldout_dir: Path, limit: int = 30) -> List[str]:
    """The failing held-out tests as ``name: message``, with nothing of the
    test code or where it lives: the directory is stripped from every name,
    and the message is the first line of the error only."""
    hidden = str(Path(heldout_dir))
    out: List[str] = []
    for m in _SUMMARY.finditer(output):
        name = m.group(2).replace(hidden + "/", "").replace(hidden, "")
        name = name.split("::", 1)[-1] if "::" in name else Path(name).name
        msg = (m.group(3) or m.group(1).lower()).replace(hidden, "<held-out>").strip()
        item = f"{name}: {msg[:300]}"
        if item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out
