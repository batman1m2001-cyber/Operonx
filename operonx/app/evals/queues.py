"""Review queues — what people should look at, and what they said.

A **queue** is a named list of things to review — runs, sessions, ops, an
experiment's cases — declared in ``operonx.toml``::

    [[queue]]
    name      = "call_failures"
    rubric    = { polite = "bool", resolved = "categorical" }
    reviewers = 2          # people per item; κ between them is reported

An online eval sends its failing runs to one (``queue = {to = …}``), and
``operonx eval queue add`` fills one from a run filter or an experiment's
failed cases. An item is a pointer, kept in ``.operonx/queues/<name>.jsonl``
(append-only, the last line per item wins); the judgement is a **score**:
:func:`review` writes a ``source="human"`` score named ``review``
(``good``/``bad``, labels, a note) and one per rubric entry, each with
``queue=<name>``. An item is done when ``reviewers`` people have reviewed
it. Because a review is a score, :func:`~operonx.app.evals.align` measures
a judge against reviews like any other human label.

Studio's ``reviews.jsonl`` (one line per review, the last per run wins) is
read as scores by :func:`reviews_as_scores` — no migration needed for
alignment to see them — and :func:`migrate_reviews` writes them to a score
store once.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from operonx.telemetry.scores import Score, ScoreFilter, ScoreStore

from .align import _human_says, cohen_kappa
from .dataset import _writing

__all__ = [
    "QueueItem",
    "QueueSpec",
    "REVIEW",
    "enqueue",
    "migrate_reviews",
    "pending",
    "queue_agreement",
    "queue_items",
    "review",
    "reviews_as_scores",
]

#: The score every review writes.
REVIEW = "review"
VERDICTS = ("good", "bad")
#: The data types a rubric entry can have.
RUBRIC_TYPES = ("bool", "numeric", "categorical")
#: What an item can point at, and the ids each needs.
_NEEDS = {
    "trace": ("trace_id",),
    "session": ("session_id",),
    "op": ("trace_id", "op_id"),
    "item": ("experiment_id", "case_id"),
}
#: Below this many items reviewed by two people, agreement is not a number.
MIN_SHARED = 10


@dataclass(frozen=True)
class QueueSpec:
    """A ``[[queue]]`` block. ``rubric`` maps score names to data types;
    ``review`` (good/bad) is always asked and never listed."""

    name: str
    rubric: Mapping[str, str] = field(default_factory=dict)
    reviewers: int = 1
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"queue name {self.name!r}: letters, digits, '-' and '_' (it names a file)"
            )
        bad = {k: v for k, v in self.rubric.items() if v not in RUBRIC_TYPES}
        if bad:
            raise ValueError(
                f"queue {self.name!r}: rubric types {bad}; each is one of {', '.join(RUBRIC_TYPES)}"
            )
        if REVIEW in self.rubric:
            raise ValueError(
                f"queue {self.name!r}: {REVIEW!r} is every queue's good/bad; leave it out of rubric"
            )
        if isinstance(self.reviewers, bool) or int(self.reviewers) < 1:
            raise ValueError(f"queue {self.name!r}: reviewers is a number of people, ≥ 1")


@dataclass
class QueueItem:
    """One thing to review: what it is (``target`` and its ids), where it
    came from (``source``: ``online:<rule>``, ``experiment:<id>``,
    ``runs``), and why (``reason``: the checks that failed)."""

    item_id: str
    target: str
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    op_id: Optional[str] = None
    experiment_id: Optional[str] = None
    case_id: Optional[str] = None
    source: str = ""
    reason: str = ""
    added_at: float = 0.0
    removed: bool = False

    def ids(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in _NEEDS[self.target]}


def _path(queues_dir: Union[str, Path], queue: str) -> Path:
    QueueSpec(queue)  # the name is a file name: checked
    return Path(queues_dir) / f"{queue}.jsonl"


def _item_id(target: str, ids: Mapping[str, Any]) -> str:
    raw = json.dumps([target, *[ids[k] for k in _NEEDS[target]]], default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def enqueue(
    queues_dir: Union[str, Path],
    queue: str,
    *,
    target: str = "trace",
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    op_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    case_id: Optional[str] = None,
    source: str = "",
    reason: str = "",
) -> str:
    """Put one thing in *queue*; returns its item id. The same thing added
    twice is one item (its latest source and reason)."""
    if target not in _NEEDS:
        raise ValueError(f"a queue item's target is one of {', '.join(_NEEDS)}, not {target!r}")
    ids = {
        "trace_id": trace_id,
        "session_id": session_id,
        "op_id": op_id,
        "experiment_id": experiment_id,
        "case_id": case_id,
    }
    missing = [k for k in _NEEDS[target] if not ids[k]]
    if missing:
        raise ValueError(f"a {target!r} queue item needs {', '.join(missing)}")
    item = QueueItem(
        _item_id(target, ids),
        target,
        **{k: v for k, v in ids.items() if v},
        source=source,
        reason=reason,
        added_at=round(time.time(), 3),
    )
    path = _path(queues_dir, queue)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _writing(path.parent), path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
    return item.item_id


def queue_items(queues_dir: Union[str, Path], queue: str) -> List[QueueItem]:
    """The queue's items, oldest first (by when each was first added)."""
    path = _path(queues_dir, queue)
    if not path.is_file():
        return []
    latest: Dict[str, QueueItem] = {}
    first: Dict[str, float] = {}
    with path.open("r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                raise ValueError(f"{path}:{n}: not JSON") from None
            item = QueueItem(
                **{k: v for k, v in raw.items() if k in QueueItem.__dataclass_fields__}
            )
            latest[item.item_id] = item
            first.setdefault(item.item_id, item.added_at)
    items = [i for i in latest.values() if not i.removed]
    return sorted(items, key=lambda i: (first[i.item_id], i.item_id))


def review(
    store: ScoreStore,
    *,
    author: str,
    verdict: Optional[str] = None,
    labels: Iterable[str] = (),
    note: str = "",
    rubric: Optional[Mapping[str, Any]] = None,
    queue: Optional[str] = None,
    target: str = "trace",
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    op_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    case_id: Optional[str] = None,
) -> List[Score]:
    """A person's review of one thing, as scores: ``review`` with the
    verdict (good/bad), labels and note, and one score per *rubric* answer
    (``True``/``False``, a number, or a label). Written and returned; a
    second review by the same person replaces the first."""
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"a review verdict is good, bad or None, not {verdict!r}")
    ids = {
        k: v
        for k, v in {
            "trace_id": trace_id,
            "session_id": session_id,
            "op_id": op_id,
            "experiment_id": experiment_id,
            "case_id": case_id,
        }.items()
        if v is not None
    }
    clean = sorted({str(x).strip() for x in labels if str(x).strip()})
    rows = [
        Score(
            score_name=REVIEW,
            target=target,
            source="human",
            data_type="categorical",
            label=verdict,
            passed=None if verdict is None else verdict == "good",
            reason=str(note or "")[:4000],
            author=author,
            queue=queue,
            metadata={"labels": clean} if clean else {},
            **ids,
        )
    ]
    for name, answer in (rubric or {}).items():
        rows.append(_rubric_score(name, answer, target, author, queue, ids))
    store.put_scores(rows)
    return rows


