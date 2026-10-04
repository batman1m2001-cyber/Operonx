"""What a score store holds — plain data, no I/O.

* :class:`Experiment` — one eval run: what produced it (the fingerprint's
  columns), how it went (status, counts, metrics, the gate);
* :class:`ExperimentItem` — one case × repeat of it: the case's run (trace
  id, status, time, cost), whether it passed, its output;
* :class:`Score` — one judgement of anything, from anyone: an eval's check
  on an item, a judge on a trace, a human on an op, a rule on a session,
  a pairwise preference between two experiments. One row type, so every
  screen is a filter over one table.

A score's id is derived from what it judges (:func:`score_id_of`), so
writing the same judgement twice — a retried batch, a re-published
experiment, a re-run online rule — is one row, and a human's edit replaces
their earlier value.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "DATA_TYPES",
    "SOURCES",
    "TARGETS",
    "Bucket",
    "Experiment",
    "ExperimentFilter",
    "ExperimentItem",
    "ExperimentPage",
    "ExperimentRecord",
    "Score",
    "ScoreFilter",
    "score_id_of",
]

#: Who judged: a deterministic check, a model, a person, an API caller.
SOURCES = ("code", "judge", "human", "api")
#: What was judged: an experiment's item, a whole run, one op execution,
#: a conversation, two experiments' answers to one case — or an evaluator
#: itself (a judge's alignment with human labels, one record per version).
TARGETS = ("item", "trace", "op", "session", "pair", "evaluator")
DATA_TYPES = ("bool", "numeric", "categorical")

#: The ids each target needs.
_NEEDS = {
    "item": ("experiment_id", "case_id"),
    "trace": ("trace_id",),
    "op": ("trace_id", "op_id"),
    "session": ("session_id",),
    "pair": ("experiment_id", "pair_experiment_id", "case_id"),
    "evaluator": ("evaluator_version",),
}


def _from(cls: Any, d: Dict[str, Any]) -> Any:
    known = cls.__dataclass_fields__  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Experiment:
    """One eval run. ``experiment_id`` is the job's ``run_id``; times are
    epoch seconds; ``metrics`` and ``gate`` are ``run.json["eval"]``'s."""

    experiment_id: str
    eval: str
    project: str = ""
    dataset: str = ""
    dataset_version: str = ""
    split: Optional[str] = None
    graph: str = ""
    code_version: Optional[str] = None
    version_dirty: Optional[bool] = None
    graph_hash: str = ""
    config_hash: str = ""
    evaluators_hash: str = ""
    operonx_version: str = ""
    variant: Optional[str] = None
    repeats: int = 1
    baseline_id: Optional[str] = None
    status: str = "running"
    started_at: float = 0.0
    ended_at: Optional[float] = None
    cases: int = 0
    errored: int = 0
    cost_usd: Optional[float] = None
    judge_cost_usd: Optional[float] = None
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    gate: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Experiment":
        return _from(cls, d)


@dataclass
class ExperimentItem:
    """One case × repeat of an experiment. ``output`` is the clipped value
    the job record holds; ``cost_usd`` and tokens are the case run's own."""

    experiment_id: str
    case_id: str
    repeat: int = 0
    case_hash: str = ""
    trace_id: Optional[str] = None
    status: str = "ok"
    ms: float = 0.0
    cost_usd: Optional[float] = None
    tokens_in: int = 0
    tokens_out: int = 0
    passed: Optional[bool] = None
    tags: List[str] = field(default_factory=list)
    cluster: Optional[str] = None
    output: Any = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExperimentItem":
        return _from(cls, d)


