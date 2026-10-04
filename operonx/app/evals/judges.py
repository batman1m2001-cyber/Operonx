"""Judges: evaluators that are operonx graphs, each call its own traced run.

Any ``GraphOp`` or ``@graph`` factory given as an evaluator is a **graph
evaluator**: it takes, by name, any of ``input``, ``output``, ``expected``,
``row``, ``outputs`` and ``trace_summary`` (the case's run as text), and
its run's outputs are the verdict — ``{passed, score?, label?, reason?}``
— as a function's return value is. Inside an eval each call is a run of
its own, traced to the eval's sinks with ``origin=eval``, ``role=judge``
and ``judged_trace`` (the case's run), so "why did the judge say that" is
one trace away and the system's cost never holds the judge's.

:func:`judge` is the graph a model grades one criterion with::

    polite = judge("llm:judge", "judges/polite.md")       # judge:polite, PASS/FAIL
    ev = Eval("replies", graph=bot, dataset="dataset:replies", evaluators=[polite],
              scores="score_store:team")                   # the store is also the cache

    render → LLMOp (reason, then verdict) → decide

A judge is **versioned** — rubric, examples, labels, temperature, the
model its ``llm:`` resource resolves to and the graph's own code — and,
with a score store, **cached** on that version and what it was shown: an
unchanged output is never judged twice. It is **binary** (``PASS`` /
``FAIL``) unless given other ``labels``, checks **one criterion** (one
judge, one label, one score), and keeps the model's reason.
:func:`pairwise` asks a model which of two answers is better, in both
orders at once; :func:`~operonx.app.evals.pairs.compare_pairwise` runs
it over two experiments. :func:`llm_judge` is the 1.9.0 judge, now this
machinery under its old contract.

Whether a judge can be trusted is measured against people: see
:mod:`~operonx.app.evals.align`.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

from operonx.core.ops import op

from .evaluators import verdict_of
from .fingerprint import canonical, config_identity, config_spec, digest, graph_spec, models_of
from .traceview import run_cost

__all__ = [
    "AVAILABLE",
    "GraphEvaluator",
    "Judge",
    "Judging",
    "PairwiseJudge",
    "evaluator_of",
    "judge",
    "llm_judge",
    "pairwise",
]

#: What a graph evaluator can take, by name.
AVAILABLE = ("input", "output", "expected", "row", "outputs", "trace_summary")

#: The labels a binary judge answers with.
BINARY = ("PASS", "FAIL")

#: Rubric files are named by their suffix.
RUBRIC_SUFFIXES = (".md", ".txt")


def _show(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


# ── the context an eval gives its judges ─────────────────────────────────


class _Shared:
    """What every case of one eval run shares: the sinks, the cache, the
    semaphore, one engine per judge graph."""

    def __init__(self, trace: Sequence[Any], cache: Any, concurrency: int):
        self.trace = list(trace)
        self.cache = cache
        self.concurrency = int(concurrency)
        self.engines: Dict[int, Any] = {}
        self._sem: Optional[asyncio.Semaphore] = None

    def semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:  # made in the loop that runs the judges
            self._sem = asyncio.Semaphore(self.concurrency)
        return self._sem


class Judging:
    """What an eval gives its judges for one run — injected as the
    ``judging`` argument: the trace consumers its engine feeds, the
    metadata its judges' runs carry (``job``, ``job_run``, and per case
    ``key``, ``case``, ``judged_trace``), the judge cache (a
    :class:`~operonx.telemetry.scores.ScoreStore`, or ``None``), and a
    semaphore of ``concurrency`` judge runs in flight. A judge called
    without one runs untraced and uncached."""

    def __init__(
        self,
        *,
        trace: Sequence[Any] = (),
        metadata: Optional[Mapping[str, Any]] = None,
        cache: Any = None,
        concurrency: int = 8,
        _shared: Optional[_Shared] = None,
    ):
        if _shared is None and int(concurrency) < 1:
            raise ValueError("judge_concurrency is how many judge runs may be in flight, ≥ 1")
        self.metadata = {k: v for k, v in (metadata or {}).items() if v is not None}
        self._shared = _shared or _Shared(trace, cache, concurrency)

    @property
    def cache(self) -> Any:
        return self._shared.cache

    def for_case(self, **fields: Any) -> "Judging":
        """The same context, for one case: *fields* join the metadata."""
        return Judging(metadata={**self.metadata, **fields}, _shared=self._shared)

    def engine(self, graph: Any) -> Any:
        """The engine that runs *graph*, traced to this eval's sinks."""
        from operonx.core import Operon

        got = self._shared.engines.get(id(graph))
        if got is None:
            got = self._shared.engines[id(graph)] = Operon(graph, trace=self._shared.trace)
        return got

    def limit(self) -> Any:
        return self._shared.semaphore()