def _rubric_score(
    name: str, answer: Any, target: str, author: str, queue: Optional[str], ids: Mapping
) -> Score:
    if isinstance(answer, bool):
        kind = {"data_type": "bool", "passed": answer}
    elif isinstance(answer, (int, float)):
        kind = {"data_type": "numeric", "value": float(answer)}
    else:
        kind = {"data_type": "categorical", "label": str(answer)}
    return Score(
        score_name=name, target=target, source="human", author=author, queue=queue, **kind, **ids
    )


def pending(
    store: ScoreStore, queues_dir: Union[str, Path], spec: QueueSpec
) -> List[Tuple[QueueItem, List[str]]]:
    """The items still waiting for a review, each with who already reviewed
    it — done is ``spec.reviewers`` people's ``review`` scores in this
    queue."""
    seen = _reviewers(store, spec.name)
    out = []
    for item in queue_items(queues_dir, spec.name):
        who = sorted(seen.get(_target_key(item.target, item.ids()), ()))
        if len(who) < spec.reviewers:
            out.append((item, who))
    return out


def _target_key(target: str, ids: Mapping[str, Any]) -> Tuple[Any, ...]:
    return (target, *[ids.get(k) for k in _NEEDS[target]])


def _score_key(s: Score) -> Tuple[Any, ...]:
    return _target_key(s.target, asdict(s))