@dataclass
class Score:
    """One judgement. ``target`` says what was judged and which ids say
    it (see :data:`TARGETS`); ``source`` who judged. A ``bool`` score
    also holds ``value`` 0/1, so means work across sources. ``score_id``
    and ``created_at`` fill themselves in when left empty."""

    score_name: str
    target: str = "item"
    source: str = "code"
    data_type: str = "bool"
    value: Optional[float] = None
    passed: Optional[bool] = None
    label: Optional[str] = None
    reason: str = ""
    trace_id: Optional[str] = None
    op_id: Optional[str] = None
    session_id: Optional[str] = None
    experiment_id: Optional[str] = None
    pair_experiment_id: Optional[str] = None
    case_id: Optional[str] = None
    repeat: Optional[int] = None
    #: Of the judged run (``eval``/``service``…, the eval or service name):
    #: denormalised, for trend queries.
    origin: str = ""
    name: str = ""
    evaluator_version: str = ""
    judge_trace_id: Optional[str] = None
    cost_usd: Optional[float] = None
    author: Optional[str] = None
    #: The online rule that produced it (such scores expire; see the stores).
    rule: Optional[str] = None
    queue: Optional[str] = None
    #: The judged input/output, clipped — readable after the trace expires.
    snapshot: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    score_id: str = ""

    def __post_init__(self) -> None:
        for value, allowed, what in (
            (self.target, TARGETS, "target"),
            (self.source, SOURCES, "source"),
            (self.data_type, DATA_TYPES, "data_type"),
        ):
            if value not in allowed:
                raise ValueError(
                    f"score {self.score_name!r}: {what} is {value!r}; one of {', '.join(allowed)}"
                )
        if not self.score_name:
            raise ValueError("a score needs a score_name (the evaluator's name)")
        missing = [k for k in _NEEDS[self.target] if getattr(self, k) in (None, "")]
        if missing:
            raise ValueError(
                f"score {self.score_name!r}: a {self.target!r} score needs {', '.join(missing)}"
            )
        if self.source == "human" and not self.author:
            raise ValueError(f"score {self.score_name!r}: a human score needs its author")
        if self.data_type == "bool" and self.value is None and self.passed is not None:
            self.value = 1.0 if self.passed else 0.0
        if not self.created_at:
            self.created_at = time.time()
        if not self.score_id:
            self.score_id = score_id_of(self)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Score":
        return _from(cls, d)


def _sha(*parts: Any) -> str:
    text = json.dumps(parts, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def score_id_of(s: Score) -> str:
    """The id a score's own fields give it — so the same judgement is
    always the same row:

    * pairwise: (experiment, pair experiment, case, score name);
    * human: (what was judged, score name, author) — an edit replaces;
    * an experiment's item: (experiment, case, repeat, score name);
    * a run, an op, a session (online, rescore), an evaluator (its
      alignment): (trace, op, session, score name, evaluator version) — a
      new evaluator is a new score, the same one's record is replaced.
    """
    if s.target == "pair":
        return _sha("pair", s.experiment_id, s.pair_experiment_id, s.case_id, s.score_name)
    ids = (s.target, s.trace_id, s.op_id, s.session_id, s.experiment_id, s.case_id, s.repeat)
    if s.source == "human":
        return _sha("human", *ids, s.score_name, s.author)
    if s.target == "item":
        return _sha("item", s.experiment_id, s.case_id, s.repeat or 0, s.score_name)
    return _sha(s.target, s.trace_id, s.op_id, s.session_id, s.score_name, s.evaluator_version)


@dataclass
class ExperimentRecord:
    """One experiment and its items (by case, then repeat)."""

    experiment: Experiment
    items: List[ExperimentItem] = field(default_factory=list)


@dataclass
class ExperimentPage:
    """One page of experiments, newest first; ``next_cursor`` ``None`` at the end."""

    items: List[Experiment]
    next_cursor: Optional[str] = None
    total: Optional[int] = None


@dataclass
class ExperimentFilter:
    """Which experiments — a small data object, as :class:`RunFilter` is.
    Times are epoch seconds over ``started_at``, ``since`` inclusive."""

    project: Optional[str] = None
    eval: Optional[str] = None
    dataset: Optional[str] = None
    status: Optional[str] = None
    code_version: Optional[str] = None
    since: Optional[float] = None
    until: Optional[float] = None
    experiment_ids: Optional[Sequence[str]] = None

    #: The fields that are equality matches on a column of the same name.
    EQUAL = ("project", "eval", "dataset", "status", "code_version")


@dataclass
class ScoreFilter:
    """Which scores. Times are epoch seconds over ``created_at``."""

    experiment_id: Optional[str] = None
    trace_id: Optional[str] = None
    op_id: Optional[str] = None
    session_id: Optional[str] = None
    case_id: Optional[str] = None
    score_name: Optional[str] = None
    origin: Optional[str] = None
    name: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None
    rule: Optional[str] = None
    since: Optional[float] = None
    until: Optional[float] = None
    score_ids: Optional[Sequence[str]] = None

    EQUAL = (
        "experiment_id",
        "trace_id",
        "op_id",
        "session_id",
        "case_id",
        "score_name",
        "origin",
        "name",
        "source",
        "target",
        "rule",
    )


@dataclass
class Bucket:
    """One time bucket of one score: how many, their mean ``value``, and
    the share that passed (of those with a ``passed``)."""

    start: float
    score_name: str
    n: int
    mean: Optional[float]
    passed: Optional[float]