# ── graph evaluators ─────────────────────────────────────────────────────


def _is_graph(ev: Any) -> bool:
    from operonx.core.ops.graph.graph_op import GraphOp

    return isinstance(ev, GraphOp) or bool(getattr(ev, "_operonx_graph", False))


def evaluator_of(ev: Any) -> Any:
    """*ev* as an eval runs it: a ``GraphOp`` or ``@graph`` factory becomes
    a :class:`GraphEvaluator`; anything else is returned as it is. A
    :class:`PairwiseJudge` is refused — it judges two experiments, not a
    case."""
    if isinstance(ev, PairwiseJudge):
        raise TypeError(
            f"{ev.eval_name!r} is a pairwise judge: it compares two experiments' answers to "
            "one case, so it is not a case's evaluator. Run it with "
            "compare_pairwise(a, b, [judge]) or `operonx eval compare A B --pairwise mod:attr`"
        )
    if isinstance(ev, GraphEvaluator) or not _is_graph(ev):
        return ev
    return GraphEvaluator(ev)


def cache_key(version: str, inputs: Mapping[str, Any]) -> str:
    """A judgement's identity: the judge's version and what it was shown."""
    return "judge:" + digest({"version": version, "inputs": dict(inputs)})


class GraphEvaluator:
    """A graph that judges a case (see the module docstring).

    Args:
        graph: A ``GraphOp``, or a ``@graph`` factory (built once, its
            parameters as the graph's inputs).
        name: The check's name; default the graph's (the factory's name).
        version: A declared version; default the digest of the graph as
            ``serialize()`` describes it and the resources it resolves.
    """

    eval_kind = "judge"  # asks something that is not deterministic code: never rescored
    #: The inputs it may take.
    accepts: Tuple[str, ...] = AVAILABLE

    def __init__(self, graph: Any, *, name: Optional[str] = None, version: Optional[str] = None):
        from operonx.core.ops.graph.graph_op import GraphOp

        if isinstance(graph, GraphOp):
            built, default = graph, graph.name
        elif getattr(graph, "_operonx_graph", False):
            params = inspect.signature(getattr(graph, "__wrapped__", graph)).parameters
            built, default = graph(**{p: None for p in params}), graph.__name__
        else:
            raise TypeError(
                f"a graph evaluator is a GraphOp or a @graph factory, not a {type(graph).__name__}"
            )
        self.graph = built
        self.eval_name = str(name or default)
        self.takes: Tuple[str, ...] = tuple(built.inputs)
        unknown = [k for k in self.takes if k not in self.accepts]
        if unknown:
            raise ValueError(
                f"graph evaluator {self.eval_name!r} takes {unknown}, which no case provides; "
                f"a graph evaluator's inputs are named after what it judges: "
                f"{', '.join(self.accepts)}"
            )
        self._declared = version
        self._version: Optional[str] = None
        self._engine: Any = None

    # -- identity --------------------------------------------------------------

    @property
    def eval_params(self) -> FrozenSet[str]:
        """The names :func:`~operonx.app.evals.evaluators.prepare` passes."""
        return frozenset(self.takes) | {"judging"}

    @property
    def eval_version(self) -> str:
        if self._declared is not None:
            return str(self._declared)
        if self._version is None:
            self._version = self._identify()
        return self._version

    def _identify(self) -> str:
        try:
            spec = self.graph.serialize()
        except NotImplementedError as exc:
            return digest({"graph": self.graph.name, "unserializable": str(exc)})
        return digest({"graph": graph_spec(spec), "config": config_spec(spec)})

    def models(self) -> List[str]:
        """The models the graph's ``llm:`` resources resolve to."""
        try:
            return models_of(config_spec(self.graph.serialize()))
        except NotImplementedError:
            return []

    def model_resolved(self) -> bool:
        return True

    # -- judging ---------------------------------------------------------------

    def inputs_of(self, avail: Mapping[str, Any]) -> Dict[str, Any]:
        """The graph's inputs from what the case offers."""
        return {k: avail.get(k) for k in self.takes}

    def refuse(self, inputs: Mapping[str, Any]) -> Optional[str]:
        """Why this case cannot be judged at all (nothing is run), or ``None``."""
        return None

    def verdict_from(self, out: Mapping[str, Any]) -> Dict[str, Any]:
        """The run's outputs as a verdict."""
        return verdict_of({k: v for k, v in out.items() if not str(k).startswith("$")})

    async def __call__(self, judging: Optional[Judging] = None, **avail: Any) -> Dict[str, Any]:
        inputs = self.inputs_of(avail)
        why = self.refuse(inputs)
        if why is not None:
            return {"passed": False, "error": why}
        return await run_judge(self, inputs, judging)

    def _untraced(self) -> Any:
        from operonx.core import Operon

        if self._engine is None:
            self._engine = Operon(self.graph, trace=[])
        return self._engine

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.eval_name!r})"


