"""What a job run leaves behind.

One directory per run, two files::

    <record_dir>/<job>/<run_id>/
      run.json      job, status, started, ended, counts, source, sink, …
      items.jsonl   one line per item: key, status, error, trace_id, ms, sent

This is the difference between a job and a for-loop. A runner that
reports "OK" for ninety items that produced nothing has no record; with
one, each of those ninety is a line whose ``sent`` is 0 and whose status
is ``empty``, and ``--resume`` knows which keys still need doing.

Items are written as they finish and flushed one at a time, so a run
that is killed still says what it got through.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

__all__ = [
    "ItemResult",
    "JobRun",
    "RunRecord",
    "ITEM_OK", "ITEM_FAILED", "ITEM_EMPTY", "ITEM_SKIPPED",
    "RUN_RUNNING", "RUN_OK", "RUN_FAILED", "RUN_STOPPED",
    "done_keys",
    "last_run",
    "runs_of",
]

#: Per-item outcomes. ``empty`` is a run that finished cleanly and sent
#: nothing to the sink — not a failure, but the thing a batch most often
#: gets wrong silently, so it has its own name.
ITEM_OK = "ok"
ITEM_FAILED = "failed"
ITEM_EMPTY = "empty"
ITEM_SKIPPED = "skipped"       # already done in the run being resumed

#: Whole-run outcomes.
RUN_RUNNING = "running"
RUN_OK = "ok"                  # no item failed
RUN_FAILED = "failed"          # some item failed and the policy carried on
RUN_STOPPED = "stopped"        # the policy was `stop` and something failed

_COUNTED = (ITEM_OK, ITEM_FAILED, ITEM_EMPTY, ITEM_SKIPPED)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    """Chronological when sorted: UTC stamp to the microsecond. Two runs
    of one job in the same microsecond collide on ``mkdir`` and the
    record retries with a fresh id, so uniqueness is enforced by the
    directory rather than assumed from the clock."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S") + f"-{now.microsecond:06d}"


@dataclass
class ItemResult:
    key: str
    status: str
    error: Optional[str] = None
    trace_id: Optional[str] = None
    ms: float = 0.0
    sent: int = 0
    attempts: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ItemResult":
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__ if k in data})


@dataclass
class JobRun:
    """A finished (or in-flight) run, read back from its two files."""

    job: str
    run_id: str
    path: Path
    status: str
    started: str
    ended: Optional[str]
    counts: Dict[str, int]
    items: List[ItemResult] = field(default_factory=list)
    resume_from: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> List[ItemResult]:
        return [i for i in self.items if i.status == ITEM_OK]

    @property
    def failed(self) -> List[ItemResult]:
        return [i for i in self.items if i.status == ITEM_FAILED]

    @property
    def empty(self) -> List[ItemResult]:
        return [i for i in self.items if i.status == ITEM_EMPTY]

    @property
    def skipped(self) -> List[ItemResult]:
        return [i for i in self.items if i.status == ITEM_SKIPPED]

    @classmethod
    def load(cls, path: str | Path) -> "JobRun":
        path = Path(path)
        meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
        items: List[ItemResult] = []
        items_file = path / "items.jsonl"
        if items_file.exists():
            with items_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        items.append(ItemResult.from_dict(json.loads(line)))
        # The four item statuses are recounted from items.jsonl, which is
        # the truth; anything else run.json counted (a stream run's `fed`
        # and `sent`) is kept as written.
        counts = {s: 0 for s in _COUNTED}
        counts.update({k: v for k, v in (meta.get("counts") or {}).items() if k not in _COUNTED})
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return cls(
            job=meta["job"], run_id=meta["run_id"], path=path, status=meta["status"],
            started=meta["started"], ended=meta.get("ended"), counts=counts, items=items,
            resume_from=meta.get("resume_from"),
            meta={k: v for k, v in meta.items()
                  if k not in ("job", "run_id", "status", "started", "ended", "resume_from")},
        )

    def summary(self) -> str:
        c = self.counts
        if "fed" in c:                                     # a stream run: one run, no items
            return f"{self.job} {self.run_id} {self.status}  fed={c['fed']} sent={c.get('sent', 0)}"
        return (f"{self.job} {self.run_id} {self.status}  "
                f"ok={c.get(ITEM_OK, 0)} failed={c.get(ITEM_FAILED, 0)} "
                f"empty={c.get(ITEM_EMPTY, 0)} skipped={c.get(ITEM_SKIPPED, 0)}")

    def __repr__(self) -> str:
        return f"JobRun({self.summary()})"


class RunRecord:
    """The writer. Opened when the run starts, finished exactly once."""

    def __init__(self, root: str | Path, job: str, *, meta: Optional[Dict[str, Any]] = None,
                 resume_from: Optional[str] = None):
        self.job = job
        for _ in range(3):
            self.run_id = new_run_id()
            self.path = Path(root) / job / self.run_id
            try:
                self.path.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError(f"could not mint a fresh run directory under {Path(root) / job}")
        self.started = _now()
        self.resume_from = resume_from
        self.meta = dict(meta or {})
        self.counts: Dict[str, int] = {s: 0 for s in _COUNTED}
        self.error: Optional[str] = None
        self._write_run(RUN_RUNNING, ended=None)
        self._items = (self.path / "items.jsonl").open("a", encoding="utf-8")

    def _write_run(self, status: str, ended: Optional[str]) -> None:
        payload = {
            "job": self.job,
            "run_id": self.run_id,
            "status": status,
            "started": self.started,
            "ended": ended,
            "resume_from": self.resume_from,
            "counts": dict(self.counts),
            "error": self.error,
            **self.meta,
        }
        tmp = self.path / "run.json.tmp"
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.path / "run.json")

    def item(self, result: ItemResult) -> None:
        self._items.write(json.dumps(result.as_dict(), ensure_ascii=False, default=str))
        self._items.write("\n")
        self._items.flush()
        self.counts[result.status] = self.counts.get(result.status, 0) + 1

    def finish(self, status: str, error: Optional[str] = None, *,
               counts: Optional[Dict[str, int]] = None,
               extra: Optional[Dict[str, Any]] = None) -> JobRun:
        """Close the record. ``counts`` adds to the four item counts (a
        stream run reports ``fed`` and ``sent``); ``extra`` lands in
        run.json beside the job's description (a stream run's trace id)."""
        if error:
            self.error = error
        if counts:
            self.counts.update(counts)
        if extra:
            self.meta.update(extra)
        self._items.close()
        self._write_run(status, ended=_now())
        return JobRun.load(self.path)


# -- reading back ----------------------------------------------------------

def runs_of(root: str | Path, job: str) -> List[Path]:
    """Every run directory of *job*, oldest first."""
    base = Path(root) / job
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "run.json").exists())


def last_run(root: str | Path, job: str) -> Optional[JobRun]:
    runs = runs_of(root, job)
    return JobRun.load(runs[-1]) if runs else None


def done_keys(run: Optional[JobRun]) -> Set[str]:
    """Keys a resumed run may skip: finished cleanly, or already skipped
    because an earlier run had finished them. ``failed`` keys run again,
    and keys a stopped run never reached are simply absent."""
    if run is None:
        return set()
    return {i.key for i in run.items if i.status in (ITEM_OK, ITEM_EMPTY, ITEM_SKIPPED)}
