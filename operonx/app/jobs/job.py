"""`Job` — an Operon run over data that does not talk back.

A job is not an op and not a graph. It says which graph runs, over which
items, what identifies an item, what to do with all the results, and what
a failure means. The graph stays what it was; the job is everything the
graph should not know::

    qc = Job("qc", graph=check_case, items="cases.jsonl", input="case", key="id",
             reduce=score_cases, concurrency=4)
    run = qc.run_sync()        # or: operonx run qc  /  operonx run qc --resume
    run.results                # {key: result}
    run.reduced                # what score_cases returned

Jobs that must run in order, as one command, are a job of ``steps``::

    nightly = Job("nightly", steps=[fetch, qc, publish])

Design and the reasons for each choice: ``docs/JOBS_AND_GUIDES_PLAN.md``.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from operonx.core.policy import Retry

from .items import check_items
from .record import JobRun

__all__ = ["Job", "ON_ERROR", "default_record_dir"]

#: What a failed item does to the rest of the run.
ON_ERROR = ("skip", "stop")

KeyFn = Callable[[Any], Any]


def default_record_dir(folder: str = ".operonx/jobs") -> Path:
    """Where runs are recorded when nothing says otherwise: *folder* under
    the project — the nearest folder with an ``operonx.toml``, else the
    working directory."""
    from operonx.app.declare import project_root

    return project_root(Path.cwd()) / folder


def _compile(job: str, what: str, g: Any, trace: Any) -> Any:
    """A graph as a compiled Operon: an ``Operon`` as is, a ``GraphOp``
    wrapped, a module-level ``@graph`` with every parameter a run input."""
    from operonx.core.engine import Operon
    from operonx.core.ops.graph import GraphOp

    if isinstance(g, str):
        from operonx.app.serve.registry import load_object

        g = load_object(g, field=f"job {job!r} {what}")
    if isinstance(g, Operon):
        return g
    if isinstance(g, GraphOp):
        return Operon(g, trace=trace)
    if callable(g) and getattr(g, "_operonx_graph", False):
        try:
            params = {p: None for p in inspect.signature(g).parameters}
        except (TypeError, ValueError):
            params = {}
        # Named after the graph explicitly: built inside `Operon(...)` it
        # would take its name from the code around this call.
        return Operon(g(name=g.__name__, **params), params=params or None, trace=trace)
    raise TypeError(
        f"job {job!r}: {what} is a {type(g).__name__}, not an Operon, a GraphOp "
        "or a module-level @graph"
    )


class Job:
    """See the module docstring.

    Args:
        name: The job's name: the CLI argument and the record's folder.
        graph: A module-level ``@graph``, a ``GraphOp``, an ``Operon``, or
            ``"module:attr"`` naming one. Runs once per item.
        items: What to loop over: a function returning an iterable (called
            on every run), an iterable or async iterable, or a ``.jsonl``
            path. ``None`` runs the graph once.
        input: The graph parameter that receives the whole item. Without
            it, a dict item fills parameters by name and anything else goes
            to the graph's only free parameter. A graph with ``ingress``
            takes the item through its door instead.
        inputs: Fixed graph inputs for every item (``--set`` adds to them).
        key: The item field that identifies it, or a function of the item.
            Without one each item gets a random id and the job cannot resume.
        output: Also export each successful result as it finishes: a
            ``.jsonl`` path (``{"key", "result"}`` lines), or a function
            ``(key, result)``, sync or async.
        reduce: A module-level ``@graph`` taking ``results`` (the list of
            every successful result, in key order) plus ``inputs``. Runs once,
            after the last item; its outputs are ``run.reduced``.
        concurrency: Items in flight at once.
        on_error: ``"skip"`` (carry on) or ``"stop"`` (start nothing new,
            and do not reduce). Either way the run is ``failed`` when an
            item failed — unless ``fail_run=False``.
        fail_run: ``False`` for a batch where a failed item is an
            outcome, not a broken run: each failure is still in the record
            (``on_item`` sees it), and the run — and the next step — goes on
            ``ok``. Default: the class's (``True``; ``False`` for an ``Eval``).
        retry: ``Retry(max_attempts=N)``: a failed or timed-out item runs
            again, with the policy's backoff between attempts.
        timeout: Seconds one item's run may take; past it, the run is
            cancelled and the item recorded ``timeout``.
        preflight: Resource keys (``"llm:x"``) that must answer before any
            item runs, so a dead endpoint fails the job once, in seconds.
        steps: Instead of ``graph``: jobs to run in order, as one command.
            The first step whose run is not ``ok`` stops the rest.
        trace: Trace consumers for the runs (``[tracing]`` in operonx.toml
            overrides it). Ignored when ``graph`` is already an ``Operon``.
        keep_results: Keep each result in the record's ``results.jsonl``.
            ``False`` for results too big to keep; ``reduce`` then has
            nothing to read and is refused.
        on_item: Called with each item's ``ItemResult`` as it is recorded
            (sync or async): progress lines, events for a host. It never
            breaks the run. ``run(on_item=...)`` replaces it for that run.
        record_dir: Where runs are recorded (``<record_dir>/<name>/<run>``).
            Default: the project's — ``[jobs] dir`` in operonx.toml, else
            ``.operonx/jobs`` under the project root.
        description: One line, for ``--list`` and the studio.
    """

    #: What a run of this job carries as its origin (an `Eval` says "eval").
    origin = "job"
    #: Whether a failed item fails the run. An `Eval` says no: a failed case
    #: is a failed verdict, and its gate (or threshold) decides the run.
    items_fail_run = True
    #: The project folder its runs go to when no ``record_dir`` is given
    #: (``[jobs] dir`` in operonx.toml overrides it for plain jobs).
    folder = ".operonx/jobs"

    def __init__(
        self,
        name: str,
        *,
        graph: Any = None,
        items: Any = None,
        input: Optional[str] = None,  # noqa: A002 — the graph parameter's name
        inputs: Optional[Dict[str, Any]] = None,
        key: Union[str, KeyFn, None] = None,
        output: Union[str, Path, Callable[..., Any], None] = None,
        reduce: Any = None,
        concurrency: int = 4,
        on_error: str = "skip",
        retry: Optional[Retry] = None,
        timeout: Optional[float] = None,
        preflight: Optional[Sequence[str]] = None,
        steps: Optional[Sequence["Job"]] = None,
        trace: Any = None,
        keep_results: bool = True,
        record_dir: Union[str, Path, None] = None,
        on_item: Optional[Callable[[Any], Any]] = None,
        description: str = "",
        fail_run: Optional[bool] = None,
    ):
        if not name or not isinstance(name, str):
            raise ValueError("a job needs a name")
        if fail_run is not None:
            self.items_fail_run = fail_run
        self.name = name
        self.description = description
        self.steps: Optional[List[Job]] = None
        if steps is not None:
            self._check_steps(steps, graph=graph, items=items, reduce=reduce, output=output)
            self.steps = list(steps)
        elif graph is None:
            raise ValueError(f"job {name!r} needs a graph (or steps=[...])")
        check_items(items, name)
        if input is not None and not (isinstance(input, str) and input):
            raise TypeError(f"job {name!r}: input is the name of a graph parameter")
        if key is not None and not (isinstance(key, str) or callable(key)):
            raise TypeError(f"job {name!r}: key must be a field name or a function of the item")
        if output is not None and not callable(output):
            if Path(output).suffix.lower() != ".jsonl":
                raise ValueError(
                    f"job {name!r}: output={str(output)!r} — a path must be a .jsonl file; "
                    "for anything else pass a function (key, result)"
                )
        if int(concurrency) < 1:
            raise ValueError(f"job {name!r}: concurrency must be at least 1")
        if on_error not in ON_ERROR:
            raise ValueError(f"job {name!r}: on_error must be 'skip' or 'stop', not {on_error!r}")
        if retry is not None and not isinstance(retry, Retry):
            raise TypeError(f"job {name!r}: retry is a Retry(max_attempts=N), not {retry!r}")
        if timeout is not None and not float(timeout) > 0:
            raise ValueError(f"job {name!r}: timeout must be a positive number of seconds")
        if reduce is not None and not keep_results:
            raise ValueError(
                f"job {name!r}: reduce reads the results the record keeps; "
                "it cannot run with keep_results=False"
            )

        self.graph = graph
        self.items = items
        self.input = input
        self.inputs: Dict[str, Any] = dict(inputs or {})
        self.key = key
        self.output = output
        self.reduce = reduce
        self.concurrency = int(concurrency)
        self.on_error = on_error
        self.retry = retry
        self.timeout = float(timeout) if timeout is not None else None
        self.preflight: List[str] = list(preflight or [])
        self.trace = trace
        self.keep_results = bool(keep_results)
        #: Where runs are recorded (``<record_dir>/<name>/<run>``). ``None``
        #: is the project's: see :func:`default_record_dir`.
        self.record_dir: Optional[Path] = Path(record_dir) if record_dir is not None else None
        self.on_item = on_item
        self._engine: Any = None
        self._reducer: Any = None
        self._doors: Optional[bool] = None

    @staticmethod
    def _check_steps(steps: Any, **given: Any) -> None:
        extra = [k for k, v in given.items() if v is not None]
        if extra:
            raise ValueError(
                f"a job of steps runs other jobs; it takes no {', '.join(extra)} of its own"
            )
        steps = list(steps)
        if not steps:
            raise ValueError("steps=[...] needs at least one job")
        bad = [s for s in steps if not isinstance(s, Job)]
        if bad:
            raise TypeError(f"a step is a Job, not a {type(bad[0]).__name__}")

    # -- the graph ---------------------------------------------------------

    def engine(self) -> Any:
        """The compiled Operon, built once."""
        if self._engine is None:
            if self.steps is not None:
                raise TypeError(f"job {self.name!r} runs steps; it has no graph")
            self._engine = _compile(self.name, "graph", self.graph, self.trace)
        return self._engine

    def reducer(self) -> Any:
        """The compiled ``reduce`` graph, built once (``None`` without one)."""
        if self.reduce is not None and self._reducer is None:
            self._reducer = _compile(self.name, "reduce", self.reduce, self.trace)
            if "results" not in self._reducer.graph.inputs:
                raise TypeError(
                    f"job {self.name!r}: the reduce graph needs a `results` parameter "
                    "(the list of every result)"
                )
        return self._reducer

    def has_doors(self) -> bool:
        """True when the graph reads items through ``ingress`` — the serving
        shape. Checked once, anywhere in the graph."""
        if self._doors is None:
            from operonx.app.serve.ops import ingress

            target = getattr(ingress, "__wrapped__", ingress)

            def walk(g: Any) -> bool:
                for op in (getattr(g, "_ops", None) or {}).values():
                    if getattr(op, "core", None) is target or walk(op):
                        return True
                return False

            self._doors = walk(self.engine().graph)
        return self._doors

    def bind(self, item: Any) -> Dict[str, Any]:
        """The run inputs for one item of a graph without doors."""
        inputs = dict(self.inputs)
        params = list(self.engine().graph.inputs)
        if self.input is not None:
            if self.input not in params:
                raise ValueError(
                    f"input={self.input!r}, but the graph takes {params or 'no parameters'}"
                )
            inputs[self.input] = item
            return inputs
        if isinstance(item, Mapping):
            unknown = [k for k in item if k not in params]
            if unknown:
                raise ValueError(
                    f"the item has {unknown}, which the graph does not take "
                    f'(it takes {params or "nothing"}); pass input="<param>" to hand it '
                    "the whole item"
                )
            inputs.update(item)
            return inputs
        free = [p for p in params if p not in self.inputs]
        if len(free) != 1:
            raise ValueError(
                f"a {type(item).__name__} item needs one graph parameter to go to; "
                f'the graph has {free or "none"} free — pass input="<param>"'
            )
        inputs[free[0]] = item
        return inputs

    # -- identity ----------------------------------------------------------

    def item_of(self, raw: Any) -> Any:
        """What the graph receives for one raw item — the item itself; an
        `Eval` hands over a case's ``input``."""
        return raw

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

    async def run(
        self,
        *,
        resume: bool = False,
        record_dir: Union[str, Path, None] = None,
        on_item: Optional[Callable[[Any], Any]] = None,
    ) -> JobRun:
        """Run the job once and return its record. *record_dir* overrides
        where it is written (for this run, and every step's); *on_item* is
        called with each item's ``ItemResult`` as it is recorded."""
        from .runner import run_job, run_steps

        kept = (self.record_dir, self.on_item)
        if record_dir is not None:
            self.record_dir = Path(record_dir)
        if on_item is not None:
            self.on_item = on_item
        try:
            if self.steps is not None:
                return await run_steps(self, resume=resume, record_dir=record_dir)
            return await run_job(self, resume=resume)
        finally:
            self.record_dir, self.on_item = kept

    def run_sync(self, **kwargs: Any) -> JobRun:
        """`run()` from synchronous code — a script, a cron entry."""
        return asyncio.run(self.run(**kwargs))

    def main(self, argv: Optional[Sequence[str]] = None, *, doc: Optional[str] = None) -> int:
        """This job as a command line; returns the exit status. Takes the
        flags ``operonx run`` has, minus the job name."""
        from operonx.cli.run import main_for

        return main_for(self, argv, doc=doc)

    def records(self) -> Path:
        """Where this job's runs are recorded."""
        return (
            Path(self.record_dir)
            if self.record_dir is not None
            else default_record_dir(self.folder)
        )

    # -- describing --------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        """What the job is, for run.json and ``--list``. No secrets: only
        the names of things."""

        def shown(value: Any) -> Any:
            if value is None or isinstance(value, str):
                return value
            if isinstance(value, Path):
                return str(value)
            if isinstance(value, (list, tuple)):
                return f"{len(value)} items"
            if callable(value) and hasattr(value, "__qualname__"):
                from operonx.app.declare import ref_name

                return ref_name(value)
            return type(value).__name__

        if self.steps is not None:
            return {
                "kind": "steps",
                "steps": [s.name for s in self.steps],
                "description": self.description,
            }
        return {
            "kind": "job",
            "graph": self.graph
            if isinstance(self.graph, str)
            else getattr(self.graph, "name", None)
            or getattr(self.graph, "__name__", type(self.graph).__name__),
            "items": shown(self.items),
            "input": self.input,
            "key": self.key
            if isinstance(self.key, str)
            else (getattr(self.key, "__name__", "fn") if self.key else None),
            "output": shown(self.output),
            "reduce": None
            if self.reduce is None
            else getattr(self.reduce, "name", None) or getattr(self.reduce, "__name__", "reduce"),
            "concurrency": self.concurrency,
            "on_error": self.on_error,
            "retry": self.retry.max_attempts if self.retry is not None else None,
            "timeout": self.timeout,
            "preflight": list(self.preflight) or None,
            "description": self.description,
        }

    def __copy__(self) -> "Job":
        """A shallow copy whose own bound methods (an `Eval`'s ``items``)
        point at the copy, not at the job it was copied from."""
        new = object.__new__(type(self))
        new.__dict__.update(self.__dict__)
        for name, value in self.__dict__.items():
            if inspect.ismethod(value) and value.__self__ is self:
                setattr(new, name, getattr(new, value.__func__.__name__))
        return new

    def __repr__(self) -> str:
        if self.steps is not None:
            return f"Job({self.name!r}, steps={[s.name for s in self.steps]})"
        d = self.describe()
        return f"Job({self.name!r}, graph={d['graph']!r}, items={d['items']!r}, key={d['key']!r})"