async def run_judge(
    ev: GraphEvaluator, inputs: Mapping[str, Any], judging: Optional[Judging]
) -> Dict[str, Any]:
    """One judgement: from the cache when it holds this version's verdict
    on these inputs (no run, no call), else one traced run of the graph."""
    from operonx.app.origin import origin_metadata

    cache = judging.cache if judging is not None and getattr(ev, "cacheable", True) else None
    key = cache_key(ev.eval_version, inputs) if cache is not None else None
    if key is not None:
        hit = await asyncio.to_thread(cache.cache_get, key)
        if hit is not None:
            return {**hit, "cached": True, "cost_usd": 0.0}

    engine = judging.engine(ev.graph) if judging is not None else ev._untraced()
    gate = judging.limit() if judging is not None else contextlib.nullcontext()
    async with gate:
        handle = engine.start(inputs=dict(inputs))
        if judging is not None:
            handle.trace.tag(
                origin_metadata("eval", **judging.metadata, role="judge", evaluator=ev.eval_name)
            )
        out = await handle.collect(unwrap=True)
    errors = out.get("$errors") or {}
    if errors:
        where, text = next(iter(errors.items()))
        lines = str(text).strip().splitlines()
        verdict: Dict[str, Any] = {
            "passed": False,
            "error": f"{str(where).rsplit('.', 1)[-1]}: {lines[-1] if lines else 'error'}",
        }
    else:
        verdict = ev.verdict_from(out)
    verdict["judge_trace_id"] = handle.trace.trace_id
    spent = run_cost(handle.trace)
    if spent:
        verdict.update(spent)
    if key is not None and not verdict.get("error"):
        await asyncio.to_thread(cache.cache_put, key, verdict)
    return verdict


# ── judge(): one criterion, graded by a model ────────────────────────────

_FORMAT = (
    "Think about the criterion first, then answer in exactly this form:\n"
    "<reason>one or two sentences on why</reason>\n"
    "<verdict>{labels}</verdict>\n"
    "The verdict is exactly one of: {listed}."
)


def _examples_text(examples: Sequence[Mapping[str, Any]]) -> str:
    out = []
    for i, ex in enumerate(examples, 1):
        part = [f"Example {i}", f"Input:\n{_show(ex.get('input'))}"]
        part.append(f"Output:\n{_show(ex.get('output'))}")
        if ex.get("expected") is not None:
            part.append(f"Expected:\n{_show(ex.get('expected'))}")
        if ex.get("reason"):
            part.append(f"<reason>{ex['reason']}</reason>")
        part.append(f"<verdict>{ex['verdict']}</verdict>")
        out.append("\n".join(part))
    return "\n\n".join(out)


