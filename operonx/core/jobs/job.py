"""`Job` — work to do with an Operon over data that does not talk back.

A job is not an op and not a graph. It is the declaration that a
`[[serve]]` block is for a listener: which graph, where the items come
from, where results go, what identifies an item, how many run at once,
what a failure means, where the record is written. The graph stays what
it was; the job is everything the graph should not know.

::

    score = Job("score_calls", graph=score_call,
                source="source:calls_today", sink="sink:scores",
                key="call_id", concurrency=8, on_error="skip")
    run = await score.run()             # JobRun: counts, per-item status, path
    run = await score.run(resume=True)  # only what the last run did not finish
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from .record import JobRun
from .runner import parse_on_error

__all__ = ["Job", "SESSION_MODES"]

#: How many runs a job mints. ``per_item`` is one run per item, which is
#: what makes failure, retry and resume honest. ``stream`` is one run fed
#: every item through `ingress` — the callbot shape: shared state, one
#: trace, and no per-item accounting.
SESSION_MODES = ("per_item", "stream")

#: How far a stream job's source may run ahead of its graph. A file is
#: pulled, not pushed, so this only caps what sits buffered; it is a
#: default rather than a required choice because nothing here is a
#: socket nobody can slow down.
DEFAULT_MAX_INFLIGHT = 1024

KeyFn = Callable[[Any], Any]


class Job:
    """See the module docstring.

    Args:
        name: The job's name; the record directory is named after it.
        graph: The Operon to run — a compiled ``Operon``, a ``GraphOp``, a
            ``@graph`` factory, or ``"module:attr"`` naming one. A factory
            is compiled the way ``[[serve]]`` compiles it: every parameter
            becomes a runtime input, fillable from ``inputs``.
        source: A ``"source:name"`` resource key, a ``.jsonl``/``.csv``
            path, an iterable, a generator function, or a ``Source``.
        sink: A ``"sink:name"`` key, a path, a list, a callable, a
            ``Sink``, or ``None`` for no sink (the record still counts).
        key: The item field that identifies it, or a function of the
            item. Without one every item gets a random id and the job
            cannot resume.
        session: ``"per_item"`` or ``"stream"`` (see :data:`SESSION_MODES`).
        concurrency: Items in flight at once (per_item).
        max_inflight: Items buffered ahead of the graph (stream).
        on_error: ``"skip"``, ``"stop"`` or ``"retry:N"``.
        trace: Trace consumers for the runs, as ``Operon(trace=...)``
            takes them. Ignored when ``graph`` is already an ``Operon``.
        inputs: Static inputs every run receives.
        item_input: For a graph with no doors: the input the item is
            bound to. The run's result is then written to the sink as the
            item's result. Leave unset for a graph with `ingress`/`egress`.
        schedule: Cron text. Declarative — recorded and listed, run by
            whatever calls ``operonx-run``.
        record_dir: Where runs are recorded; ``<record_dir>/<name>/<run>``.
        description: One line, for ``--list`` and the studio.
    """

    def __init__(
        self,
        name: str,
        *,
        graph: Any,
        source: Any = None,
        sink: Any = None,
        key: Union[str, KeyFn, None] = None,
        session: str = "per_item",
        concurrency: int = 4,
        max_inflight: int = DEFAULT_MAX_INFLIGHT,
        on_error: str = "skip",
        trace: Any = None,
        inputs: Optional[Dict[str, Any]] = None,
        item_input: Optional[str] = None,
        schedule: Optional[str] = None,
        record_dir: Union[str, Path] = "jobs",
        description: str = "",
    ):
        if not name or not isinstance(name, str):
            raise ValueError("a job needs a name")
        if session not in SESSION_MODES:
            raise ValueError(f"job {name!r}: session must be one of {SESSION_MODES}, not {session!r}")
        if int(concurrency) < 1:
            raise ValueError(f"job {name!r}: concurrency must be at least 1")
        if int(max_inflight) < 1:
            raise ValueError(f"job {name!r}: max_inflight must be at least 1")
        parse_on_error(on_error)                          # fail at declaration, not at 2 a.m.
        if key is not None and not (isinstance(key, str) or callable(key)):
            raise TypeError(f"job {name!r}: key must be a field name or a function of the item")

        self.name = name
        self.graph = graph
        self.source = source
        self.sink = sink
        self.key = key
        self.session = session
        self.concurrency = int(concurrency)
        self.max_inflight = int(max_inflight)
        self.on_error = on_error
        self.trace = trace
        self.inputs: Dict[str, Any] = dict(inputs or {})
        self.item_input = item_input
        self.schedule = schedule
        self.record_dir = Path(record_dir)
        self.description = description
        self._engine: Any = None

    # -- the graph ---------------------------------------------------------

    def engine(self) -> Any:
        """The compiled Operon, built once."""
        if self._engine is not None:
            return self._engine
        from operonx.core.engine import Operon
        from operonx.core.ops.graph import GraphOp

        g = self.graph
        if isinstance(g, str):
            from operonx.core.serve.registry import load_object

            g = load_object(g, field=f"job {self.name!r} graph")
        if isinstance(g, Operon):
            self._engine = g
        elif isinstance(g, GraphOp):
            self._engine = Operon(g, trace=self.trace)
        elif callable(g):
            try:
                params = {p: None for p in inspect.signature(g).parameters}
            except (TypeError, ValueError):
                params = {}
            self._engine = Operon(g, params=params or None, trace=self.trace)
        else:
            raise TypeError(f"job {self.name!r}: graph is a {type(g).__name__}, "
                            "not an Operon, a GraphOp or a @graph factory")
        return self._engine

    # -- identity ----------------------------------------------------------

    def key_of(self, item: Any) -> str:
        """The item's identity, as a non-empty string."""
        if self.key is None:
            return uuid.uuid4().hex[:12]
        if callable(self.key):
            value = self.key(item)
        elif isinstance(item, Mapping):
            if self.key not in item:
                raise KeyError(f"item has no field {self.key!r}")
            value = item[self.key]
        else:
            if not hasattr(item, self.key):
                raise KeyError(f"item has no attribute {self.key!r}")
            value = getattr(item, self.key)
        text = "" if value is None else str(value)
        if not text:
            raise ValueError(f"item key {self.key!r} is empty")
        return text

    # -- running -----------------------------------------------------------

    async def run(self, *, resume: bool = False) -> JobRun:
        """Run the job once and return its record."""
        from .runner import run_job

        return await run_job(self, resume=resume)

    def run_sync(self, *, resume: bool = False) -> JobRun:
        """`run()` from synchronous code — a script, a cron entry."""
        return asyncio.run(self.run(resume=resume))

    # -- from the manifest ---------------------------------------------------

    @classmethod
    def from_spec(cls, spec: Any, root: Union[str, Path, None] = None) -> "Job":
        """A Job from a ``[[job]]`` block (``operonx.core.manifest.JobSpec``).

        Paths in the block are relative to *root*, the manifest's
        directory; ``source:`` / ``sink:`` keys and ``module:attr`` graph
        entries are left for the hub and the importer to resolve.
        """
        root = Path(root) if root is not None else Path.cwd()

        def located(value: Optional[str]) -> Any:
            if value is None:
                return None
            if ":" in value and not Path(value).exists():
                return value                                  # a resource key
            path = Path(value)
            return path if path.is_absolute() else root / path

        record_dir = Path(spec.record_dir) if spec.record_dir else Path("jobs")
        if not record_dir.is_absolute():
            record_dir = root / record_dir
        return cls(
            spec.name,
            graph=spec.graph,
            source=located(spec.source),
            sink=located(spec.sink),
            key=spec.key,
            session=spec.session,
            concurrency=spec.concurrency,
            max_inflight=spec.max_inflight or DEFAULT_MAX_INFLIGHT,
            on_error=spec.on_error,
            trace=list(spec.trace) or None,
            inputs=dict(spec.inputs),
            item_input=spec.item_input,
            schedule=spec.schedule,
            record_dir=record_dir,
            description=spec.description,
        )

    # -- describing --------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        """What the job is, for run.json and ``--list``. No secrets: only
        the names of things."""
        g = self.graph
        graph_name = g if isinstance(g, str) else getattr(g, "name", None) or getattr(g, "__name__", type(g).__name__)

        def shown(value: Any) -> Any:
            if value is None or isinstance(value, str):
                return value
            return str(value) if isinstance(value, Path) else repr(value)

        return {
            "graph": graph_name,
            "source": shown(self.source),
            "sink": shown(self.sink),
            "key": self.key if isinstance(self.key, str) else (getattr(self.key, "__name__", "fn") if self.key else None),
            "session": self.session,
            "concurrency": self.concurrency if self.session == "per_item" else None,
            "max_inflight": self.max_inflight if self.session == "stream" else None,
            "on_error": self.on_error if self.session == "per_item" else None,
            "item_input": self.item_input,
            "schedule": self.schedule,
            "description": self.description,
        }

    def __repr__(self) -> str:
        d = self.describe()
        return (f"Job({self.name!r}, graph={d['graph']!r}, source={d['source']!r}, "
                f"sink={d['sink']!r}, key={d['key']!r}, {self.session}, "
                f"concurrency={self.concurrency}, on_error={self.on_error!r})")