def _reviewers(store: ScoreStore, queue: str) -> Dict[Tuple[Any, ...], set]:
    out: Dict[Tuple[Any, ...], set] = defaultdict(set)
    for s in store.scores(ScoreFilter(score_name=REVIEW, source="human"), limit=10_000_000):
        if s.queue == queue and s.target in _NEEDS:
            out[_score_key(s)].add(str(s.author))
    return out


def queue_agreement(
    store: ScoreStore, queue: str, score_names: Optional[Sequence[str]] = None
) -> Dict[str, Dict[str, Any]]:
    """How much reviewers agree, per score in *queue* (``review`` and each
    bool rubric entry): Cohen's κ over the items two people both judged —
    the first two reviewers of each. Fewer than :data:`MIN_SHARED` shared
    items gives no number, and says so: if people disagree with each other,
    a judge's alignment against them means little."""
    by: Dict[str, Dict[Tuple[Any, ...], Dict[str, Optional[bool]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for s in store.scores(ScoreFilter(source="human"), limit=10_000_000):
        if s.queue != queue or s.target not in _NEEDS:
            continue
        if score_names is not None and s.score_name not in score_names:
            continue
        by[s.score_name][_score_key(s)][str(s.author)] = _human_says(s)
    out: Dict[str, Dict[str, Any]] = {}
    for name, targets in sorted(by.items()):
        tp = fp = fn = tn = 0
        shared = 0
        for votes in targets.values():
            said = [votes[a] for a in sorted(votes) if votes[a] is not None][:2]
            if len(said) < 2:
                continue
            shared += 1
            a, b = said
            tp += a and b
            tn += (not a) and (not b)
            fp += (not a) and b
            fn += a and (not b)
        if shared < MIN_SHARED:
            out[name] = {
                "shared": shared,
                "kappa": None,
                "note": f"{shared} items reviewed by two people — fewer than {MIN_SHARED}, "
                "no agreement measured",
            }
            continue
        stats = cohen_kappa(tp, fp, fn, tn)
        out[name] = {
            "shared": shared,
            "kappa": stats.get("kappa"),
            "kappa_ci": stats.get("kappa_ci"),
            "agreement": (tp + tn) / shared,
        }
    return out


def reviews_as_scores(path: Union[str, Path]) -> List[Score]:
    """Studio's ``reviews.jsonl`` as ``review`` scores on runs: the last
    line per run and person, a verdict-less line kept for its labels and
    note."""
    path = Path(path)
    if not path.is_file():
        return []
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("run"):
                latest[(str(rec["run"]), str(rec.get("user") or "unknown"))] = rec
    out = []
    for (run, user), rec in latest.items():
        verdict = rec.get("verdict") if rec.get("verdict") in VERDICTS else None
        labels = sorted({str(x) for x in rec.get("labels") or ()})
        out.append(
            Score(
                score_name=REVIEW,
                target="trace",
                source="human",
                data_type="categorical",
                label=verdict,
                passed=None if verdict is None else verdict == "good",
                reason=str(rec.get("note") or ""),
                author=user,
                trace_id=run,
                metadata={"labels": labels, "from": "reviews.jsonl"}
                if labels
                else {"from": "reviews.jsonl"},
                created_at=float(rec.get("at") or 0.0) or time.time(),
            )
        )
    return out


def migrate_reviews(path: Union[str, Path], store: ScoreStore) -> int:
    """Write :func:`reviews_as_scores` to *store*; returns how many. Running
    it again writes the same rows (a review's id is its run, name and
    author)."""
    rows = reviews_as_scores(path)
    if rows:
        store.put_scores(rows)
    return len(rows)