@op(bound="sync")
def judge_prompt(
    case_input: Any = None,
    case_output: Any = None,
    case_expected: Any = None,
    trace_summary: Any = None,
    rubric: str = "",
    examples: list = None,
    labels: list = None,
) -> dict:
    """The messages a judge model reads: the criterion, the answer format,
    the few-shot examples; then the case."""
    allowed = list(labels or BINARY)
    system = [
        "You grade one output against one criterion.",
        f"Criterion:\n{rubric}",
        _FORMAT.format(labels="|".join(allowed), listed=", ".join(allowed)),
    ]
    if examples:
        system.append("Graded examples:\n\n" + _examples_text(examples))
    user = [f"Input:\n{_show(case_input)}", f"Output:\n{_show(case_output)}"]
    if case_expected is not None:
        user.append(f"Expected (a reference answer):\n{_show(case_expected)}")
    if trace_summary:
        user.append(f"Trace of the run that produced the output:\n{trace_summary}")
    return {
        "messages": [
            {"role": "system", "content": "\n\n".join(system)},
            {"role": "user", "content": "\n\n".join(user)},
        ]
    }


@op(bound="sync")
def decide(
    verdict: Any = None, reason: Any = None, error: Any = None, pass_labels: list = None
) -> dict:
    """The model's label as a verdict: passed when it is a passing label."""
    if error or verdict is None:
        return {
            "passed": False,
            "label": None,
            "reason": reason,
            "error": f"the judge's answer did not parse: {error or 'no verdict'}",
        }
    label = str(verdict).strip()
    return {"passed": label in (pass_labels or ()), "label": label, "reason": reason, "error": None}


def _rubric(rubric: Union[str, Path]) -> Tuple[str, Optional[str]]:
    """The rubric's text, and the file's stem when it is a file."""
    path = Path(rubric) if isinstance(rubric, Path) else None
    if path is None and isinstance(rubric, str) and rubric.strip().endswith(RUBRIC_SUFFIXES):
        path = Path(rubric.strip())
    if path is None:
        return str(rubric), None
    if not path.is_file():
        raise ValueError(
            f"judge: no rubric file at {path.resolve()} (a rubric ending in "
            f"{' or '.join(RUBRIC_SUFFIXES)} is read as a file; write the criterion inline otherwise)"
        )
    return path.read_text(encoding="utf-8").strip(), path.stem


def resolved_model(resource: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """The ``llm:`` resource's config as the hub declares it (secrets
    dropped) and its model — ``(None, None)`` when no hub is installed or
    it does not declare the key."""
    from operonx.core.registry import ResourceHub

    try:
        config = ResourceHub.instance().get_config(f"llm:{resource}")
    except (RuntimeError, KeyError):
        return None, None
    dumped = config.model_dump(mode="json") if hasattr(config, "model_dump") else dict(config)
    ident = config_identity(dumped)
    model = ident.get("model") if isinstance(ident, Mapping) else None
    return ident, model if isinstance(model, str) else None


def _resource_key(llm: str) -> str:
    return llm.partition(":")[2] if llm.startswith("llm:") else llm


class Judge(GraphEvaluator):
    """A model grading one criterion (see :func:`judge`)."""

    def __init__(
        self,
        llm: str,
        rubric: Union[str, Path],
        *,
        name: Optional[str] = None,
        labels: Sequence[str] = BINARY,
        pass_labels: Sequence[str] = ("PASS",),
        reference: Union[bool, str] = "auto",
        examples: Sequence[Mapping[str, Any]] = (),
        include_trace: bool = False,
        temperature: float = 0.0,
        max_retries: int = 1,
        check_name: Optional[str] = None,
    ):
        text, stem = _rubric(rubric)
        name = name or stem
        if not name:
            raise ValueError(
                "judge: an inline rubric needs name= (the criterion it checks, e.g. "
                "name='polite' → the check 'judge:polite'); a rubric file is named by its stem"
            )
        labels = tuple(str(x) for x in labels)
        pass_labels = tuple(str(x) for x in pass_labels)
        if len(labels) < 2 or len(set(labels)) != len(labels):
            raise ValueError(
                f"judge {name!r}: labels are two or more distinct answers, not {labels}"
            )
        if not pass_labels or not set(pass_labels) <= set(labels):
            raise ValueError(
                f"judge {name!r}: pass_labels {pass_labels} must be some of the labels {labels}"
            )
        if reference not in (True, False, "auto"):
            raise ValueError(f"judge {name!r}: reference is True, False or 'auto'")
        for ex in examples:
            self._check_example(ex, name, labels)
        self.resource = _resource_key(str(llm))
        self.rubric = text
        self.labels, self.pass_labels = labels, pass_labels
        self.reference = reference
        self.examples = [dict(ex) for ex in examples]
        self.include_trace = bool(include_trace)
        self.temperature = float(temperature)
        self.max_retries = int(max_retries)
        super().__init__(self._build(), name=check_name or f"judge:{name}")

    def _check_example(self, ex: Any, name: str, labels: Sequence[str]) -> None:
        if not isinstance(ex, Mapping) or str(ex.get("verdict")) not in labels:
            raise ValueError(
                f"judge {name!r}: each example is {{input, output, expected?, verdict, "
                f"reason?}} with a verdict among {tuple(labels)}; got {ex!r}"
            )

    # -- the graph ---------------------------------------------------------------

    def _build(self) -> Any:
        from operonx.core import END, PARENT, START
        from operonx.core.ops.graph.graph_op import GraphOp
        from operonx.providers.ops import LLMOp

        with GraphOp(name="judge") as g:
            prompt = judge_prompt(
                case_input=PARENT["input"],
                case_output=PARENT["output"],
                case_expected=PARENT["expected"] if self.reference is not False else None,
                trace_summary=PARENT["trace_summary"] if self.include_trace else None,
                rubric=self.rubric,
                examples=self.examples or None,
                labels=list(self.labels),
                name="render",
            )
            llm = LLMOp.of(
                resource=self.resource,
                messages=prompt["messages"],
                fields=["reason: str", "verdict: str"],
                parser="xml",
                validators={"verdict": list(self.labels)},
                max_retries=self.max_retries,
                on_failure="error",
                temperature=self.temperature,
                name="grade",
            )
            verdict = decide(
                verdict=llm["verdict"],
                reason=llm["reason"],
                error=llm["error"],
                pass_labels=list(self.pass_labels),
                name="decide",
            )
            START >> prompt >> llm >> verdict >> END
        return g

    # -- identity ----------------------------------------------------------------

    def _identify(self) -> str:
        config, _ = resolved_model(self.resource)
        return digest(
            {
                "judge": type(self).__name__,
                "rubric": self.rubric,
                "examples": self.examples,
                "labels": self.labels,
                "pass_labels": self.pass_labels,
                "reference": self.reference,
                "include_trace": self.include_trace,
                "temperature": self.temperature,
                "model": config if config is not None else {"resource": self.resource},
                # the graph is a pure function of this code and the values above
                "graph": canonical([type(self)._build, judge_prompt, decide, self._extra_code()]),
            }
        )

    def _extra_code(self) -> List[Any]:
        return []

    def models(self) -> List[str]:
        _, model = resolved_model(self.resource)
        return [model] if model else []

    def model_resolved(self) -> bool:
        return resolved_model(self.resource)[0] is not None

    # -- judging -----------------------------------------------------------------

    def refuse(self, inputs: Mapping[str, Any]) -> Optional[str]:
        if self.reference is True and inputs.get("expected") is None:
            return (
                f"{self.eval_name}: reference=True and this case has no expected value to "
                "compare with"
            )
        return None

    def verdict_from(self, out: Mapping[str, Any]) -> Dict[str, Any]:
        verdict = {"passed": bool(out.get("passed")), "label": out.get("label")}
        if out.get("reason"):
            verdict["reason"] = out["reason"]
        if out.get("error"):
            verdict["error"] = out["error"]
        return verdict


def judge(
    llm: str,
    rubric: Union[str, Path],
    *,
    name: Optional[str] = None,
    labels: Sequence[str] = BINARY,
    pass_labels: Sequence[str] = ("PASS",),
    reference: Union[bool, str] = "auto",
    examples: Sequence[Mapping[str, Any]] = (),
    include_trace: bool = False,
    temperature: float = 0.0,
    max_retries: int = 1,
) -> Judge:
    """A model grades each case against one criterion: ``PASS`` or ``FAIL``
    (or one of ``labels``; it passes on ``pass_labels``), with its reason.

    Args:
        llm: The ``llm:`` resource the judge runs on (``"llm:judge"``).
        rubric: The criterion: text, or a ``.md``/``.txt`` file (read now).
        name: The check is ``judge:<name>``; default the rubric file's stem.
        reference: ``"auto"`` shows the case's ``expected`` when it has
            one; ``True`` requires one; ``False`` never shows it.
        examples: Graded examples — ``{input, output, expected?, verdict,
            reason?}`` — shown before the case.
        include_trace: Show the case's run (``trace_summary``): a judge of
            how the answer was reached.
        temperature: Sampling temperature (0: the same answer each time).
        max_retries: Asks again when the answer is not one of the labels.
    """
    return Judge(
        llm,
        rubric,
        name=name,
        labels=labels,
        pass_labels=pass_labels,
        reference=reference,
        examples=examples,
        include_trace=include_trace,
        temperature=temperature,
        max_retries=max_retries,
    )


# ── llm_judge(): the 1.9.0 judge, on the same machinery ──────────────────


@op(bound="sync")
def json_judge_prompt(
    case_input: Any = None, case_output: Any = None, case_expected: Any = None, rubric: str = ""
) -> dict:
    """1.9.0's prompt: the rubric, then the case."""
    system = (
        rubric + "\n\nAnswer with JSON only: "
        '{"passed": true|false, "score": 0.0-1.0, "reason": "one sentence"}'
    )
    user = f"Input:\n{_show(case_input)}\n\nOutput:\n{_show(case_output)}\n\nExpected:\n" + (
        _show(case_expected) if case_expected is not None else "(none given)"
    )
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}


class _JsonJudge(Judge):
    """:func:`llm_judge`: 1.9.0's JSON answer ``{passed, score, reason}``."""

    def _build(self) -> Any:
        from operonx.core import END, PARENT, START
        from operonx.core.ops.graph.graph_op import GraphOp
        from operonx.providers.ops import LLMOp

        with GraphOp(name="llm_judge") as g:
            prompt = json_judge_prompt(
                case_input=PARENT["input"],
                case_output=PARENT["output"],
                case_expected=PARENT["expected"],
                rubric=self.rubric,
                name="render",
            )
            llm = LLMOp.of(
                resource=self.resource,
                messages=prompt["messages"],
                fields=["passed: bool", "score: float", "reason: str"],
                parser="json",
                name="grade",
            )
            START >> prompt >> llm >> END
        return g

    def _extra_code(self) -> List[Any]:
        return [json_judge_prompt]

    def verdict_from(self, out: Mapping[str, Any]) -> Dict[str, Any]:
        if out.get("error"):
            return {"passed": False, "error": str(out["error"])}
        return {
            "passed": bool(out.get("passed")),
            "score": out.get("score"),
            "reason": out.get("reason"),
        }


def llm_judge(resource: str, rubric: str, *, name: str = "llm_judge") -> Judge:
    """An LLM grades the case against *rubric*: ``{passed, score, reason}``
    parsed from its JSON answer, with the call's cost and usage kept on
    the verdict. *resource* is an ``llm:`` key (or a bare name) the hub
    resolves. The 1.9.0 judge, now a traced, versioned, cached
    :class:`Judge`; :func:`judge` (PASS/FAIL with a reason) is the one
    to reach for."""
    return _JsonJudge(resource, rubric, name=name, check_name=name)


# ── pairwise(): which of two answers is better ───────────────────────────

#: A pairwise judge's labels, in the order it was shown the answers.
PAIR_LABELS = ("A", "B", "TIE")


@op(bound="sync")
def pairwise_prompt(
    case_input: Any = None,
    first: Any = None,
    second: Any = None,
    case_expected: Any = None,
    rubric: str = "",
    examples: list = None,
) -> dict:
    """The messages for one order: answer A is *first*, B *second*."""
    system = [
        "You compare two outputs for the same input against one criterion.",
        f"Criterion:\n{rubric}",
        _FORMAT.format(
            labels="A|B|TIE",
            listed="A (the first is better), B (the second is better), TIE (neither is)",
        ),
        "Judge the content, not the order or the length.",
    ]
    if examples:
        system.append("Graded examples:\n\n" + _pair_examples_text(examples))
    user = [f"Input:\n{_show(case_input)}"]
    if case_expected is not None:
        user.append(f"Expected (a reference answer):\n{_show(case_expected)}")
    user.append(f"[Answer A]\n{_show(first)}")
    user.append(f"[Answer B]\n{_show(second)}")
    return {
        "messages": [
            {"role": "system", "content": "\n\n".join(system)},
            {"role": "user", "content": "\n\n".join(user)},
        ]
    }


def _pair_examples_text(examples: Sequence[Mapping[str, Any]]) -> str:
    out = []
    for i, ex in enumerate(examples, 1):
        part = [
            f"Example {i}",
            f"Input:\n{_show(ex.get('input'))}",
            f"[Answer A]\n{_show(ex.get('a'))}",
            f"[Answer B]\n{_show(ex.get('b'))}",
        ]
        if ex.get("reason"):
            part.append(f"<reason>{ex['reason']}</reason>")
        part.append(f"<verdict>{ex['verdict']}</verdict>")
        out.append("\n".join(part))
    return "\n\n".join(out)


def _winner(label: Any, swapped: bool) -> Optional[str]:
    """One order's answer as the experiment it names: ``a``, ``b``, ``tie``."""
    text = str(label).strip().upper() if label is not None else ""
    if text == "TIE":
        return "tie"
    if text not in ("A", "B"):
        return None
    first = "b" if swapped else "a"
    return first if text == "A" else ("a" if first == "b" else "b")


@op(bound="sync")
def reconcile(
    ab: Any = None,
    ba: Any = None,
    ab_reason: Any = None,
    ba_reason: Any = None,
    ab_error: Any = None,
    ba_error: Any = None,
) -> dict:
    """Both orders as one verdict: the winner they agree on, else a tie
    marked ``inconsistent`` (the preference followed the position)."""
    first, second = _winner(ab, False), _winner(ba, True)
    if ab_error or ba_error or first is None or second is None:
        return {
            "winner": None,
            "inconsistent": None,
            "ab": first,
            "ba": second,
            "reason": None,
            "error": f"the judge's answer did not parse: {ab_error or ba_error or 'no verdict'}",
        }
    agree = first == second
    return {
        "winner": first if agree else "tie",
        "inconsistent": not agree,
        "ab": first,
        "ba": second,
        "reason": f"A/B order: {ab_reason} | B/A order: {ba_reason}",
        "error": None,
    }


class PairwiseJudge(Judge):
    """A model choosing between two answers, asked in both orders as two
    parallel branches of one run (see :func:`pairwise`)."""

    accepts = ("input", "a", "b", "expected")

    def __init__(
        self,
        llm: str,
        rubric: Union[str, Path],
        *,
        name: Optional[str] = None,
        reference: Union[bool, str] = "auto",
        examples: Sequence[Mapping[str, Any]] = (),
        temperature: float = 0.0,
        max_retries: int = 1,
    ):
        text, stem = _rubric(rubric)
        if not (name or stem):
            raise ValueError(
                "pairwise: an inline rubric needs name= (the check is 'pairwise:<name>'); "
                "a rubric file is named by its stem"
            )
        super().__init__(
            llm,
            text,
            name=name or stem,
            labels=PAIR_LABELS,
            pass_labels=("A",),
            reference=reference,
            examples=examples,
            temperature=temperature,
            max_retries=max_retries,
            check_name=f"pairwise:{name or stem}",
        )

    def _check_example(self, ex: Any, name: str, labels: Sequence[str]) -> None:
        if not isinstance(ex, Mapping) or str(ex.get("verdict")) not in PAIR_LABELS:
            raise ValueError(
                f"pairwise {name!r}: each example is {{input, a, b, verdict, reason?}} with a "
                f"verdict among {PAIR_LABELS}; got {ex!r}"
            )

    def _build(self) -> Any:
        from operonx.core import END, PARENT, START
        from operonx.core.ops.graph.graph_op import GraphOp
        from operonx.providers.ops import LLMOp

        def order(first: str, second: str, tag: str) -> Tuple[Any, Any]:
            prompt = pairwise_prompt(
                case_input=PARENT["input"],
                first=PARENT[first],
                second=PARENT[second],
                case_expected=PARENT["expected"] if self.reference is not False else None,
                rubric=self.rubric,
                examples=self.examples or None,
                name=f"render_{tag}",
            )
            llm = LLMOp.of(
                resource=self.resource,
                messages=prompt["messages"],
                fields=["reason: str", "verdict: str"],
                parser="xml",
                validators={"verdict": list(PAIR_LABELS)},
                max_retries=self.max_retries,
                on_failure="error",
                temperature=self.temperature,
                name=f"grade_{tag}",
            )
            return prompt, llm

        with GraphOp(name="pairwise") as g:
            p_ab, ab = order("a", "b", "ab")
            p_ba, ba = order("b", "a", "ba")
            joined = reconcile(
                ab=ab["verdict"],
                ba=ba["verdict"],
                ab_reason=ab["reason"],
                ba_reason=ba["reason"],
                ab_error=ab["error"],
                ba_error=ba["error"],
                name="reconcile",
            )
            START >> [p_ab, p_ba]  # the two orders are two branches: asked at once
            p_ab >> ab >> joined
            p_ba >> ba >> joined
            joined >> END
        return g

    def _extra_code(self) -> List[Any]:
        return [pairwise_prompt, reconcile, _winner, _pair_examples_text]

    def refuse(self, inputs: Mapping[str, Any]) -> Optional[str]:
        if self.reference is True and inputs.get("expected") is None:
            return f"{self.eval_name}: reference=True and this case has no expected value"
        return None

    def verdict_from(self, out: Mapping[str, Any]) -> Dict[str, Any]:
        if out.get("error"):
            return {
                "passed": False,
                "error": out["error"],
                "ab": out.get("ab"),
                "ba": out.get("ba"),
            }
        return {
            "winner": out.get("winner"),
            "inconsistent": bool(out.get("inconsistent")),
            "ab": out.get("ab"),
            "ba": out.get("ba"),
            "reason": out.get("reason"),
        }

    async def compare(
        self,
        *,
        input: Any,  # noqa: A002 — named as every evaluator names it
        a: Any,
        b: Any,
        expected: Any = None,
        judging: Optional[Judging] = None,
    ) -> Dict[str, Any]:
        """One case: which of *a* and *b* (two experiments' outputs) is better."""
        inputs = {"input": input, "a": a, "b": b, "expected": expected}
        inputs = {k: v for k, v in inputs.items() if k in self.takes}
        why = self.refuse(inputs)
        if why is not None:
            return {"error": why}
        return await run_judge(self, inputs, judging)


def pairwise(
    llm: str,
    rubric: Union[str, Path],
    *,
    name: Optional[str] = None,
    reference: Union[bool, str] = "auto",
    examples: Sequence[Mapping[str, Any]] = (),
    temperature: float = 0.0,
    max_retries: int = 1,
) -> PairwiseJudge:
    """A model picks the better of two answers to one case — ``A``, ``B``
    or ``TIE`` — asked twice, once in each order, as two branches of one
    run. The orders agree: that answer wins. They differ: a ``tie``
    marked ``inconsistent`` (the choice followed the position). Run it
    over two experiments with
    :func:`~operonx.app.evals.pairs.compare_pairwise`; the score is
    ``pairwise:<name>``. *examples*: ``{input, a, b, verdict, reason?}``."""
    return PairwiseJudge(
        llm,
        rubric,
        name=name,
        reference=reference,
        examples=examples,
        temperature=temperature,
        max_retries=max_retries,
    )
